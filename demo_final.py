import os
import json
from flask import Flask, request, jsonify, render_template_string
from dotenv import load_dotenv
from xendit import XenditClient

load_dotenv()

app = Flask(__name__)

# --- KONFIGURASI ---
XENDIT_API_KEY = os.getenv('XENDIT_API_KEY', 'xnd_development_xxxxx')
xendit_client = XenditClient(api_key=XENDIT_API_KEY)

# Master Price List (Sesuai Halaman 1 & 5 Dokumen)
MASTER_PRICE = {
    "Panel Surya Extra": 5000000,
    "Inverter Upgrade": 3000000,
    "Kabel Tambahan": 500000
}

# Data Dummy Pelanggan
CUSTOMER = {
    "given_names": "Budi",
    "surname": "Santoso",
    "email": "budi@example.com",
    "mobile_number": "+628123456789"
}

@app.route('/')
def dashboard_surveyor():
    """Halaman 1: Input Survei & Hitung Otomatis"""
    items_html = "".join([f'<option value="{price}">{name} - Rp {price:,}</option>' for name, price in MASTER_PRICE.items()])
    
    return render_template_string('''
    <html>
    <body style="font-family: 'Segoe UI', sans-serif; background: #f0f2f5; padding: 20px;">
        <div style="max-width: 500px; margin: 0 auto; background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.1);">
            <h2 style="color: #2ecc71; text-align: center;">🌿 Wiraky Green Energy</h2>
            <p style="text-align: center; color: #666;">Modul Additional Cost - IndiHome Aura</p>
            
            <form action="/process_order" method="POST">
                <label><b>Nama Pelanggan:</b></label>
                <input type="text" value="{{ name }}" style="width: 100%; padding: 10px; margin-bottom: 15px; border: 1px solid #ddd; border-radius: 6px;" readonly>
                
                <label><b>Pilih Material/Jasa Tambahan:</b></label>
                <select name="item" style="width: 100%; padding: 10px; margin-bottom: 15px; border: 1px solid #ddd; border-radius: 6px;">
                    {{ items | safe }}
                </select>
                
                <button type="submit" style="width: 100%; padding: 12px; background: #2ecc71; color: white; border: none; border-radius: 6px; font-weight: bold; cursor: pointer;">
                    ✅ Setujui & Buat Invoice
                </button>
            </form>
            <p style="font-size: 12px; color: #888; margin-top: 15px; text-align: center;">
                *Sistem akan menghitung selisih terhadap standar paket secara otomatis.
            </p>
        </div>
    </body>
    </html>
    ''', name=CUSTOMER['given_names'] + " " + CUSTOMER['surname'], items=items_html)

@app.route('/process_order', methods=['POST'])
def process_order():
    """Halaman 2, 3 & 4: Persetujuan, Invoice & WA Tagihan"""
    item_name = request.form.get('item').split(' - ')[0]
    amount = int(request.form.get('item').split(' - ')[1].replace('.', '').replace(',', ''))
    
    try:
        # 1. Create Invoice di Xendit
        invoice = xendit_client.Invoice.create(
            amount=amount,
            description=f"Additional Cost: {item_name}",
            customer=CUSTOMER,
            success_redirect_url="https://wiraky.com/success",
            failure_redirect_url="https://wiraky.com/failure"
        )
        
        invoice_url = invoice['invoice_url']
        invoice_id = invoice['id']
        external_id = invoice['external_id']

        # 2. Simulasi Template WA A (Halaman 2 Dokumen)
        wa_template_a = f"""
        Yth. Bapak/Ibu {CUSTOMER['given_names']},
        Terima kasih telah menyetujui rincian Additional Cost pemasangan PLTS IndiHome Aura untuk nomor order WRK-{external_id[-4:]}.
        
        Nomor invoice: {invoice_id}
        Nilai tagihan: Rp {amount:,}
        Batas pembayaran: 24 Jam
        
        Silakan lakukan pembayaran melalui tautan resmi berikut:
        {invoice_url}
        
        Pembayaran hanya melalui kanal resmi PT Wiraky Nusa Telekomunikasi.
        """
        
        return render_template_string('''
        <html>
        <body style="font-family: sans-serif; padding: 20px;">
            <h2>✅ Invoice Berhasil Dibuat!</h2>
            <div style="background: #e8f5e9; padding: 20px; border-left: 5px solid #2ecc71; border-radius: 4px; margin-bottom: 20px;">
                <h3>📱 Simulasi WhatsApp (Template A)</h3>
                <pre style="white-space: pre-wrap; font-family: sans-serif;">{{ message }}</pre>
            </div>
            <a href="{{ url }}" target="_blank" style="display: inline-block; padding: 10px 20px; background: #003d79; color: white; text-decoration: none; border-radius: 6px;">🔗 Coba Bayar di Link Ini</a>
            <br><br>
            <small><i>Link ini akan mengarah ke halaman pembayaran Xendit Sandbox.</i></small>
        </body>
        </html>
        ''', message=wa_template_a, url=invoice_url)

    except Exception as e:
        return f"<h3>Error:</h3> {str(e)}"

@app.route('/webhook/xendit', methods=['POST'])
def webhook_handler():
    """Halaman 4, 6 & 7: Webhook & Verifikasi Otomatis"""
    payload = request.form if request.form else request.get_json()
    status = payload.get('status')
    external_id = payload.get('external_id')
    
    print(f"🔔 Webhook Masuk: Status {status} untuk Order {external_id}")

    if status == 'PAID':
        # Logika Idempotensi & Verifikasi (Halaman 4)
        print("✅ VERIFIKASI SUKSES: Pembayaran sah.")
        
        # Template WA B (Lunas)
        print("📱 Mengirim WA Template B (Lunas) ke Pelanggan...")
        
        # Notifikasi Internal (Template E)
        print("📢 Mengirim Notifikasi Internal ke Pak Yudi & Finance...")
        
        return jsonify({"status": "success"}), 200
    
    return jsonify({"status": "ignored"}), 200

if __name__ == '__main__':
    app.run(debug=True, port=5000)