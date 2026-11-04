"""Logika bisnis Standar API Integrated (bagian 01, 08, 09).

Prinsip yang ditegakkan di sini:
- Tanpa Additional Cost (total = 0) -> tidak ada panggilan API penagihan (NOT_REQUIRED).
- Pengiriman hanya setelah verifikasi Engineering + persetujuan pelanggan tercatat.
- RECEIVED (HTTP 202) hanyalah bukti Finance menerima pengajuan, BUKAN bukti sudah dibayar.
- PAID hanya diterapkan bila: callback terautentikasi (dicek di route), event belum pernah
  diproses, referensi/revisi/nominal/mata uang cocok, dan verified_by berasal dari Finance.
- Nominal berbeda -> tolak LUNAS, catat konflik, beri notifikasi admin, TIDAK auto-update.
- Status dan audit disimpan dalam SATU transaksi repository.
"""
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from core import audit_logger
from core.master_price import build_items
from core.payload import (PayloadError, WIB, amounts_equal, build_additional_cost_event,
                          new_event_id, parse_iso8601, validate_outbound, wib_iso)
from core.status import (APPROVED, EXPIRED, FAILED, NOT_REQUIRED, PAID, PAYMENT_PENDING,
                         RECEIVED, REFUNDED, REVIEW, SENT, can_transition)

Clock = Callable[[], datetime]

_PHONE_RE = re.compile(r"^\+?[0-9]{8,15}$")


class BusinessRuleError(Exception):
    """Pelanggaran aturan bisnis; membawa kode mesin-terbaca dan HTTP status yang pantas."""

    def __init__(self, code: str, message: str, http_status: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


@dataclass
class PaymentOutcome:
    result: str                       # RECORDED | DUPLICATE | ALREADY_PAID | NOT_PAID | REFUND_RECORDED | REJECTED
    http_status: int = 200
    error: Optional[str] = None       # kode error bila ditolak
    message: Optional[str] = None
    recorded_status: Optional[str] = None


# ---------------------------------------------------------------------------
# helper internal
# ---------------------------------------------------------------------------

def _audit(repo, logs, action, actor, current, *, record=None, additional_cost_id=None,
           revision=None, **details):
    if record is not None:
        additional_cost_id, revision = record["additional_cost_id"], record["revision"]
    entry = audit_logger.audit_entry(action, actor, current, additional_cost_id, revision, details)
    repo.append_audit(entry)
    logs.append(("audit", entry))


def _alert(repo, logs, code, message, current, *, additional_cost_id=None, **details):
    entry = audit_logger.alert_entry(code, message, current, additional_cost_id, details)
    repo.add_alert(entry)
    logs.append(("alert", entry))


def _emit(logs):
    for kind, entry in logs:
        (audit_logger.emit_audit if kind == "audit" else audit_logger.emit_alert)(entry)


def _move(record, new_status, current, note):
    old = record["status"]
    if not can_transition(old, new_status):
        raise BusinessRuleError("INVALID_TRANSITION", f"Perpindahan status {old} -> {new_status} tidak diizinkan", 409)
    if old != new_status:
        record["history"].append({"at": current, "from": old, "to": new_status, "note": note})
    record["status"] = new_status
    record["updated_at"] = current


def _imply_ack(repo, record, current, logs, actor, event_id):
    """Callback terautentikasi dari Finance membuktikan Finance sudah menerima pengajuan,
    walau respons 202 kita hilang (mis. timeout). Majukan status dan hentikan retry outbox."""
    if record["status"] in (APPROVED, SENT):
        if record["status"] == APPROVED:
            _move(record, SENT, current, "ack tersirat dari callback")
        _move(record, RECEIVED, current, "ack tersirat dari callback")
        record["received_at"] = record.get("received_at") or current
        entry = repo.get_outbox(record["event_id"])
        if entry and entry["state"] in ("PENDING", "FAILED"):
            entry["state"] = "ACKED"
            entry["acked_at"] = current
            repo.put_outbox(entry)
        _audit(repo, logs, "ACK_IMPLIED_BY_CALLBACK", actor, current, record=record, event_id=event_id)


def _clean_customer(customer: Optional[dict]) -> dict:
    customer = customer or {}
    name = str(customer.get("name") or "").strip()
    package = str(customer.get("package") or "").strip()
    if not name:
        raise BusinessRuleError("CUSTOMER_NAME_REQUIRED", "Nama pelanggan wajib diisi")
    if not package:
        raise BusinessRuleError("CUSTOMER_PACKAGE_REQUIRED", "Paket pelanggan wajib diisi")
    whatsapp = re.sub(r"[\s\-()]", "", str(customer.get("whatsapp") or ""))
    if whatsapp.startswith("0"):
        whatsapp = "+62" + whatsapp[1:]
    elif whatsapp.startswith("62"):
        whatsapp = "+" + whatsapp
    if not _PHONE_RE.match(whatsapp):
        raise BusinessRuleError("WHATSAPP_INVALID", "Nomor WhatsApp pelanggan tidak valid (contoh: 081234567890)")
    return {"name": name, "whatsapp": whatsapp, "package": package}


def _require_approval(approval: Optional[dict]) -> dict:
    approval = approval or {}
    if approval.get("status") != "APPROVED" or approval.get("engineering_verified") is not True \
            or not str(approval.get("reference") or "").strip():
        raise BusinessRuleError(
            "NOT_APPROVED",
            "Pengajuan hanya boleh dikirim setelah verifikasi Engineering dan persetujuan pelanggan tercatat",
            422)
    return approval


# ---------------------------------------------------------------------------
# 1. pembuatan Additional Cost (+ antrean outbox)
# ---------------------------------------------------------------------------

def create_additional_cost(repo, *, order_id: str, customer: dict, selection: dict, approval: Optional[dict],
                           now: Clock, additional_cost_id: Optional[str] = None,
                           actor: str = "surveyor") -> dict:
    """Buat Additional Cost baru, atau revisi baru bila `additional_cost_id` diberikan.

    Total 0 -> NOT_REQUIRED (tidak ada outbox, tidak ada panggilan Finance).
    Total > 0 -> wajib approval lengkap; record APPROVED + 1 entri outbox PENDING.
    """
    order_id = str(order_id or "").strip()
    if not order_id:
        raise BusinessRuleError("ORDER_ID_REQUIRED", "Nomor order wajib diisi")
    customer = _clean_customer(customer)
    try:
        items = build_items(selection)
    except ValueError as exc:
        raise BusinessRuleError("INVALID_ITEMS", str(exc))
    total = sum(i["subtotal"] for i in items)
    current = now()
    logs: List[Tuple[str, dict]] = []

    with repo.transaction():
        if total == 0:
            if additional_cost_id:
                raise BusinessRuleError("ZERO_REVISION_UNSUPPORTED",
                                        "Revisi menjadi Rp0 harus melalui prosedur pembatalan Finance", 409)
            ac_id = f"AC-{current.astimezone(WIB).year}-{repo.next_sequence('additional_cost'):04d}"
            record = _new_record(ac_id, 1, order_id, customer, [], 0, None, NOT_REQUIRED, current)
            record["history"].append({"at": current, "from": None, "to": NOT_REQUIRED,
                                      "note": "tidak ada Additional Cost"})
            repo.save_cost(record)
            _audit(repo, logs, "ADDITIONAL_COST_NOT_REQUIRED", actor, current, record=record)
            result_id, result_rev = ac_id, 1
        else:
            approval = _require_approval(approval)
            approved_at = approval.get("approved_at") or wib_iso(current)
            if parse_iso8601(approved_at) is None:
                raise BusinessRuleError("APPROVED_AT_INVALID", "approved_at harus ISO-8601 berzona waktu")

            if additional_cost_id:
                previous = repo.get_cost(additional_cost_id)
                if previous is None:
                    raise BusinessRuleError("COST_NOT_FOUND", f"{additional_cost_id} tidak ditemukan", 404)
                if previous["order_id"] != order_id:
                    raise BusinessRuleError("ORDER_MISMATCH", "Order tidak sesuai dengan Additional Cost", 409)
                if previous["status"] in (PAID, REFUNDED, REVIEW, NOT_REQUIRED):
                    raise BusinessRuleError(
                        "REVISION_NOT_ALLOWED",
                        f"Additional Cost berstatus {previous['status']}; revisi perlu prosedur resmi Finance", 409)
                ac_id, revision = additional_cost_id, previous["revision"] + 1
                previous["superseded"] = True
                previous["updated_at"] = current
                previous["history"].append({"at": current, "from": previous["status"], "to": previous["status"],
                                            "note": f"digantikan revisi {revision}"})
                repo.save_cost(previous)
                old_entry = repo.get_outbox(previous["event_id"])
                if old_entry and old_entry["state"] == "PENDING":
                    old_entry["state"] = "CANCELLED"
                    repo.put_outbox(old_entry)
                _audit(repo, logs, "REVISION_SUPERSEDES_PREVIOUS", actor, current, record=previous,
                       new_revision=revision)
            else:
                active = [c for c in repo.list_costs(order_id)
                          if c["status"] != NOT_REQUIRED and not c["superseded"]]
                if active:
                    raise BusinessRuleError(
                        "ACTIVE_COST_EXISTS",
                        f"Order {order_id} sudah memiliki Additional Cost {active[0]['additional_cost_id']} "
                        f"({active[0]['status']}). Gunakan revisi; tidak boleh membuat tagihan ganda.", 409)
                ac_id = f"AC-{current.astimezone(WIB).year}-{repo.next_sequence('additional_cost'):04d}"
                revision = 1

            approval_block = {"status": "APPROVED", "reference": str(approval["reference"]).strip(),
                              "approved_at": approved_at, "engineering_verified": True}
            record = _new_record(ac_id, revision, order_id, customer, items, total, approval_block, APPROVED, current)
            record["event_id"] = new_event_id(ac_id, revision)
            record["history"].append({"at": current, "from": None, "to": APPROVED,
                                      "note": "diverifikasi Engineering dan disetujui pelanggan"})
            payload = build_additional_cost_event(record, current)
            try:
                validate_outbound(payload)
            except PayloadError as exc:
                raise BusinessRuleError("INVALID_PAYLOAD", ", ".join(exc.errors), 422)
            record["payload"] = payload
            repo.save_cost(record)
            repo.put_outbox({
                "event_id": record["event_id"], "additional_cost_id": ac_id, "revision": revision,
                "state": "PENDING", "attempts": 0, "next_attempt_at": current, "created_at": current,
                "acked_at": None, "last_error": None, "last_http_status": None,
            })
            _audit(repo, logs, "ADDITIONAL_COST_APPROVED", actor, current, record=record,
                   total_amount=total, approval_reference=approval_block["reference"])
            result_id, result_rev = ac_id, revision

    _emit(logs)
    return repo.get_cost(result_id, result_rev)


def _new_record(ac_id, revision, order_id, customer, items, total, approval, status, current):
    return {
        "additional_cost_id": ac_id, "revision": revision, "order_id": order_id,
        "customer": customer, "items": items, "total_amount": total, "currency": "IDR",
        "approval": approval, "status": status, "event_id": None, "payload": None,
        "finance_reference": None, "invoice_id": None, "payment_reference": None,
        "paid_at": None, "verified_by": None, "superseded": False, "needs_review": False,
        "created_at": current, "updated_at": current, "received_at": None,
        "last_reconcile_at": None, "history": [],
    }


# ---------------------------------------------------------------------------
# 2. hasil pengiriman ke Finance (dipanggil outbox)
# ---------------------------------------------------------------------------

def record_send_success(repo, event_id: str, body: dict, now: Clock, http_status: int = 202):
    """Finance menjawab 202 RECEIVED. Ini BUKAN bukti pembayaran."""
    current = now()
    logs: List[Tuple[str, dict]] = []
    with repo.transaction():
        entry = repo.get_outbox(event_id)
        if entry is None:
            return None
        entry["attempts"] += 1
        entry.update(state="ACKED", acked_at=current, last_error=None, last_http_status=http_status)
        repo.put_outbox(entry)
        record = repo.get_cost(entry["additional_cost_id"], entry["revision"])
        if record is not None:
            record["finance_reference"] = body.get("finance_reference") or record.get("finance_reference")
            if record["status"] in (APPROVED, SENT):
                if record["status"] == APPROVED:
                    _move(record, SENT, current, "pengajuan terkirim")
                _move(record, RECEIVED, current, f"Finance menerima ({body.get('finance_reference')})")
                record["received_at"] = current
            record["updated_at"] = current
            repo.save_cost(record)
            _audit(repo, logs, "FINANCE_ACK_RECEIVED", "system", current, record=record,
                   finance_reference=record["finance_reference"], http_status=http_status)
    _emit(logs)
    return repo.get_cost(entry["additional_cost_id"], entry["revision"])


def record_send_failure(repo, event_id: str, *, error: str, retriable: bool, now: Clock, settings,
                        http_status: Optional[int] = None):
    """Kegagalan kirim. Retriable -> jadwalkan ulang (exponential backoff, event_id SAMA).
    Tidak retriable / percobaan habis -> REVIEW + notifikasi admin, data tidak hilang."""
    current = now()
    logs: List[Tuple[str, dict]] = []
    with repo.transaction():
        entry = repo.get_outbox(event_id)
        if entry is None:
            return None
        entry["attempts"] += 1
        entry["last_error"] = error
        entry["last_http_status"] = http_status
        record = repo.get_cost(entry["additional_cost_id"], entry["revision"])
        exhausted = entry["attempts"] >= settings.outbox_max_attempts
        if retriable and not exhausted:
            delay = min(settings.outbox_backoff_cap, settings.outbox_backoff_base * (2 ** (entry["attempts"] - 1)))
            entry["state"] = "PENDING"
            entry["next_attempt_at"] = current + _seconds(delay)
            _audit(repo, logs, "SEND_RETRY_SCHEDULED", "system", current, record=record,
                   attempts=entry["attempts"], retry_in_seconds=delay, error=error)
        else:
            entry["state"] = "FAILED"
            reason = "percobaan kirim habis" if retriable else "ditolak Finance"
            if record is not None and record["status"] in (APPROVED, SENT):
                _move(record, REVIEW, current, f"pengiriman gagal: {reason}")
                record["needs_review"] = True
                repo.save_cost(record)
            _audit(repo, logs, "SEND_FAILED", "system", current, record=record,
                   attempts=entry["attempts"], reason=reason, error=error, http_status=http_status)
            _alert(repo, logs, "SEND_FAILED",
                   f"Pengajuan {entry['additional_cost_id']} rev {entry['revision']} gagal dikirim ke Finance ({reason}): {error}",
                   current, additional_cost_id=entry["additional_cost_id"], http_status=http_status)
        repo.put_outbox(entry)
    _emit(logs)
    return entry


def _seconds(value: float):
    from datetime import timedelta
    return timedelta(seconds=value)


# ---------------------------------------------------------------------------
# 3. pemrosesan event pembayaran dari Finance (webhook ATAU rekonsiliasi)
# ---------------------------------------------------------------------------

def process_payment_event(repo, event: dict, *, now: Clock, settings, source: str = "WEBHOOK") -> PaymentOutcome:
    """Validasi bisnis + pencatatan status. `event` sudah lolos autentikasi dan validasi struktur.

    Idempotent: event_id yang sudah diproses -> DUPLICATE tanpa perubahan apa pun.
    """
    current = now()
    logs: List[Tuple[str, dict]] = []
    with repo.transaction():
        outcome = _process(repo, event, current, settings, f"finance:{source.lower()}", logs)
    _emit(logs)
    return outcome


def _process(repo, event, current, settings, actor, logs) -> PaymentOutcome:
    event_id, ac_id, rev = event["event_id"], event["additional_cost_id"], event["revision"]
    pay_status = event["payment_status"]

    def reject(code, message, http=409, record=None, **details):
        _audit(repo, logs, f"REJECTED_{code}", actor, current, record=record,
               additional_cost_id=ac_id, revision=rev, event_id=event_id, payment_status=pay_status, **details)
        _alert(repo, logs, code, message, current, additional_cost_id=ac_id, event_id=event_id, **details)
        return PaymentOutcome("REJECTED", http, code, message)

    def accept(result, record, note):
        repo.mark_event(event_id, {"at": current, "additional_cost_id": ac_id, "revision": rev,
                                   "payment_status": pay_status, "source": actor})
        record["updated_at"] = current
        repo.save_cost(record)
        _audit(repo, logs, note, actor, current, record=record, event_id=event_id, payment_status=pay_status)
        return PaymentOutcome(result, 200, recorded_status=record["status"])

    # 1. idempotency
    if repo.has_event(event_id):
        _audit(repo, logs, "DUPLICATE_EVENT_IGNORED", actor, current, additional_cost_id=ac_id,
               revision=rev, event_id=event_id)
        return PaymentOutcome("DUPLICATE", 200)

    # 2. referensi
    record = repo.get_cost(ac_id, rev)
    if record is None:
        if repo.latest_revision(ac_id) is None:
            return reject("UNKNOWN_REFERENCE", f"{ac_id} tidak dikenal oleh Green Energy")
        return reject("REVISION_MISMATCH", f"{ac_id} revisi {rev} tidak dikenal")
    if record["order_id"] != event["order_id"]:
        return reject("ORDER_MISMATCH", f"order_id pada event tidak cocok untuk {ac_id}", record=record)
    if record["status"] == NOT_REQUIRED:
        return reject("UNKNOWN_REFERENCE", f"{ac_id} tidak memerlukan penagihan", record=record)
    if record["superseded"]:
        return reject("REVISION_SUPERSEDED",
                      f"Event untuk {ac_id} rev {rev} yang sudah digantikan revisi lebih baru; periksa manual "
                      f"(bisa berarti pembayaran masuk untuk tagihan lama)", record=record)

    # 3. status non-final
    if pay_status == "PENDING":
        if record["status"] in (PAID, REFUNDED, REVIEW):
            return accept_stale(repo, logs, actor, current, record, event_id, ac_id, rev, pay_status)
        _imply_ack(repo, record, current, logs, actor, event_id)
        record["invoice_id"] = event.get("invoice_id") or record["invoice_id"]
        _move(record, PAYMENT_PENDING, current, "Finance: menunggu pembayaran")
        return accept("NOT_PAID", record, "PAYMENT_PENDING_RECORDED")

    if pay_status in ("FAILED", "EXPIRED"):
        if record["status"] in (PAID, REFUNDED, REVIEW):
            return reject("STATE_CONFLICT", f"Event {pay_status} diterima untuk {ac_id} yang sudah {record['status']}",
                          record=record)
        _imply_ack(repo, record, current, logs, actor, event_id)
        _move(record, FAILED if pay_status == "FAILED" else EXPIRED, current, f"Finance: {pay_status}")
        return accept("NOT_PAID", record, f"PAYMENT_{pay_status}_RECORDED")

    if pay_status == "REFUNDED":
        if record["status"] == REFUNDED:
            return accept("DUPLICATE", record, "REFUND_ALREADY_RECORDED")
        if record["status"] != PAID:
            return reject("STATE_CONFLICT", f"REFUNDED diterima untuk {ac_id} yang berstatus {record['status']}",
                          record=record)
        _move(record, REFUNDED, current, "Finance: refund")
        record["needs_review"] = True
        _alert(repo, logs, "REFUND_AFTER_PAID",
               f"{ac_id} di-refund setelah PAID. Status tidak dihapus; tentukan dampak Journey melalui prosedur resmi.",
               current, additional_cost_id=ac_id, event_id=event_id)
        return accept("REFUND_RECORDED", record, "REFUND_RECORDED")

    # 4. PAID: validasi ketat
    if event.get("verified_by") != settings.expected_verifier:
        return reject("INVALID_VERIFIER", "verified_by bukan dari sistem Keuangan Wiraky", 422, record=record,
                      verified_by=str(event.get("verified_by")))
    if event.get("currency") != record["currency"]:
        return reject("CURRENCY_MISMATCH", "Mata uang tidak cocok", 422, record=record)
    if not amounts_equal(event.get("amount_paid"), record["total_amount"]):
        return reject("AMOUNT_MISMATCH",
                      f"Nominal {ac_id}: dibayar {event.get('amount_paid')} != disetujui {record['total_amount']}. "
                      f"LUNAS ditolak, status tidak diubah.", record=record,
                      amount_paid=str(event.get("amount_paid")), expected=record["total_amount"])
    if record["invoice_id"] and record["invoice_id"] != event["invoice_id"]:
        return reject("INVOICE_MISMATCH", f"invoice_id berbeda untuk {ac_id}", record=record,
                      invoice_id=event["invoice_id"], expected=record["invoice_id"])

    if record["status"] == PAID:
        if record["payment_reference"] == event["payment_reference"]:
            return accept("ALREADY_PAID", record, "ALREADY_PAID_IGNORED")
        return reject("PAYMENT_REFERENCE_CONFLICT",
                      f"{ac_id} sudah PAID dengan referensi lain: kemungkinan pembayaran ganda", record=record,
                      existing=record["payment_reference"], received=event["payment_reference"])
    if record["status"] in (REFUNDED, REVIEW):
        return reject("STATE_CONFLICT", f"PAID diterima untuk {ac_id} yang berstatus {record['status']}", record=record)

    _imply_ack(repo, record, current, logs, actor, event_id)
    previous = record["status"]
    _move(record, PAID, current, f"Finance memverifikasi pembayaran {event['payment_reference']}")
    record.update(invoice_id=event["invoice_id"], payment_reference=event["payment_reference"],
                  verified_by=event["verified_by"], paid_at=event["paid_at"])
    if previous in (FAILED, EXPIRED):
        _alert(repo, logs, "LATE_PAYMENT", f"{ac_id} dibayar setelah berstatus {previous}; Finance memverifikasi bukti bank.",
               current, additional_cost_id=ac_id, event_id=event_id)
    return accept("RECORDED", record, "PAYMENT_PAID_RECORDED")


def accept_stale(repo, logs, actor, current, record, event_id, ac_id, rev, pay_status) -> PaymentOutcome:
    """Event non-final yang datang terlambat (out-of-order) setelah status final: diabaikan, tidak menurunkan status."""
    repo.mark_event(event_id, {"at": current, "additional_cost_id": ac_id, "revision": rev,
                               "payment_status": pay_status, "source": actor, "stale": True})
    _audit(repo, logs, "STALE_EVENT_IGNORED", actor, current, record=record, event_id=event_id,
           payment_status=pay_status, current_status=record["status"])
    return PaymentOutcome("NOT_PAID", 200, recorded_status=record["status"])
