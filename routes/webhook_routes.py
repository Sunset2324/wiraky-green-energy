from flask import Blueprint, request, jsonify
from services.gateway_service import verify_webhook_signature, check_payment_status
from services.whatsapp_service import send_whatsapp_notification
from core.database import get_db_connection # Asumsi ada fungsi ini
from core.audit_logger import log_audit # Asumsi ada fungsi log

webhook_bp = Blueprint('webhook', __name__)

@webhook_bp.route('/xendit', methods=['POST'])
def handle_xendit_webhook():
    """
    Endpoint untuk menerima notifikasi dari Payment Gateway.
    """
    payload = request.get_json() or request.form.to_dict()
    
    # 1. Ambil Signature dari Header (Sesuaikan dengan nama header gateway, misal: x-callback-token)
    signature = request.headers.get('x-callback-token', '')
    
    # 2. Verifikasi Keamanan (Sesuai PDF: memverifikasi signature/secret)
    if not verify_webhook_signature(payload, signature):
        return jsonify({"error": "Unauthorized: Invalid signature"}), 401

    # 3. Ekstrak Data Penting
    external_id = payload.get('external_id')  # Ini adalah WRK-INV-{order_id} kita
    gateway_payment_id = payload.get('id')
    status = payload.get('status')            # e.g., 'PAID', 'EXPIRED'
    amount_paid = payload.get('amount', 0)
    payment_method = payload.get('payment_method', 'Unknown')

    # 4. Hubungkan ke Database
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Ambil data invoice berdasarkan external_id
        cursor.execute("SELECT * FROM invoices WHERE external_id = %s", (external_id,))
        invoice = cursor.fetchone()
        
        if not invoice:
            return jsonify({"error": "Invoice not found"}), 404

        order_id = invoice['order_id']
        expected_amount = invoice['amount']
        current_status = invoice['status']

        # 5. IDEMPOTENSI (Sesuai PDF Halaman 4)
        # Jika status sudah LUNAS, abaikan proses, tapi tetap return 200 OK agar gateway tidak retry terus.
        if current_status == 'LUNAS':
            print(f"[Idempotency] Webhook diabaikan, invoice {external_id} sudah LUNAS.")
            return jsonify({"status": "ignored_already_paid"}), 200

        # 6. Validasi Bisnis
        if status == 'PAID':
            if amount_paid < expected_amount:
                # Sesuai PDF: "PERLU PEMERIKSAAN" jika nominal tidak cocok
                cursor.execute(
                    "UPDATE invoices SET status = 'PERLU PEMERIKSAAN' WHERE external_id = %s", 
                    (external_id,)
                )
                conn.commit()
                log_audit(order_id, "WEBHOOK_AMOUNT_MISMATCH", f"Expected: {expected_amount}, Got: {amount_paid}")
                return jsonify({"status": "amount_mismatch"}), 200

            # 7. Update Status ke LUNAS secara Atomik
            cursor.execute(
                """UPDATE invoices 
                   SET status = 'LUNAS', 
                       gateway_payment_id = %s, 
                       paid_at = NOW(), 
                       payment_method = %s 
                   WHERE external_id = %s AND status != 'LUNAS'""",
                (gateway_payment_id, payment_method, external_id)
            )
            conn.commit()
            
            # 8. Audit Log
            log_audit(order_id, "PAYMENT_VERIFIED", f"Gateway ID: {gateway_payment_id}")

            # 9. Trigger Notifikasi WhatsApp (Template B & E)
            # Ambil data pelanggan untuk notifikasi
            cursor.execute("SELECT customer_name, customer_phone FROM orders WHERE id = %s", (order_id,))
            customer = cursor.fetchone()
            
            wa_data = {
                "nama": customer['customer_name'],
                "order_id": order_id,
                "invoice_id": external_id,
                "nominal": amount_paid,
                "waktu_bayar": payload.get('paid_at', 'Baru saja'),
                "metode": payment_method,
                "kuitansi_link": f"{os.getenv('APP_URL')}/receipt/{external_id}"
            }
            
            # Kirim ke Pelanggan (Template B)
            send_whatsapp_notification(customer['customer_phone'], "B", wa_data)
            
            # Kirim ke Internal Pak Yudi/Finance (Template E)
            internal_phone = os.getenv("INTERNAL_COORDINATOR_PHONE") # No HP Pak Yudi/Finance
            send_whatsapp_notification(internal_phone, "E", wa_data)

            return jsonify({"status": "success", "message": "Payment verified and notifications queued"}), 200

        elif status == 'EXPIRED':
            cursor.execute("UPDATE invoices SET status = 'KEDALUWARSA' WHERE external_id = %s", (external_id,))
            conn.commit()
            # Opsional: Kirim Template D
            return jsonify({"status": "expired"}), 200

        return jsonify({"status": "ignored"}), 200

    except Exception as e:
        conn.rollback()
        print(f"[Webhook Error] {str(e)}")
        return jsonify({"error": "Internal server error"}), 500
    finally:
        cursor.close()
        conn.close()