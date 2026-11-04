"""SIMULATOR Finance API Wiraky untuk demo dan UAT lokal. BUKAN sistem Finance asli.

Meniru kontrak Standar API Integrated:
- menerima pengajuan (verifikasi HMAC + timestamp, Idempotency-Key) -> HTTP 202 RECEIVED
- invoice dibuat internal; "pembayaran" dipicu manual dari halaman simulator
- mengirim callback PAYMENT_CONFIRMED bertanda tangan HMAC (secret berbeda dari arah kirim)
- melayani GET status untuk rekonsiliasi
- punya aksi skenario negatif (nominal salah, duplikat, replay, signature palsu, callback hilang)
"""
import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Mapping, Optional, Tuple

from core.payload import PayloadError, canonical_body, validate_outbound, wib_iso
from core.security import compute_signature, is_fresh, utc_timestamp, verify_signature
from services.finance_client import TransportError, requests_transport

class FinanceSimulator:
    def __init__(self, settings, *, now: Optional[Callable[[], datetime]] = None, callback_transport=None,
                 schedule: Optional[Callable[[float, Callable[[], None]], None]] = None):
        self.settings = settings
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.callback_transport = callback_transport or requests_transport
        if schedule is None and settings.simulator_auto_pending:
            schedule = self._thread_schedule
        self._schedule = schedule
        self._lock = threading.RLock()
        self._invoices: Dict[str, Dict[int, dict]] = {}
        self._idempotency: Dict[str, dict] = {}
        self._seq = {"fin": 0, "pay": 0}

    @staticmethod
    def _thread_schedule(delay, fn):
        timer = threading.Timer(delay, fn)
        timer.daemon = True
        timer.start()

    # ------------------------------------------------------------------ autentikasi
    def _authenticate(self, headers: Mapping[str, str], body: bytes) -> Optional[Tuple[int, dict]]:
        timestamp = headers.get("X-Timestamp", "")
        signature = headers.get("X-Signature", "")
        if not is_fresh(timestamp, self.now(), self.settings.timestamp_max_age):
            return 401, {"error": "INVALID_TIMESTAMP"}
        if not verify_signature([self.settings.finance_api_secret], timestamp, body, signature):
            return 401, {"error": "INVALID_SIGNATURE"}
        return None

    # ------------------------------------------------------------------ POST additional-costs
    def handle_submission(self, headers: Mapping[str, str], body: bytes) -> Tuple[int, dict]:
        denied = self._authenticate(headers, body)
        if denied:
            return denied
        try:
            payload = json.loads(body)
        except ValueError:
            return 400, {"error": "INVALID_JSON"}
        if not isinstance(payload, dict):
            return 400, {"error": "INVALID_JSON"}
        try:
            validate_outbound(payload)
        except PayloadError as exc:
            return 422, {"error": "INVALID_PAYLOAD", "details": exc.errors}

        event_id = payload["event_id"]
        if headers.get("Idempotency-Key") != event_id or headers.get("X-Event-ID", event_id) != event_id:
            return 422, {"error": "IDEMPOTENCY_KEY_MISMATCH"}

        digest = hashlib.sha256(body).hexdigest()
        ac_id, revision = payload["additional_cost_id"], payload["revision"]
        with self._lock:
            prior = self._idempotency.get(event_id)
            if prior:
                if prior["hash"] != digest:
                    return 409, {"error": "IDEMPOTENCY_CONFLICT"}
                return prior["status"], prior["response"]     # finance_reference LAMA, tanpa invoice baru

            revisions = self._invoices.setdefault(ac_id, {})
            if revisions and revision <= max(revisions):
                return 409, {"error": "REVISION_CONFLICT"}
            if any(inv["status"] == "PAID" for inv in revisions.values()):
                return 409, {"error": "INVOICE_ALREADY_PAID"}
            for older in revisions.values():          # revisi baru membatalkan tagihan sebelumnya
                if older["status"] in ("PENDING", "FAILED", "EXPIRED"):
                    older["status"] = "CANCELLED"

            self._seq["fin"] += 1
            year = self.now().astimezone(timezone(timedelta(hours=7))).year
            invoice = {
                "additional_cost_id": ac_id, "order_id": payload["order_id"], "revision": revision,
                "finance_reference": f"FIN-{year}-{self._seq['fin']:04d}",
                "invoice_id": f"INV-{ac_id}" if revision == 1 else f"INV-{ac_id}-R{revision:02d}",
                "total_amount": payload["total_amount"], "currency": payload["currency"],
                "customer": payload["customer"], "status": "PENDING",
                "created_at": self.now(), "last_event": None, "deliveries": [],
            }
            revisions[revision] = invoice
            response = {"status": "RECEIVED", "order_id": payload["order_id"], "additional_cost_id": ac_id,
                        "finance_reference": invoice["finance_reference"],
                        "message": "Pengajuan diterima untuk diproses"}
            self._idempotency[event_id] = {"hash": digest, "status": 202, "response": response}

        if self._schedule:
            self._schedule(1.0, lambda: self._safe_emit(ac_id, "PENDING"))
        return 202, response

    def _safe_emit(self, ac_id, status):
        try:
            self.emit(ac_id, status)
        except Exception:      # timer thread tidak boleh melempar
            pass

    # ------------------------------------------------------------------ GET status
    def handle_status(self, headers: Mapping[str, str], additional_cost_id: str,
                      revision: Optional[int] = None) -> Tuple[int, dict]:
        denied = self._authenticate(headers, b"")
        if denied:
            return denied
        with self._lock:
            invoice = self._find(additional_cost_id, revision)
            if invoice is None:
                return 404, {"error": "NOT_FOUND"}
            event = invoice["last_event"] or self._build_event(invoice, "PENDING", stable_id=True)
            return 200, dict(event)

    # ------------------------------------------------------------------ pembentukan event
    def _find(self, additional_cost_id, revision=None):
        revisions = self._invoices.get(additional_cost_id)
        if not revisions:
            return None
        return revisions.get(revision if revision is not None else max(revisions))

    def _build_event(self, invoice, payment_status, *, amount=None, stable_id=False) -> dict:
        year = self.now().astimezone(timezone(timedelta(hours=7))).year
        if stable_id:
            event_id = f"EVT-STS-{invoice['additional_cost_id']}-R{invoice['revision']:02d}-{payment_status}"
        else:
            self._seq["pay"] += 1
            event_id = f"EVT-PAY-{year}-{self._seq['pay']:04d}"
        event = {
            "schema_version": "1.0", "event_id": event_id, "event_type": "PAYMENT_CONFIRMED",
            "order_id": invoice["order_id"], "additional_cost_id": invoice["additional_cost_id"],
            "revision": invoice["revision"], "invoice_id": invoice["invoice_id"],
            "currency": "IDR", "payment_status": payment_status,
        }
        if payment_status == "PAID":
            event.update(payment_reference=f"TRX-SIM-{self._seq['pay']:04d}",
                         amount_paid=invoice["total_amount"] if amount is None else amount,
                         paid_at=wib_iso(self.now()), verified_by=self.settings.expected_verifier)
        else:
            event["amount_paid"] = 0
        return event

    # ------------------------------------------------------------------ callback ke Green Energy
    def deliver(self, event: dict, *, timestamp: Optional[str] = None, signature: Optional[str] = None,
                secret: Optional[str] = None) -> dict:
        body = canonical_body(event)
        ts = timestamp or utc_timestamp(self.now())
        sig = signature or compute_signature(secret or self.settings.callback_secret, ts, body)
        headers = {"Content-Type": "application/json", "X-Timestamp": ts, "X-Signature": sig,
                   "X-Event-ID": event["event_id"], "Idempotency-Key": event["event_id"]}
        record = {"at": self.now(), "event_id": event["event_id"], "payment_status": event["payment_status"]}
        try:
            status, raw = self.callback_transport("POST", self.settings.green_energy_callback_url, headers, body,
                                                  self.settings.request_timeout)
            try:
                parsed = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, UnicodeDecodeError):
                parsed = {}
            record.update(http_status=status, response=parsed.get("result") or parsed.get("error") or "")
        except TransportError as exc:
            record.update(http_status=None, response=f"TRANSPORT: {exc}")
        with self._lock:
            invoice = self._find(event["additional_cost_id"], event["revision"])
            if invoice is not None:
                invoice["deliveries"].append(record)
        return record

    def emit(self, additional_cost_id: str, payment_status: str, *, amount=None, deliver: bool = True,
             revision: Optional[int] = None) -> dict:
        """Ubah status invoice di sisi Finance, lalu (opsional) kirim callback."""
        with self._lock:
            invoice = self._find(additional_cost_id, revision)
            if invoice is None:
                raise KeyError(additional_cost_id)
            if invoice["status"] == "CANCELLED":
                raise ValueError("Invoice sudah dibatalkan oleh revisi baru")
            if payment_status == "PENDING" and invoice["status"] != "PENDING":
                return {"event": None, "delivery": None}   # PENDING terlambat: jangan timpa status final
            event = self._build_event(invoice, payment_status, amount=amount)
            if payment_status != "PENDING":
                invoice["status"] = payment_status
            invoice["last_event"] = event
        delivery = self.deliver(event) if deliver else None
        return {"event": event, "delivery": delivery}

    # ------------------------------------------------------------------ aksi untuk halaman simulator
    ACTIONS = ("pay", "pay_wrong", "pay_silent", "expire", "fail", "refund",
               "resend", "bad_signature", "replay_old")

    def action(self, name: str, additional_cost_id: str) -> dict:
        if name not in self.ACTIONS:
            raise ValueError(f"aksi tidak dikenal: {name}")
        with self._lock:
            invoice = self._find(additional_cost_id)
            if invoice is None:
                raise KeyError(additional_cost_id)
            total, last = invoice["total_amount"], invoice["last_event"]

        if name == "pay":
            return self.emit(additional_cost_id, "PAID")
        if name == "pay_wrong":
            return self.emit(additional_cost_id, "PAID", amount=max(total - 1000, 1))
        if name == "pay_silent":
            return self.emit(additional_cost_id, "PAID", deliver=False)      # callback "hilang"
        if name == "expire":
            return self.emit(additional_cost_id, "EXPIRED")
        if name == "fail":
            return self.emit(additional_cost_id, "FAILED")
        if name == "refund":
            return self.emit(additional_cost_id, "REFUNDED")
        if last is None:
            raise ValueError("Belum ada event untuk dikirim ulang")
        if name == "resend":              # event_id SAMA -> Green Energy harus menjawab DUPLICATE
            return {"event": last, "delivery": self.deliver(last)}
        if name == "bad_signature":       # signature palsu -> 401 INVALID_SIGNATURE
            return {"event": last, "delivery": self.deliver(last, signature="0" * 64)}
        # replay_old: signature VALID tapi timestamp 10 menit lalu -> 401 INVALID_TIMESTAMP
        old_ts = utc_timestamp(self.now() - timedelta(minutes=10))
        return {"event": last, "delivery": self.deliver(last, timestamp=old_ts)}

    def list_invoices(self) -> list:
        with self._lock:
            rows = [dict(inv, deliveries=list(inv["deliveries"]))
                    for revisions in self._invoices.values() for inv in revisions.values()]
        return sorted(rows, key=lambda r: (r["created_at"], r["revision"]), reverse=True)
