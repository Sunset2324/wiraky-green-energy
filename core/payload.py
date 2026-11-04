"""Kontrak payload (Standar API Integrated bagian 04 dan 06): pembentukan + validasi."""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import List, Optional

from core.status import PAYMENT_STATUSES

SCHEMA_VERSION = "1.0"
WIB = timezone(timedelta(hours=7))


class PayloadError(ValueError):
    """Payload tidak memenuhi kontrak. `errors` berisi daftar kode masalah."""

    def __init__(self, errors: List[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def canonical_body(payload: dict) -> bytes:
    """Serialisasi JSON ringkas UTF-8. Bytes inilah yang ditandatangani (byte-for-byte)."""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def utc_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def wib_iso(dt: datetime) -> str:
    return dt.astimezone(WIB).isoformat(timespec="seconds")


def parse_iso8601(value) -> Optional[datetime]:
    """Parse ISO-8601 yang WAJIB berzona waktu ('Z' atau '+07:00'). None jika tidak valid."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def new_event_id(additional_cost_id: str, revision: int) -> str:
    """Contoh: AC-2026-0001 rev 1 -> EVT-AC-2026-0001-R01 (stabil, tidak berubah saat retry)."""
    return f"EVT-{additional_cost_id}-R{int(revision):02d}"


def _is_number(value) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def build_additional_cost_event(record: dict, occurred_at: datetime) -> dict:
    """Payload ADDITIONAL_COST_APPROVED persis seperti contoh bagian 04."""
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": record["event_id"],
        "event_type": "ADDITIONAL_COST_APPROVED",
        "occurred_at": utc_z(occurred_at),
        "source": "GREEN_ENERGY",
        "order_id": record["order_id"],
        "additional_cost_id": record["additional_cost_id"],
        "revision": record["revision"],
        "customer": {
            "name": record["customer"]["name"],
            "whatsapp": record["customer"]["whatsapp"],
            "package": record["customer"]["package"],
        },
        "additional_items": [dict(i) for i in record["items"]],
        "total_amount": record["total_amount"],
        "currency": record["currency"],
        "approval": {
            "status": record["approval"]["status"],
            "reference": record["approval"]["reference"],
            "approved_at": record["approval"]["approved_at"],
        },
        "payment_status": "NOT_BILLED",
    }


def validate_outbound(payload: dict) -> None:
    """Validasi minimum bagian 04. Melempar PayloadError bila ada yang tidak valid."""
    errors: List[str] = []
    p = payload or {}

    if p.get("schema_version") != SCHEMA_VERSION:
        errors.append("SCHEMA_VERSION")
    if p.get("event_type") != "ADDITIONAL_COST_APPROVED":
        errors.append("EVENT_TYPE")
    for field in ("event_id", "order_id", "additional_cost_id"):
        if not isinstance(p.get(field), str) or not p.get(field).strip():
            errors.append(f"{field.upper()}_REQUIRED")
    revision = p.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        errors.append("REVISION")
    elif isinstance(p.get("event_id"), str) and isinstance(p.get("additional_cost_id"), str):
        if p["event_id"] != new_event_id(p["additional_cost_id"], revision):
            errors.append("EVENT_ID_INCONSISTENT")

    approval = p.get("approval") or {}
    if approval.get("status") != "APPROVED":
        errors.append("APPROVAL_NOT_APPROVED")
    if not approval.get("reference"):
        errors.append("APPROVAL_REFERENCE_REQUIRED")
    if parse_iso8601(approval.get("approved_at")) is None:
        errors.append("APPROVED_AT_INVALID")

    if p.get("currency") != "IDR":
        errors.append("CURRENCY_MUST_BE_IDR")

    customer = p.get("customer") or {}
    for field in ("name", "whatsapp", "package"):
        if not isinstance(customer.get(field), str) or not customer.get(field).strip():
            errors.append(f"CUSTOMER_{field.upper()}_REQUIRED")

    total = p.get("total_amount")
    if isinstance(total, bool) or not isinstance(total, int) or total <= 0:
        errors.append("TOTAL_AMOUNT_MUST_BE_POSITIVE_INTEGER")

    items = p.get("additional_items")
    if not isinstance(items, list) or not items:
        errors.append("ITEMS_REQUIRED")
    else:
        running = 0
        for index, item in enumerate(items):
            qty, price, sub = item.get("qty"), item.get("unit_price"), item.get("subtotal")
            if not all(isinstance(v, int) and not isinstance(v, bool) for v in (qty, price, sub)) or qty <= 0:
                errors.append(f"ITEM_{index}_INVALID")
                continue
            if qty * price != sub:
                errors.append(f"ITEM_{index}_SUBTOTAL_MISMATCH")
            running += sub
        if isinstance(total, int) and not isinstance(total, bool) and running != total:
            errors.append("ITEMS_TOTAL_MISMATCH")

    if errors:
        raise PayloadError(errors)


def validate_payment_event(event: dict) -> List[str]:
    """Validasi struktur callback pembayaran (bagian 06). Mengembalikan daftar masalah."""
    errors: List[str] = []
    e = event if isinstance(event, dict) else {}
    for field in ("event_id", "order_id", "additional_cost_id"):
        if not isinstance(e.get(field), str) or not e.get(field).strip():
            errors.append(f"{field.upper()}_REQUIRED")
    revision = e.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        errors.append("REVISION_INVALID")
    status = e.get("payment_status")
    if status not in PAYMENT_STATUSES:
        errors.append("PAYMENT_STATUS_INVALID")
    if status == "PAID":
        for field in ("invoice_id", "payment_reference", "verified_by"):
            if not isinstance(e.get(field), str) or not e.get(field).strip():
                errors.append(f"{field.upper()}_REQUIRED")
        if not _is_number(e.get("amount_paid")):
            errors.append("AMOUNT_PAID_INVALID")
        if parse_iso8601(e.get("paid_at")) is None:
            errors.append("PAID_AT_INVALID")
    return errors


def amounts_equal(a, b) -> bool:
    """Bandingkan nominal secara eksak (tanpa error float)."""
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except (InvalidOperation, ValueError):
        return False
