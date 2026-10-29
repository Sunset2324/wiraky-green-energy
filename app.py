import os
from flask import Flask, jsonify, render_template
from dotenv import load_dotenv

# 1. Load Environment Variables dari file .env
load_dotenv()

# 2. Inisialisasi Aplikasi Flask (HARUS di awal sebelum digunakan)
app = Flask(__name__)

# Konfigurasi Dasar
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'default-secret-key-change-me')
app.config['JSON_AS_ASCII'] = False  # Agar karakter Indonesia (UTF-8) aman di JSON

# Data Dummy Master Price (Nanti ambil dari Database)
MASTER_PRICE = {
    "PLTS_01": {"name": "Panel Surya Extra", "price": 5000000},
    "INV_01": {"name": "Inverter Upgrade", "price": 3000000}
}

# 3. Import Routes (Blueprints)
from routes.webhook_routes import webhook_bp
from routes.api_routes import api_bp
from routes.dashboard_routes import dashboard_bp

# 4. Registrasi Blueprint ke Aplikasi Utama
app.register_blueprint(webhook_bp, url_prefix='/webhook')
app.register_blueprint(api_bp, url_prefix='/api')
app.register_blueprint(dashboard_bp, url_prefix='/dashboard')

# 5. Route Halaman Surveyor (Sesuai PDF Halaman 1)
@app.route('/')
def surveyor_page():
    return render_template('surveyor_input.html', 
                           customer_name="Budi Santoso", 
                           master_price=MASTER_PRICE)

# 6. Health Check Endpoint (untuk monitoring uptime)
@app.route('/health')
def health_check():
    return jsonify({
        "status": "online", 
        "service": "Wiraky Green Energy Payment System",
        "message": "Sistem pembayaran Additional Cost siap digunakan."
    })

# 7. Error Handling Global
@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint tidak ditemukan"}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Terjadi kesalahan pada server"}), 500

# 8. Entry Point untuk Development Lokal
if __name__ == '__main__':
    app.run(debug=True, port=5000)