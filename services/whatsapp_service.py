"""Notifikasi WhatsApp Business (Cloud API) ke pelanggan.

Prinsip:
  * Notifikasi bersifat INFORMATIF. Kegagalan WhatsApp TIDAK PERNAH mengubah status tagihan/pembayaran;
    PAID tetap hanya berasal dari callback Finance yang terautentikasi (Standar API bagian 08).
  * Pesan dikirim sebagai TEMPLATE (kategori UTILITY) karena pelanggan biasanya di luar jendela 24 jam.
    Template harus dibuat & disetujui Meta lebih dulu (lihat docs/WA_BUSINESS_SETUP.md).
  * Pesan TIDAK memuat tautan pembayaran: instruksi bayar hanya lewat kanal resmi Finance Wiraky
    (mencegah pelanggan terbiasa mengklik tautan, yang memudahkan phishing).
  * Pemicu didasarkan pada status tagihan (scan berkala), bukan dipanggil dari logika pembayaran,
    sehingga transaksi pembayaran tidak terpengaruh dan pengiriman bisa diulang aman.
  * Satu pesan per (additional_cost_id, revisi, jenis): idempotent, tidak ada pesan ganda.
  * Mode LOG (default) tidak mengirim apa pun; pesan hanya dicatat dan tampil di halaman /whatsapp.
"""
import json
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from core import audit_logger
from core.payload import parse_iso8601
from core.status import PAID, PAYMENT_PENDING, RECEIVED

log = logging.getLogger("wiraky.whatsapp")

BILL_ISSUED = "BILL_ISSUED"
PAYMENT_CONFIRMED = "PAYMENT_CONFIRMED"

# Isi template yang HARUS didaftarkan di Meta (nama template dari settings). {{n}} = urutan variabel.
TEMPLATE_BODIES = {
    BILL_ISSUED: ("Halo {{1}}, pemasangan Green Energy untuk pesanan {{2}} memerlukan biaya tambahan "
                  "sebesar {{3}} (ref. {{4}}) yang telah Anda setujui. Tagihan sudah diterbitkan oleh Finance "
                  "Wiraky. Pembayaran hanya melalui kanal resmi Wiraky."),
    PAYMENT_CONFIRMED: ("Halo {{1}}, pembayaran biaya tambahan {{2}} sebesar {{3}} untuk pesanan {{4}} "
                        "telah kami terima dan diverifikasi pada {{5}}. Terima kasih."),
}

_RETRIABLE_META_CODES = {130429, 131056, 80007, 131000, 2}  # rate limit / gangguan sementara di sisi Meta
_STATUS_RANK = {"accepted": 0, "sent": 1, "delivered": 2, "read": 3}
_WIB = timezone(timedelta(hours=7))


class WhatsAppError(Exception):
    pass


def _rupiah(value) -> str:
    return "Rp " + f"{int(value):,}".replace(",", ".")


def render_text(kind: str, params: list) -> str:
    text = TEMPLATE_BODIES[kind]
    for index, value in enumerate(params, start=1):
        text = text.replace("{{%d}}" % index, str(value))
    return text


def normalize_recipient(number: str) -> str:
    """'+6281234567890' -> '6281234567890' (format Cloud API: hanya digit, kode negara)."""
    digits = re.sub(r"\D", "", str(number or ""))
    if not 8 <= len(digits) <= 15:
        raise WhatsAppError("Nomor WhatsApp tidak valid")
    return digits


def mask(number: str) -> str:
    digits = re.sub(r"\D", "", str(number or ""))
    return "***" + digits[-4:] if digits else "-"


def requests_transport(method, url, headers, data, timeout):
    import requests
    resp = requests.request(method, url, headers=headers, data=data, timeout=timeout, allow_redirects=False)
    return resp.status_code, resp.content


def _wib_text(paid_at) -> str:
    try:
        moment = parse_iso8601(paid_at) if isinstance(paid_at, str) else paid_at
        return moment.astimezone(_WIB).strftime("%d-%m-%Y %H:%M WIB")
    except Exception:
        return str(paid_at or "-")


class WhatsAppNotifier:
    def __init__(self, repo, settings, now: Callable[[], datetime], transport=None):
        self.repo, self.settings, self.now = repo, settings, now
        self.transport = transport or requests_transport
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ penjadwalan
    def _kind_for(self, record) -> Optional[str]:
        if record.get("superseded"):
            return None
        if record["status"] == PAID:
            return PAYMENT_CONFIRMED
        if record["status"] in (RECEIVED, PAYMENT_PENDING):
            return BILL_ISSUED
        return None

    def _template_name(self, kind):
        return (self.settings.template_bill_issued if kind == BILL_ISSUED
                else self.settings.template_payment_confirmed)

    def _params(self, kind, record):
        name = record["customer"]["name"]
        if kind == BILL_ISSUED:
            return [name, record["order_id"], _rupiah(record["total_amount"]), record["additional_cost_id"]]
        return [name, record["additional_cost_id"], _rupiah(record["total_amount"]), record["order_id"],
                _wib_text(record.get("paid_at"))]

    def enqueue_due(self) -> int:
        """Buat entri notifikasi untuk status yang layak. Mengembalikan jumlah entri baru."""
        created = 0
        with self._lock:
            for record in self.repo.list_costs():
                kind = self._kind_for(record)
                if kind is None or kind not in self.settings.notify_kinds:
                    continue
                key = f"{record['additional_cost_id']}#R{record['revision']}#{kind}"
                if kind == PAYMENT_CONFIRMED:       # sudah lunas: jangan kirim "tagihan terbit" yang belum terkirim
                    bill = self.repo.get_notification(f"{record['additional_cost_id']}#R{record['revision']}#{BILL_ISSUED}")
                    if bill and bill["state"] in ("PENDING", "RETRY"):
                        bill.update(state="SKIPPED", last_error="Sudah lunas sebelum terkirim", updated_at=self.now())
                        self.repo.put_notification(bill)
                if self.repo.get_notification(key):
                    continue
                params = self._params(kind, record)
                try:
                    to = normalize_recipient(record["customer"]["whatsapp"])
                except WhatsAppError as exc:
                    state, error = "FAILED", str(exc)
                else:
                    state, error = "PENDING", None
                now = self.now()
                self.repo.put_notification({
                    "key": key, "additional_cost_id": record["additional_cost_id"], "revision": record["revision"],
                    "kind": kind, "template": self._template_name(kind), "params": params,
                    "preview": render_text(kind, params), "to": record["customer"]["whatsapp"],
                    "state": state, "attempts": 0, "next_attempt_at": now, "created_at": now, "updated_at": now,
                    "message_id": None, "delivery_status": None, "simulated": self.settings.mode != "CLOUD_API",
                    "last_error": error,
                })
                if state == "FAILED":
                    self._alert("WA_INVALID_RECIPIENT", f"Nomor WhatsApp {record['additional_cost_id']} tidak valid; "
                                f"pelanggan tidak mendapat notifikasi.", record["additional_cost_id"])
                created += 1
        return created

    # ------------------------------------------------------------------ pengiriman
    def process_due(self) -> list:
        results = []
        with self._lock:
            self.enqueue_due()
            now = self.now()
            for entry in self.repo.list_notifications():
                if entry["state"] in ("PENDING", "RETRY") and entry["next_attempt_at"] <= now:
                    results.append(self._send(entry))
        return results

    def send_now(self) -> list:
        """Untuk tombol manual: abaikan jadwal backoff pada entri yang menunggu."""
        results = []
        with self._lock:
            self.enqueue_due()
            for entry in self.repo.list_notifications():
                if entry["state"] in ("PENDING", "RETRY"):
                    results.append(self._send(entry))
        return results

    def build_request(self, entry) -> dict:
        return {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": normalize_recipient(entry["to"]),
            "type": "template",
            "template": {
                "name": entry["template"],
                "language": {"code": self.settings.language},
                "components": [{"type": "body",
                                "parameters": [{"type": "text", "text": str(p)} for p in entry["params"]]}],
            },
        }

    def _send(self, entry) -> dict:
        entry["attempts"] += 1
        entry["updated_at"] = self.now()
        if self.settings.mode != "CLOUD_API":
            entry.update(state="SENT", message_id=f"wamid.LOG-{entry['attempts']}-{abs(hash(entry['key'])) % 10**8:08d}",
                         delivery_status="accepted", last_error=None, simulated=True)
            self.repo.put_notification(entry)
            self._audit("WA_LOGGED", entry)
            return {"key": entry["key"], "outcome": "LOGGED"}

        body = json.dumps(self.build_request(entry), ensure_ascii=False).encode("utf-8")
        headers = {"Authorization": f"Bearer {self.settings.access_token}", "Content-Type": "application/json"}
        retriable, error, message_id = False, None, None
        try:
            status, raw = self.transport("POST", self.settings.messages_url, headers, body,
                                         self.settings.request_timeout)
        except Exception as exc:  # jaringan/timeout
            status, raw, retriable, error = None, b"", True, f"TRANSPORT: {type(exc).__name__}"
        else:
            try:
                data = json.loads(raw) if raw else {}
            except ValueError:
                data = {}
            if status == 200 and isinstance(data.get("messages"), list) and data["messages"] \
                    and isinstance(data["messages"][0], dict) and data["messages"][0].get("id"):
                message_id = str(data["messages"][0]["id"])
            else:
                err = data.get("error") if isinstance(data, dict) and isinstance(data.get("error"), dict) else {}
                code = err.get("code")
                retriable = status in (429, 500, 502, 503, 504) or code in _RETRIABLE_META_CODES or status == 200
                error = f"HTTP_{status} meta_code={code} {str(err.get('message', ''))[:120]}".strip()

        if message_id:
            entry.update(state="SENT", message_id=message_id, delivery_status="accepted", last_error=None)
            self.repo.put_notification(entry)
            self._audit("WA_SENT", entry)
            return {"key": entry["key"], "outcome": "SENT"}

        entry["last_error"] = error
        if retriable and entry["attempts"] < self.settings.max_attempts:
            delay = min(self.settings.backoff_cap, self.settings.backoff_base * (2 ** (entry["attempts"] - 1)))
            entry.update(state="RETRY", next_attempt_at=self.now() + timedelta(seconds=delay))
            self.repo.put_notification(entry)
            self._audit("WA_RETRY_SCHEDULED", entry, delay_seconds=delay)
            return {"key": entry["key"], "outcome": "RETRY"}

        entry["state"] = "FAILED"
        self.repo.put_notification(entry)
        self._audit("WA_FAILED", entry)
        self._alert("WA_SEND_FAILED", f"Notifikasi WhatsApp {entry['kind']} untuk {entry['additional_cost_id']} "
                    f"gagal terkirim ({error}). Status tagihan tidak terpengaruh; hubungi pelanggan manual.",
                    entry["additional_cost_id"])
        return {"key": entry["key"], "outcome": "FAILED"}

    # ------------------------------------------------------------------ status dari webhook Meta
    def apply_status_update(self, message_id: str, status: str, errors=None) -> str:
        """sent/delivered/read/failed dari webhook. Mengembalikan UPDATED | IGNORED | UNKNOWN."""
        with self._lock:
            entry = self.repo.find_notification_by_message_id(message_id)
            if not entry:
                return "UNKNOWN"
            if status == "failed":
                detail = "; ".join(f"{e.get('code')}: {str(e.get('title') or e.get('message') or '')[:80]}"
                                   for e in (errors or []) if isinstance(e, dict))
                entry.update(delivery_status="failed", last_error=detail or "failed", updated_at=self.now())
                self.repo.put_notification(entry)
                self._audit("WA_DELIVERY_FAILED", entry)
                self._alert("WA_DELIVERY_FAILED", f"WhatsApp ke pelanggan {entry['additional_cost_id']} gagal "
                            f"terkirim ({detail or 'tanpa detail'}). Hubungi pelanggan manual.",
                            entry["additional_cost_id"])
                return "UPDATED"
            if status not in _STATUS_RANK:
                return "IGNORED"
            current = entry.get("delivery_status")
            if current in _STATUS_RANK and _STATUS_RANK[current] >= _STATUS_RANK[status]:
                return "IGNORED"                       # webhook bisa datang tidak berurutan; jangan turun level
            entry.update(delivery_status=status, updated_at=self.now())
            self.repo.put_notification(entry)
            self._audit("WA_DELIVERY_" + status.upper(), entry)
            return "UPDATED"

    # ------------------------------------------------------------------ util
    def _audit(self, action, entry, **extra):
        self.repo.append_audit(audit_logger.audit_entry(
            action, "whatsapp", self.now(), entry["additional_cost_id"], entry["revision"],
            {"kind": entry["kind"], "to": mask(entry["to"]), "message_id": entry.get("message_id"),
             "attempts": entry["attempts"], "error": entry.get("last_error"), **extra}))

    def _alert(self, code, message, additional_cost_id):
        entry = audit_logger.alert_entry(code, message, self.now(), additional_cost_id, {})
        self.repo.add_alert(entry)
        audit_logger.emit_alert(entry)
