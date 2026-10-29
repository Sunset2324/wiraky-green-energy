from flask import Blueprint, render_template, request, jsonify, redirect, url_for
from core.database import get_db_connection
from core.audit_logger import log_audit
from datetime import datetime
import os

dashboard_bp = Blueprint('dashboard', __name__, template_folder='../templates')


@dashboard_bp.route('/')
def dashboard_home():
    """
    Halaman utama dashboard internal.
    Sesuai PDF Halaman 5: Monitoring status order dan invoice.
    """
    conn = get_db_connection()
    if not conn:
        # Fallback ke dummy data jika database belum tersedia
        dummy_orders = [
            {"order_id": "WRK-001", "customer_name": "Budi S.", "amount": 5000000, "status": "LUNAS"},
            {"order_id": "WRK-002", "customer_name": "Siti A.", "amount": 3000000, "status": "MENUNGGU PEMBAYARAN"},
            {"order_id": "WRK-003", "customer_name": "Ahmad R.", "amount": 4500000, "status": "DRAFT"},
        ]
        return render_template('internal_dashboard.html', orders=dummy_orders)
    
    try:
        cursor = conn.cursor(dictionary=True)
        
        # Query untuk mengambil semua order dengan status terbaru
        cursor.execute("""
            SELECT 
                o.id as order_id,
                o.customer_name,
                COALESCE(i.amount, 0) as amount,
                COALESCE(i.status, 'DRAFT') as status,
                o.created_at
            FROM orders o
            LEFT JOIN invoices i ON o.id = i.order_id
            ORDER BY o.created_at DESC
            LIMIT 100
        """)
        
        orders = cursor.fetchall()
        cursor.close()
        conn.close()
        
        return render_template('internal_dashboard.html', orders=orders)
    
    except Exception as e:
        print(f"[Dashboard Error] {str(e)}")
        # Fallback ke dummy data
        dummy_orders = [
            {"order_id": "WRK-001", "customer_name": "Budi S.", "amount": 5000000, "status": "LUNAS"},
        ]
        return render_template('internal_dashboard.html', orders=dummy_orders)


@dashboard_bp.route('/order/<order_id>')
def order_detail(order_id):
    """
    Halaman detail order dan invoice.
    Sesuai PDF Halaman 4: Relasi Order → Invoice → Gateway Payment ID → Status → Kuitansi
    """
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cursor = conn.cursor(dictionary=True)
        
        # Ambil detail order dan invoice
        cursor.execute("""
            SELECT 
                o.id as order_id,
                o.customer_name,
                o.customer_phone,
                o.item_code,
                o.reason,
                i.external_id as invoice_id,
                i.amount,
                i.status as payment_status,
                i.gateway_payment_id,
                i.paid_at,
                i.payment_method,
                o.created_at,
                o.updated_at
            FROM orders o
            LEFT JOIN invoices i ON o.id = i.order_id
            WHERE o.id = %s
        """, (order_id,))
        
        order = cursor.fetchone()
        
        if not order:
            cursor.close()
            conn.close()
            return jsonify({"error": "Order not found"}), 404
        
        # Ambil audit log untuk order ini
        cursor.execute("""
            SELECT action, detail, actor, created_at 
            FROM audit_logs 
            WHERE order_id = %s 
            ORDER BY created_at DESC 
            LIMIT 20
        """, (order_id,))
        
        audit_logs = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        return render_template('order_detail.html', order=order, audit_logs=audit_logs)
    
    except Exception as e:
        print(f"[Order Detail Error] {str(e)}")
        return jsonify({"error": "Failed to fetch order details"}), 500


@dashboard_bp.route('/schedule/<order_id>', methods=['POST'])
def schedule_installation(order_id):
    """
    Aksi "Jadwalkan Pasang" untuk order yang sudah LUNAS.
    Sesuai PDF Halaman 5: Pak Yudi/koordinator mengatur tindak lanjut lapangan.
    """
    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "Database connection failed"}), 500
    
    try:
        cursor = conn.cursor(dictionary=True)
        
        # Cek apakah order sudah LUNAS
        cursor.execute("""
            SELECT i.status 
            FROM invoices i 
            WHERE i.order_id = %s
        """, (order_id,))
        
        invoice = cursor.fetchone()
        
        if not invoice:
            cursor.close()
            conn.close()
            return jsonify({"error": "Invoice not found"}), 404
        
        if invoice['status'] != 'LUNAS':
            cursor.close()
            conn.close()
            return jsonify({"error": "Cannot schedule: payment not verified"}), 400
        
        # Update status order ke "DIPROSES"
        cursor.execute("""
            UPDATE orders 
            SET status = 'DIPROSES', updated_at = NOW() 
            WHERE id = %s
        """, (order_id,))
        
        conn.commit()
        
        # Log audit
        log_audit(order_id, "INSTALLATION_SCHEDULED", "Installation scheduled by coordinator")
        
        cursor.close()
        conn.close()
        
        return jsonify({
            "success": True,
            "message": "Installation scheduled successfully"
        }), 200
    
    except Exception as e:
        conn.rollback()
        print(f"[Schedule Error] {str(e)}")
        return jsonify({"error": "Failed to schedule installation"}), 500
    finally:
        cursor.close()
        conn.close()


@dashboard_bp.route('/stats')
def dashboard_stats():
    """
    Endpoint untuk statistik dashboard (opsional, untuk chart/grafik).
    """
    conn = get_db_connection()
    if not conn:
        return jsonify({
            "total_orders": 0,
            "total_paid": 0,
            "total_pending": 0
        }), 200
    
    try:
        cursor = conn.cursor(dictionary=True)
        
        # Total order
        cursor.execute("SELECT COUNT(*) as total FROM orders")
        total_orders = cursor.fetchone()['total']
        
        # Total LUNAS
        cursor.execute("""
            SELECT COUNT(*) as total, COALESCE(SUM(amount), 0) as total_amount
            FROM invoices 
            WHERE status = 'LUNAS'
        """)
        paid_data = cursor.fetchone()
        total_paid = paid_data['total']
        total_amount = paid_data['total_amount']
        
        # Total MENUNGGU
        cursor.execute("""
            SELECT COUNT(*) as total 
            FROM invoices 
            WHERE status = 'MENUNGGU PEMBAYARAN'
        """)
        total_pending = cursor.fetchone()['total']
        
        cursor.close()
        conn.close()
        
        return jsonify({
            "total_orders": total_orders,
            "total_paid": total_paid,
            "total_pending": total_pending,
            "total_amount": float(total_amount)
        }), 200
    
    except Exception as e:
        print(f"[Stats Error] {str(e)}")
        return jsonify({"error": "Failed to fetch stats"}), 500