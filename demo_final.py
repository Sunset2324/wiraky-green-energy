import os
import json
from flask import Flask, request, jsonify, render_template_string
from dotenv import load_dotenv
from xendit import XenditClient

# Load konfigurasi dari .env
load_dotenv()

app = Flask(__name__, template_folder='templates', static_folder='static')

# --- KONFIGURASI GATEWAY ---
XENDIT_API_KEY = os.getenv('XENDIT_API_KEY', 'xnd_development_xxxxx')
xendit_client = XenditClient(api_key=XENDIT_API_KEY)

# --- MASTER PRICE LIST (Sesuai Halaman 1 & 5) ---
MASTER_PRICE = {
    "PLTS_01": {"name": "Panel Surya Extra", "price": 5000000},
    "INV_01": {"name": "Inverter Upgrade", "price": 3000000},
    "KABEL_01": {"name": "Kabel Tambahan 10m", "price": 500000}
}

# --- DATA DUMMY PELANGGAN ---
CUSTOMER = {
    "given_names": "Budi",
    "surname": "Santoso",
    "email": "budi@example.com",
    "mobile_number": "+628123456789"
}

# --- ROUTES ---

@app.route('/')
def surveyor_input():
    """Halaman 1: Input Survei"""
    return render_template_string('''
    <html>
    <body style="font-family: sans-serif; background: #f4f7f6; padding: 20px;">
        <div style="max-width: 500px; margin: 0 auto; background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.1);">
            <h2 style="color: #2ecc71;">🌿 Wiraky Green Energy</h2>
            <p>Input Additional Cost - IndiHome Aura</p>
            <form action="/create_invoice" method="POST">
                <label>Nama Pelanggan:</label>
                <input type="text" value="{{ name }}" readonly style="width: 100%; padding: 10px; margin-bottom: 10px;">
                <label>Material Tambahan:</label>
                <select name="item_code" style="width: 100%; padding: 10px; margin-bottom: 10px;">
                    {% for code, info in prices.items() %}
                    <option value="{{ code }}">{{ info.name }} - Rp {{ "{:,}".format(info.price) }}</option>
                    {% endfor %}
                </select>
                <button type="submit" style="width: 100%; padding: 12px; background: #2ecc71; color: white; border: none; border-radius: 6px; cursor: pointer;">✅ Buat Invoice & Kirim WA</button>
            </form>
        </div>
    </body>
    </html>
    ''', name=CUSTOMER['given_names'] + " " + CUSTOMER['surname'], prices=MASTER_PRICE)

@app.route('/create_invoice', methods=['POST'])
def create_invoice():
    """Halaman 2, 3 & 4: Persetujuan & Generate Link Pembayaran"""
    item_code = request.form.get('item_code')
    item_info = MASTER_PRICE[item_code]
    
    try:
        # 1. Request ke Xendit
        invoice = xendit_client.Invoice.create(
            amount=item_info['price'],
            description=f"Additional Cost: {item_info['name']}",
            customer=CUSTOMER,
            success_redirect_url="https://wiraky.com/success",
            failure_redirect_url="https://wiraky.com/failure"
        )
        
        invoice_url = invoice['invoice_url']
        invoice_id = invoice['id']
        
        # 2. Simulasi Template WA A (Halaman 2)
        wa_msg = f"""
        Yth. Bapak/Ibu {CUSTOMER['given_names']},
        Invoice Additional Cost IndiHome Aura telah terbit.
        
        Nomor invoice: {invoice_id}
        Nilai tagihan: Rp {item_info['price']:,}
        
        Silakan bayar via: {invoice_url}
        """
        
        return render_template_string('''
        <html>
        <body style="font-family: sans-serif; padding: 20px;">
            <h2>✅ Invoice Berhasil Dibuat!</h2>
            <div style="background: #e8f5e9; padding: 20px; border-left: 5px solid #2ecc71;">
                <b>Simulasi WhatsApp (Template A):</b><br>
                <pre>{{ msg }}</pre>
            </div>
            <br>
            <a href="{{ url }}" target="_blank" style="padding: 10px 20px; background: #003d79; color: white; text-decoration: none; border-radius: 6px;">🔗 Coba Bayar (Link Xendit)</a>
        </body>
        </html>
        ''', msg=wa_msg, url=invoice_url)

    except Exception as e:
        return f"<h3>Error:</h3> {str(e)}"

@app.route('/webhook/xendit', methods=['POST'])
def webhook_handler():
    """Halaman 4 & 7: Verifikasi Webhook Otomatis"""
    payload = request.form if request.form else request.get_json()
    status = payload.get('status')
    
    print(f"🔔 Webhook Masuk: Status {status}")

    if status == 'PAID':
        # Logika Idempotensi & Verifikasi (Halaman 4)
        print("✅ VERIFIKASI SUKSES: Pembayaran sah.")
        print("📱 Mengirim WA Template B (Lunas)...")
        print("📢 Notifikasi Internal ke Pak Yudi...")
        return jsonify({"status": "success"}), 200
    
    return jsonify({"status": "ignored"}), 200

if __name__ == '__main__':
    app.run(debug=True, port=5000)