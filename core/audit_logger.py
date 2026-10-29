"""Audit log dan alert admin.

Entri disimpan di repository (di DALAM transaksi yang sama dengan perubahan status, sesuai
standar bagian 08) dan juga ditulis ke logger Python setelah transaksi berhasil.
"""
import json
import logging
from datetime import datetime

audit_log = logging.getLogger("wiraky.audit")
alert_log = logging.getLogger("wiraky.alert")


def audit_entry(action, actor, now, additional_cost_id=None, revision=None, details=None):
    return {
        "at": now,
        "action": action,
        "actor": actor,
        "additional_cost_id": additional_cost_id,
        "revision": revision,
        "details": details or {},
    }


def alert_entry(code, message, now, additional_cost_id=None, details=None):
    """Notifikasi admin (konflik nominal, kegagalan kirim, refund, dst)."""
    return {
        "at": now,
        "code": code,
        "message": message,
        "additional_cost_id": additional_cost_id,
        "details": details or {},
    }


def _jsonable(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"tidak bisa serialisasi {type(value)}")


def emit_audit(entry):
    audit_log.info(json.dumps(entry, ensure_ascii=False, default=_jsonable))


def emit_alert(entry):
    alert_log.warning(json.dumps(entry, ensure_ascii=False, default=_jsonable))
