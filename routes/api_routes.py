from flask import Blueprint, request, jsonify, render_template
from services.gateway_service import create_payment_link, check_payment_status
from services.whatsapp_service import send_whatsapp_notification
from core.database import get_db_connection
from core.audit_logger import log_audit
import os

api_bp = Blueprint('api', __name__)

# Data Dummy Master Price (Nanti ambil dari Database)
MASTER_PRICE = {
    "PLTS_01": {"name": "Panel Surya Extra", "price": 5000000},
    "INV_01": {"name": "Inverter Upgrade", "price": 3000000}
}


@api_bp.route('/create_invoice', methods=['POST'])
def api_create_invoice():
    """
    Endpoint untuk membuat invoice setelah pelanggan menyetujui.
    Sesuai PDF Halaman 4 step 04: "Sistem menerbitkan invoice Wiraky dan mengirim tautan pembayaran"
    """
    data = request.get_json()
    
    # Validasi input
    required_fields = ['order_id', 'customer_name', 'customer_phone', 'item_code']
    if not all(field in data for field in required_fields):
        return jsonify({"error": "Missing required fields"}), 400
    
    order_id = data['order_id']
    customer_name = data['customer_name']
    customer_phone = data['customer_phone']
    item_code = data['item_code']
    
    # Validasi item_code ada di Master Price List
    if item_code not in MASTER_PRICE:
        return jsonify({"error": "Invalid item code"}), 400
    
    item_info = MASTER_PRICE[item_code]
    amount = item_info['price']
    
    # Buat invoice di Xendit
    result = create_payment_link(
        order_id=order_id,
        customer_name=customer_name,
        customer_phone=customer_phone,
        amount=amount,
        item_desc=item_info['name']
    )
    
    if not result['success']:
        return jsonify({"error": "Failed to create invoice", "details": result.get('error')}), 500
    
    # Simpan ke database (simulasi - nanti ganti dengan query INSERT)
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO invoices (order_id, external_id, amount, status, created_at) 
               VALUES (%s, %s, %s, 'MENUNGGU PEMBAYARAN', NOW())""",
            (order_id, result['external_id'], amount)
        )
        conn.commit()
        cursor.close()
        conn.close()
    
    # Kirim WhatsApp Template A (sesuai PDF Halaman 2)
    wa_data = {
        "nama": customer_name,
        "order_id": order_id,
        "invoice_id": result['external_id'],
        "nominal": amount,
        "expired_at": result.get('expiry_date', '24 jam'),
        "payment_link": result['invoice_url'],
        "status_link": f"{os.getenv('APP_URL')}/status/{order_id}"
    }
    
    wa_sent = send_whatsapp_notification(customer_phone, "A", wa_data)
    
    # Log audit
    log_audit(order_id, "INVOICE_CREATED", f"Invoice {result['external_id']} created for {amount}")
    
    return jsonify({
        "success": True,
        "invoice_id": result['external_id'],
        "invoice_url": result['invoice_url'],
        "amount": amount,
        "whatsapp_sent": wa_sent
    }), 201


@api_bp.route('/check_status/<order_id>', methods=['GET'])
def api_check_status(order_id):
    """
    Endpoint untuk cek status pembayaran.
    Sesuai PDF Halaman 4 poin 6: "Job rekonsiliasi memeriksa status ke API gateway"
    """
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM invoices WHERE order_id = %s", (order_id,))
    invoice = cursor.fetchone()
    cursor.close()
    conn.close()
    
    if not invoice:
        return jsonify({"error": "Invoice not found"}), 404
    
    # Jika status masih MENUNGGU, cek langsung ke gateway (rekonsiliasi)
    if invoice['status'] == 'MENUNGGU PEMBAYARAN':
        gateway_status = check_payment_status(invoice['external_id'])
        
        if gateway_status == 'PAID':
            # Update status di database
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE invoices SET status = 'LUNAS', paid_at = NOW() WHERE order_id = %s",
                (order_id,)
            )
            conn.commit()
            cursor.close()
            conn.close()
            
            invoice['status'] = 'LUNAS'
            log_audit(order_id, "REKONSILIASI_PAID", "Payment confirmed via reconciliation")
    
    return jsonify({
        "order_id": order_id,
        "status": invoice['status'],
        "amount": invoice['amount'],
        "created_at": str(invoice['created_at'])
    }), 200


@api_bp.route('/resend_whatsapp', methods=['POST'])
def api_resend_whatsapp():
    """
    Endpoint untuk mengirim ulang WhatsApp jika gagal.
    Sesuai PDF Halaman 3: "sistem mencatat kegagalan dan menyediakan kirim ulang terkendali"
    """
    data = request.get_json()
    
    if 'order_id' not in data or 'template_type' not in data:
        return jsonify({"error": "Missing order_id or template_type"}), 400
    
    order_id = data['order_id']
    template_type = data['template_type']
    
    # Ambil data invoice dan pelanggan
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """SELECT i.*, o.customer_name, o.customer_phone 
           FROM invoices i 
           JOIN orders o ON i.order_id = o.id 
           WHERE i.order_id = %s""",
        (order_id,)
    )
    invoice = cursor.fetchone()
    cursor.close()
    conn.close()
    
    if not invoice:
        return jsonify({"error": "Invoice not found"}), 404
    
    # Siapkan data WhatsApp
    wa_data = {
        "nama": invoice['customer_name'],
        "order_id": order_id,
        "invoice_id": invoice['external_id'],
        "nominal": invoice['amount'],
        "payment_link": f"{os.getenv('APP_URL')}/pay/{invoice['external_id']}",
        "status_link": f"{os.getenv('APP_URL')}/status/{order_id}"
    }
    
    # Kirim ulang
    wa_sent = send_whatsapp_notification(invoice['customer_phone'], template_type, wa_data)
    
    log_audit(order_id, "WHATSAPP_RESENT", f"Template {template_type} resent")
    
    return jsonify({
        "success": wa_sent,
        "message": "WhatsApp resent" if wa_sent else "Failed to resend WhatsApp"
    }), 200 if wa_sent else 500