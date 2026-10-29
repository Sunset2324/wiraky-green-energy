import os
from flask import Flask, jsonify, render_template
from dotenv import load_dotenv

# 1. Load Environment Variables
load_dotenv()

# 2. Inisialisasi Flask
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'default-secret-key-change-me')
app.config['JSON_AS_ASCII'] = False

# Data Dummy Master Price
MASTER_PRICE = {
    "KBL-PV-004": {"name": "Kabel PV 4mm2", "price": 15000},
    "MCB-AC-002": {"name": "MCB tambahan", "price": 125000},
    "JASA-001": {"name": "Instalasi khusus", "price": 225000},
    "PLTS-01": {"name": "Panel Surya Extra", "price": 5000000},
    "INV-01": {"name": "Inverter Upgrade", "price": 3000000}
}

# 3. Import Routes (Blueprints)
from routes.api_routes import api_bp
from routes.dashboard_routes import dashboard_bp
from routes.webhook_routes import finance_webhook_bp

# 4. Registrasi Blueprint
app.register_blueprint(finance_webhook_bp, url_prefix='/api/v1/finance')
app.register_blueprint(api_bp, url_prefix='/api')
app.register_blueprint(dashboard_bp, url_prefix='/dashboard')

# 5. Route Halaman Surveyor
@app.route('/')
def surveyor_page():
    return render_template('surveyor_input.html', 
                           customer_name="Budi Santoso", 
                           master_price=MASTER_PRICE)

# 6. Health Check
@app.route('/health')
def health_check():
    return jsonify({
        "status": "online", 
        "service": "Wiraky Green Energy Payment System",
        "version": "2.0 - STANDAR_API_INTEGRATED v1.0",
        "message": "Sistem pembayaran Additional Cost siap digunakan."
    })

# 7. Error Handling
@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint tidak ditemukan"}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Terjadi kesalahan pada server"}), 500

# 8. Entry Point
if __name__ == '__main__':
    app.run(debug=True, port=5000)