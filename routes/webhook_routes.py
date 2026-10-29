"""
Webhook receiver untuk menerima callback PAYMENT_CONFIRMED dari Sistem Keuangan Wiraky.
Sesuai STANDAR_API_INTEGRATED v1.0 - Halaman 6, 7, & 8.

Endpoint: POST /api/v1/finance/payment-events
"""

from flask import Blueprint, request, jsonify
from services.hmac_utils import verify_signature, is_timestamp_fresh
from core.database import get_db_connection
from core.audit_logger import log_audit
import os
import json

finance_webhook_bp = Blueprint('finance_webhook', __name__)


def validate_and_record_payment(event: dict) -> dict:
    """
    Validasi bisnis dan simpan status pembayaran ke database.
    Sesuai PDF Halaman 8:
    1. Cari order + additional_cost_id + revision yang approved
    2. Cocokkan invoice, total_amount, currency, payment_reference
    3. Jika event_id sudah diproses, return DUPLICATE
    4. Simpan status dan audit dalam SATU transaksi database
    """
    conn = get_db_connection()
    if not conn:
        return {"result": "ERROR", "message": "Database connection failed"}
    
    cursor = conn.cursor(dictionary=True)
    
    try:
        event_id = event.get("event_id")
        order_id = event.get("order_id")
        additional_cost_id = event.get("additional_cost_id")
        revision = event.get("revision")
        invoice_id = event.get("invoice_id")
        payment_reference = event.get("payment_reference")
        amount_paid = event.get("amount_paid")
        currency = event.get("currency")
        paid_at = event.get("paid_at")
        
        # 1. Cek idempotency - apakah event_id sudah pernah diproses?
        cursor.execute(
            "SELECT id FROM finance_events WHERE event_id = %s",
            (event_id,)
        )
        existing_event = cursor.fetchone()
        
        if existing_event:
            # Event sudah diproses - return DUPLICATE (idempotent)
            return {
                "result": "DUPLICATE",
                "message": "Event already processed",
                "event_id": event_id
            }
        
        # 2. Cari order dan additional cost yang approved
        cursor.execute("""
            SELECT ac.*, o.customer_name, o.customer_phone
            FROM additional_costs ac
            JOIN orders o ON ac.order_id = o.id
            WHERE ac.additional_cost_id = %s 
              AND ac.revision = %s
              AND ac.status IN ('APPROVED', 'SENT', 'PAYMENT_PENDING')
        """, (additional_cost_id, revision))
        
        additional_cost = cursor.fetchone()
        
        if not additional_cost:
            return {
                "result": "NOT_FOUND",
                "message": "Additional cost not found or not approved"
            }
        
        # 3. Validasi nominal dan currency
        expected_amount = additional_cost.get("total_amount")
        if abs(amount_paid - expected_amount) > 0.01:
            # Nominal tidak cocok - log dan tolak
            log_audit(
                order_id,
                "PAYMENT_AMOUNT_MISMATCH",
                f"Expected: {expected_amount}, Got: {amount_paid}, Invoice: {invoice_id}"
            )
            return {
                "result": "AMOUNT_MISMATCH",
                "message": "Payment amount does not match approved amount"
            }
        
        if currency != "IDR":
            return {
                "result": "INVALID_CURRENCY",
                "message": "Currency must be IDR"
            }
        
        # 4. Update status ke PAID dalam transaksi atomik
        cursor.execute("""
            UPDATE additional_costs 
            SET status = 'PAID',
                finance_reference = %s,
                invoice_id = %s,
                payment_reference = %s,
                amount_paid = %s,
                paid_at = %s,
                updated_at = NOW()
            WHERE additional_cost_id = %s AND revision = %s
        """, (
            event.get("finance_reference") or additional_cost.get("finance_reference"),
            invoice_id,
            payment_reference,
            amount_paid,
            paid_at,
            additional_cost_id,
            revision
        ))
        
        # 5. Catat event untuk idempotency
        cursor.execute("""
            INSERT INTO finance_events 
            (event_id, event_type, order_id, additional_cost_id, revision, 
             invoice_id, payment_reference, amount_paid, currency, paid_at, 
             raw_payload, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        """, (
            event_id,
            event.get("event_type"),
            order_id,
            additional_cost_id,
            revision,
            invoice_id,
            payment_reference,
            amount_paid,
            currency,
            paid_at,
            json.dumps(event)
        ))
        
        # 6. Commit transaksi
        conn.commit()
        
        # 7. Audit log
        log_audit(
            order_id,
            "PAYMENT_CONFIRMED",
            f"Event: {event_id}, Invoice: {invoice_id}, Amount: {amount_paid}, Ref: {payment_reference}"
        )
        
        # 8. Notifikasi internal (opsional - bisa ditambahkan nanti)
        # send_internal_notification(additional_cost, event)
        
        return {
            "result": "SUCCESS",
            "message": "Payment recorded successfully",
            "order_id": order_id,
            "additional_cost_id": additional_cost_id
        }
    
    except Exception as e:
        conn.rollback()
        log_audit(
            event.get("order_id", "UNKNOWN"),
            "PAYMENT_RECORD_ERROR",
            f"Event: {event.get('event_id')}, Error: {str(e)}"
        )
        return {
            "result": "ERROR",
            "message": str(e)
        }
    finally:
        cursor.close()
        conn.close()


@finance_webhook_bp.route('/payment-events', methods=['POST'])
def finance_payment_webhook():
    """
    Endpoint untuk menerima webhook PAYMENT_CONFIRMED dari Sistem Keuangan Wiraky.
    Sesuai PDF Halaman 7 & 8.
    """
    # Ambil raw body dan headers
    body_bytes = request.get_data(cache=False)
    timestamp = request.headers.get("X-Timestamp", "")
    signature = request.headers.get("X-Signature", "")
    
    # 1. Validasi timestamp freshness (mencegah replay attack)
    if not is_timestamp_fresh(timestamp, max_age_seconds=300):
        return jsonify({"error": "INVALID_TIMESTAMP"}), 401
    
    # 2. Validasi signature HMAC-SHA256
    callback_secret = os.getenv("WIRAKY_FINANCE_CALLBACK_SECRET")
    if not callback_secret:
        return jsonify({"error": "CONFIG_MISSING"}), 500
    
    if not verify_signature(callback_secret, timestamp, body_bytes, signature):
        return jsonify({"error": "INVALID_SIGNATURE"}), 401
    
    # 3. Parse JSON body
    try:
        event = json.loads(body_bytes)
    except ValueError:
        return jsonify({"error": "INVALID_JSON"}), 400
    
    # 4. Validasi event_type
    if event.get("event_type") != "PAYMENT_CONFIRMED":
        return jsonify({"error": "INVALID_EVENT_TYPE"}), 422
    
    # 5. Validasi currency
    if event.get("currency") != "IDR":
        return jsonify({"error": "INVALID_CURRENCY"}), 422
    
    # 6. Jika status bukan PAID, catat saja (tidak update ke LUNAS)
    if event.get("payment_status") != "PAID":
        # Catat status informatif (PENDING, FAILED, EXPIRED, dll)
        log_audit(
            event.get("order_id", "UNKNOWN"),
            "PAYMENT_STATUS_UPDATE",
            f"Event: {event.get('event_id')}, Status: {event.get('payment_status')}"
        )
        return jsonify({"result": "NOT_PAID"}), 200
    
    # 7. Validasi bisnis dan simpan ke database
    result = validate_and_record_payment(event)
    
    # 8. Return response sesuai hasil
    if result.get("result") == "SUCCESS":
        return jsonify(result), 200
    elif result.get("result") == "DUPLICATE":
        # Idempotent - return 200 OK tanpa error
        return jsonify(result), 200
    elif result.get("result") == "AMOUNT_MISMATCH":
        return jsonify(result), 409  # Conflict
    elif result.get("result") == "NOT_FOUND":
        return jsonify(result), 404
    else:
        return jsonify(result), 500