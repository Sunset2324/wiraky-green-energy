import os, json, hmac, hashlib
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify

webhook_bp = Blueprint('webhook_bp', __name__)
FINANCE_CALLBACK_SECRET = os.environ.get("WIRAKY_FINANCE_CALLBACK_SECRET")

def valid_signature(body, timestamp, supplied_signature):
    signed = timestamp.encode("utf-8") + b"." + body
    expected = hmac.new(FINANCE_CALLBACK_SECRET.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, supplied_signature)

def fresh_timestamp(value, max_age_seconds=300):
    try:
        received = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        delta = abs((datetime.now(timezone.utc) - received).total_seconds())
        return delta <= max_age_seconds
    except ValueError:
        return False

@webhook_bp.route("/finance/payment-events", methods=["POST"])
def finance_payment_webhook():
    body = request.get_data(cache=False)
    timestamp = request.headers.get("X-Timestamp", "")
    signature = request.headers.get("X-Signature", "")

    # 1. Keamanan: Cek Timestamp & Signature
    if not fresh_timestamp(timestamp):
        return jsonify(error="INVALID_TIMESTAMP"), 401
    if not valid_signature(body, timestamp, signature):
        return jsonify(error="INVALID_SIGNATURE"), 401

    try:
        event = json.loads(body)
    except ValueError:
        return jsonify(error="INVALID_JSON"), 400

    # 2. Validasi Event
    if event.get("event_type") != "PAYMENT_CONFIRMED":
        return jsonify(error="INVALID_EVENT_TYPE"), 422
    if event.get("currency") != "IDR":
        return jsonify(error="INVALID_CURRENCY"), 422
    if event.get("payment_status") != "PAID":
        return jsonify(result="NOT_PAID"), 200 # Status informatif, bukan LUNAS

    # 3. Idempotency & Database Transaction
    # Cek apakah event_id sudah pernah diproses di DB Green Energy
    # Jika sudah, return 200 OK tanpa mengubah DB (Mencegah duplikasi)
    
    # TODO: Panggil fungsi database transaksional
    # validate_and_record_payment(event) 
    
    return jsonify(result="SUCCESS_PROCESSED"), 200