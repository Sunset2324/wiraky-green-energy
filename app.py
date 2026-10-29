import os
import sys
from flask import Flask
from dotenv import load_dotenv

# 1. Load Environment Variables dari file .env
load_dotenv()

# 2. Inisialisasi Aplikasi Flask
app = Flask(__name__)

# Konfigurasi Dasar
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'default-secret-key-change-me')
app.config['JSON_AS_ASCII'] = False # Agar karakter Indonesia (UTF-8) aman di JSON

# 3. Import Routes (Blueprints)
# Kita import di sini agar struktur folder tetap rapi
from routes.webhook_routes import webhook_bp
from routes.api_routes import api_bp
from routes.dashboard_routes import dashboard_bp

# 4. Registrasi Blueprint ke Aplikasi Utama
app.register_blueprint(webhook_bp, url_prefix='/webhook')
app.register_blueprint(api_bp, url_prefix='/api')
app.register_blueprint(dashboard_bp, url_prefix='/dashboard')

# 5. Route Default (Health Check)
@app.route('/')
def home():
    return {
        "status": "online", 
        "service": "Wiraky Green Energy Payment System",
        "message": "Sistem pembayaran Additional Cost siap digunakan."
    }

# 6. Error Handling Global
@app.errorhandler(404)
def not_found(error):
    return {"error": "Endpoint tidak ditemukan"}, 404

@app.errorhandler(500)
def internal_error(error):
    return {"error": "Terjadi kesalahan pada server"}, 500

# 7. Entry Point untuk Development Lokal
if __name__ == '__main__':
    app.run(debug=True, port=5000)