import os
import json
import uuid
from datetime import datetime, timezone
from flask import Flask, jsonify, render_template, request, redirect, url_for, flash, session
from dotenv import load_dotenv

# 1. Load Environment Variables
load_dotenv()

# 2. Inisialisasi Flask
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'default-secret-key-change-me')
app.config['JSON_AS_ASCII'] = False

# Data Dummy Master Price (Sesuai STANDAR API INTEGRATED v1.0)
MASTER_PRICE = {
    "KBL-PV-004": {"name": "Kabel PV 4mm2", "unit": "meter", "price": 15000},
    "MCB-AC-002": {"name": "MCB tambahan", "unit": "pcs", "price": 125000},
    "JASA-001": {"name": "Instalasi khusus", "unit": "paket", "price": 225000},
    "PLTS-01": {"name": "Panel Surya Extra", "unit": "unit", "price": 5000000},
    "INV-01": {"name": "Inverter Upgrade", "unit": "unit", "price": 3000000}
}

# 3. Import Services & Routes
from services.finance_api_client import send_additional_cost
from core.audit_logger import log_audit
from routes.api_routes import api_bp
from routes.dashboard_routes import dashboard_bp
from routes.webhook_routes import finance_webhook_bp

# 4. Registrasi Blueprint
app.register_blueprint(finance_webhook_bp, url_prefix='/api/v1/finance')
app.register_blueprint(api_bp, url_prefix='/api')
app.register_blueprint(dashboard_bp, url_prefix='/dashboard')

# ==========================================
# 5. ROUTE: Halaman Surveyor (Step 01)
# ==========================================
@app.route('/')
def surveyor_page():
    return render_template('surveyor_input.html', 
                           customer_name="Budi Santoso", 
                           master_price=MASTER_PRICE)

# ==========================================
# 6. ROUTE: Proses & Hitung (Step 02)
# ==========================================
@app.route('/process_order', methods=['POST'])
def process_order():
    """Menerima form dari surveyor, validasi, dan simpan ke session untuk review."""
    customer_name = request.form.get('customer_name', '').strip()
    order_id = request.form.get('order_id', '').strip()
    item_code = request.form.get('item_code', '').strip()
    qty = int(request.form.get('qty', 1))
    reason = request.form.get('reason', '').strip()
    customer_phone = request.form.get('customer_phone', '').strip()
    package = request.form.get('package', 'IndiHome Aura 3500').strip()
    
    # Validasi input dasar
    if not all([customer_name, order_id, item_code, customer_phone]):
        flash("Semua field wajib harus diisi!", "error")
        return redirect(url_for('surveyor_page'))
    
    if item_code not in MASTER_PRICE:
        flash("Kode material tidak valid!", "error")
        return redirect(url_for('surveyor_page'))
    
    # Hitung otomatis sesuai Master Price List
    item_info = MASTER_PRICE[item_code]
    unit_price = item_info['price']
    subtotal = qty * unit_price
    
    # Simpan ke session untuk halaman review
    session['pending_order'] = {
        'customer_name': customer_name,
        'customer_phone': customer_phone,
        'order_id': order_id,
        'package': package,
        'reason': reason,
        'items': [{
            'part_code': item_code,
            'description': item_info['name'],
            'qty': qty,
            'unit': item_info.get('unit', 'unit'),
            'unit_price': unit_price,
            'subtotal': subtotal
        }],
        'total_amount': subtotal
    }
    
    return redirect(url_for('review_order'))

# ==========================================
# 7. ROUTE: Review & Persetujuan (Step 03)
# ==========================================
@app.route('/review')
def review_order():
    """Menampilkan rincian untuk disetujui Engineering & Pelanggan."""
    pending = session.get('pending_order')
    if not pending:
        flash("Tidak ada data order yang pending!", "error")
        return redirect(url_for('surveyor_page'))
    
    return render_template('review_order.html', order=pending)

# ==========================================
# 8. ROUTE: Konfirmasi & Kirim ke Finance (Step 04)
# ==========================================
@app.route('/confirm_order', methods=['POST'])
def confirm_order():
    """Setelah disetujui, kirim payload ke Sistem Keuangan Wiraky via API."""
    pending = session.get('pending_order')
    if not pending:
        flash("Tidak ada data order yang pending!", "error")
        return redirect(url_for('surveyor_page'))
    
    # Generate ID unik sesuai standar
    additional_cost_id = f"AC-{datetime.now().year}-{uuid.uuid4().hex[:8].upper()}"
    revision = 1
    event_id = f"EVT-AC-{datetime.now().year}-{uuid.uuid4().hex[:8].upper()}-R{revision}"
    approval_reference = f"APR-{datetime.now().year}-{uuid.uuid4().hex[:8].upper()}"
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    # Siapkan payload sesuai Schema v1.0
    payload = {
        "schema_version": "1.0",
        "event_id": event_id,
        "event_type": "ADDITIONAL_COST_APPROVED",
        "occurred_at": now_utc,
        "source": "GREEN_ENERGY",
        "order_id": pending['order_id'],
        "additional_cost_id": additional_cost_id,
        "revision": revision,
        "customer": {
            "name": pending['customer_name'],
            "whatsapp": pending['customer_phone'],
            "package": pending['package']
        },
        "additional_items": pending['items'],
        "total_amount": pending['total_amount'],
        "currency": "IDR",
        "approval": {
            "status": "APPROVED",
            "reference": approval_reference,
            "approved_at": now_utc
        },
        "payment_status": "NOT_BILLED"
    }
    
    # Kirim ke Finance API
    result = send_additional_cost(payload)
    
    # Log audit
    log_audit(
        pending['order_id'],
        "ADDITIONAL_COST_SUBMITTED",
        f"AC ID: {additional_cost_id}, Amount: {pending['total_amount']}, Result: {result.get('success')}"
    )
    
    # Clear session
    session.pop('pending_order', None)
    
    # Tampilkan hasil
    return render_template('order_result.html', 
                           result=result, 
                           order_id=pending['order_id'],
                           additional_cost_id=additional_cost_id)

# ==========================================
# 9. Health Check & Error Handling
# ==========================================
@app.route('/health')
def health_check():
    return jsonify({
        "status": "online", 
        "service": "Wiraky Green Energy Payment System",
        "version": "2.0 - STANDAR_API_INTEGRATED v1.0",
        "message": "Sistem pembayaran Additional Cost siap digunakan."
    })

@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint tidak ditemukan"}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Terjadi kesalahan pada server"}), 500

# 10. Entry Point
if __name__ == '__main__':
    app.run(debug=True, port=5000)