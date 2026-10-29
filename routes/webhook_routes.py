"""Callback pembayaran dari Finance (Standar API Integrated bagian 03, 07, 08).

POST /api/v1/finance/payment-events

Urutan pemeriksaan: rate limit -> timestamp -> HMAC signature -> JSON -> event_type ->
mata uang -> struktur -> validasi bisnis + pencatatan (idempotent, atomik).
Kode respons: 200 sukses/duplikat aman | 400 JSON rusak | 401 autentikasi gagal |
409 konflik bisnis (nominal/referensi/revisi/status) | 422 isi tidak valid | 429 rate limit.
"""
import json

from flask import Blueprint, current_app, jsonify, request

from core import payment_logic as logic
from core.payload import validate_payment_event
from core.security import is_fresh, verify_signature

webhook_bp = Blueprint("finance_webhook", __name__)


@webhook_bp.post("/api/v1/finance/payment-events")
def finance_payment_webhook():
    svc = current_app.extensions["wiraky"]

    if not svc.limiter.allow(request.remote_addr or "unknown"):
        return jsonify(error="RATE_LIMITED"), 429

    body = request.get_data(cache=False)
    timestamp = request.headers.get("X-Timestamp", "")
    signature = request.headers.get("X-Signature", "")

    if not is_fresh(timestamp, svc.now(), svc.settings.timestamp_max_age):
        return jsonify(error="INVALID_TIMESTAMP"), 401
    if not verify_signature(svc.settings.inbound_secrets(), timestamp, body, signature):
        return jsonify(error="INVALID_SIGNATURE"), 401

    try:
        event = json.loads(body)
    except ValueError:
        return jsonify(error="INVALID_JSON"), 400
    if not isinstance(event, dict):
        return jsonify(error="INVALID_JSON"), 400

    if event.get("event_type") != "PAYMENT_CONFIRMED":
        return jsonify(error="INVALID_EVENT_TYPE"), 422
    if event.get("currency") != "IDR":
        return jsonify(error="INVALID_CURRENCY"), 422
    header_event_id = request.headers.get("X-Event-ID")
    if header_event_id and header_event_id != event.get("event_id"):
        return jsonify(error="EVENT_ID_MISMATCH"), 422
    problems = validate_payment_event(event)
    if problems:
        return jsonify(error="INVALID_PAYLOAD", details=problems), 422

    outcome = logic.process_payment_event(svc.repo, event, now=svc.now, settings=svc.settings, source="webhook")
    if outcome.error:
        return jsonify(error=outcome.error, message=outcome.message), outcome.http_status
    return jsonify(result=outcome.result, status=outcome.recorded_status), outcome.http_status
