"""
API Routes untuk Green Energy - STANDAR API INTEGRATED v1.0
Endpoint untuk mengirim Additional Cost ke Sistem Keuangan Wiraky.
"""

from flask import Blueprint, request, jsonify
from services.finance_api_client import send_additional_cost
from core.database import get_db_connection
from core.audit_logger import log_audit
from datetime import datetime, timezone
import uuid

api_bp = Blueprint('api', __name__)

# Master Price List (dummy - nanti dari database)
MASTER_PRICE = {
    "KBL-PV-004": {"name": "Kabel PV 4mm2", "unit": "meter", "price": 15000},
    "MCB-AC-002": {"name": "MCB tambahan", "unit": "pcs", "price": 125000},
    "JASA-001": {"name": "Instalasi khusus", "unit": "paket", "price": 225000},
    "PLTS-01": {"name": "Panel Surya Extra", "unit": "unit", "price": 5000000},
    "INV-01": {"name": "Inverter Upgrade", "unit": "unit", "price": 3000000}
}


@api_bp.route('/v1/additional-costs', methods=['POST'])
def submit_additional_cost():
    """
    Endpoint untuk mengirim Additional Cost yang sudah disetujui ke Finance Wiraky.
    Sesuai STANDAR_API_INTEGRATED v1.0 - Halaman 4 & 5.
    
    Payload dari frontend:
    {
        "order_id": "WIR-2026-0001",
        "customer_name": "Pelanggan Contoh",
        "customer_phone": "+62XXXXXXXXXXX",
        "package": "IndiHome Aura 3500",
        "items": [
            {"part_code": "KBL-PV-004", "description": "Kabel PV 4mm2", "qty": 15, "unit": "meter", "unit_price": 15000}
        ],
        "approval_reference": "APR-2026-0001"
    }
    """
    data = request.get_json()
    
    # Validasi input minimum
    required_fields = ['order_id', 'customer_name', 'customer_phone', 'items', 'approval_reference']
    if not all(field in data for field in required_fields):
        return jsonify({"error": "Missing required fields"}), 400
    
    items = data.get('items', [])
    if not items:
        return jsonify({"error": "No items provided"}), 400
    
    # Hitung total dan validasi items
    additional_items = []
    total_amount = 0
    
    for item in items:
        part_code = item.get('part_code')
        if part_code not in MASTER_PRICE:
            return jsonify({"error": f"Invalid part_code: {part_code}"}), 400
        
        qty = item.get('qty', 0)
        unit_price = item.get('unit_price', MASTER_PRICE[part_code]['price'])
        subtotal = qty * unit_price
        
        additional_items.append({
            "part_code": part_code,
            "description": item.get('description', MASTER_PRICE[part_code]['name']),
            "qty": qty,
            "unit": item.get('unit', MASTER_PRICE[part_code]['unit']),
            "unit_price": unit_price,
            "subtotal": subtotal
        })
        
        total_amount += subtotal
    
    # Business rule: jika total = 0, tidak kirim ke Finance
    if total_amount <= 0:
        return jsonify({
            "result": "NOT_REQUIRED",
            "message": "Total additional cost is 0, no billing needed"
        }), 200
    
    # Generate IDs
    order_id = data['order_id']
    additional_cost_id = f"AC-{datetime.now().year}-{uuid.uuid4().hex[:8].upper()}"
    revision = 1
    event_id = f"EVT-AC-{datetime.now().year}-{uuid.uuid4().hex[:8].upper()}-R{revision}"
    
    # Simpan ke database dulu (status APPROVED)
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cursor = conn.cursor()
        
        # Simpan order jika belum ada
        cursor.execute(
            """INSERT IGNORE INTO orders (id, customer_name, customer_phone, package_name, status) 
               VALUES (%s, %s, %s, %s, 'IN_PROGRESS')""",
            (order_id, data['customer_name'], data['customer_phone'], data.get('package', 'IndiHome Aura'))
        )
        
        # Simpan additional cost
        import json
        cursor.execute(
            """INSERT INTO additional_costs 
               (order_id, additional_cost_id, revision, items_json, total_amount, currency,
                approval_status, approval_reference, approved_at, status, event_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s)""",
            (
                order_id,
                additional_cost_id,
                revision,
                json.dumps(additional_items),
                total_amount,
                'IDR',
                'APPROVED',
                data['approval_reference'],
                'APPROVED',
                event_id
            )
        )
        
        conn.commit()
        
        # Log audit
        log_audit(
            order_id,
            "ADDITIONAL_COST_APPROVED",
            f"AC ID: {additional_cost_id}, Amount: {total_amount}, Items: {len(items)}"
        )
        
    except Exception as e:
        conn.rollback()
        return jsonify({"error": f"Database error: {str(e)}"}), 500
    finally:
        cursor.close()
        conn.close()
    
    # Siapkan payload untuk Finance API
    payload = {
        "schema_version": "1.0",
        "event_id": event_id,
        "event_type": "ADDITIONAL_COST_APPROVED",
        "occurred_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "GREEN_ENERGY",
        "order_id": order_id,
        "additional_cost_id": additional_cost_id,
        "revision": revision,
        "customer": {
            "name": data['customer_name'],
            "whatsapp": data['customer_phone'],
            "package": data.get('package', 'IndiHome Aura')
        },
        "additional_items": additional_items,
        "total_amount": total_amount,
        "currency": "IDR",
        "approval": {
            "status": "APPROVED",
            "reference": data['approval_reference'],
            "approved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        },
        "payment_status": "NOT_BILLED"
    }
    
    # Kirim ke Finance API
    result = send_additional_cost(payload)
    
    # Update status berdasarkan response
    conn = get_db_connection()
    if conn:
        try:
            cursor = conn.cursor()
            
            if result.get('success'):
                # Update ke RECEIVED
                cursor.execute(
                    """UPDATE additional_costs 
                       SET status = 'RECEIVED', 
                           finance_reference = %s,
                           last_sent_at = NOW(),
                           last_response_code = 202
                       WHERE additional_cost_id = %s AND revision = %s""",
                    (result.get('finance_reference'), additional_cost_id, revision)
                )
                
                log_audit(
                    order_id,
                    "ADDITIONAL_COST_SENT",
                    f"Finance Ref: {result.get('finance_reference')}"
                )
            else:
                # Update status FAILED
                cursor.execute(
                    """UPDATE additional_costs 
                       SET status = 'FAILED',
                           last_sent_at = NOW(),
                           last_response_code = %s,
                           retry_count = retry_count + 1
                       WHERE additional_cost_id = %s AND revision = %s""",
                    (result.get('status_code'), additional_cost_id, revision)
                )
                
                log_audit(
                    order_id,
                    "ADDITIONAL_COST_SEND_FAILED",
                    f"Error: {result.get('error')}, Message: {result.get('message')}"
                )
            
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            print(f"[DB Update Error] {str(e)}")
    
    # Return response
    if result.get('success'):
        return jsonify({
            "success": True,
            "order_id": order_id,
            "additional_cost_id": additional_cost_id,
            "finance_reference": result.get('finance_reference'),
            "status": "RECEIVED",
            "message": result.get('message')
        }), 202
    else:
        return jsonify({
            "success": False,
            "error": result.get('error'),
            "message": result.get('message'),
            "status_code": result.get('status_code')
        }), result.get('status_code') or 500


@api_bp.route('/v1/additional-costs/<additional_cost_id>/status', methods=['GET'])
def get_additional_cost_status(additional_cost_id):
    """
    Endpoint untuk cek status Additional Cost (untuk rekonsiliasi).
    Sesuai PDF Halaman 3: "GET status pembayaran untuk rekonsiliasi"
    """
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """SELECT ac.*, o.customer_name, o.customer_phone
               FROM additional_costs ac
               JOIN orders o ON ac.order_id = o.id
               WHERE ac.additional_cost_id = %s
               ORDER BY ac.revision DESC
               LIMIT 1""",
            (additional_cost_id,)
        )
        
        ac = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if not ac:
            return jsonify({"error": "Additional cost not found"}), 404
        
        return jsonify({
            "additional_cost_id": ac['additional_cost_id'],
            "order_id": ac['order_id'],
            "status": ac['status'],
            "total_amount": float(ac['total_amount']),
            "currency": ac['currency'],
            "finance_reference": ac['finance_reference'],
            "invoice_id": ac['invoice_id'],
            "payment_reference": ac['payment_reference'],
            "paid_at": str(ac['paid_at']) if ac['paid_at'] else None
        }), 200
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route('/v1/health', methods=['GET'])
def api_health():
    """Health check untuk API"""
    return jsonify({
        "status": "healthy",
        "service": "Green Energy API",
        "version": "1.0 - STANDAR_API_INTEGRATED"
    }), 200