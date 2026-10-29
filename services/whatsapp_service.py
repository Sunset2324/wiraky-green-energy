import os
import requests
from datetime import datetime

# Konfigurasi WhatsApp API (Ganti dengan provider yang digunakan, misal: Fonnte/Meta)
WA_API_URL = os.getenv("WA_API_URL")
WA_API_TOKEN = os.getenv("WA_API_TOKEN")

def send_whatsapp_notification(phone: str, template_type: str, data: dict):
    """
    Mengirim pesan WhatsApp berdasarkan template yang disetujui.
    template_type: 'A', 'B', 'C', 'D', atau 'E'
    """
    phone = str(phone).replace("+", "").replace(" ", "")
    
    templates = {
        "A": f"""Yth. Bapak/Ibu {data['nama']},
Terima kasih telah menyetujui rincian Additional Cost pemasangan PLTS IndiHome Aura untuk nomor order {data['order_id']}.
Nomor invoice: {data['invoice_id']}
Nilai tagihan: Rp {data['nominal']:,}
Batas pembayaran: {data['expired_at']}

Silakan lakukan pembayaran melalui tautan resmi berikut:
{data['payment_link']}

Pembayaran hanya melalui kanal resmi PT Wiraky Nusa Telekomunikasi. Petugas lapangan dilarang meminta uang tunai/tips.
Status: {data['status_link']}""",

        "B": f"""Yth. Bapak/Ibu {data['nama']},
Pembayaran Additional Cost IndiHome Aura Anda telah BERHASIL/LUNAS dan terverifikasi oleh sistem.
Nomor order: {data['order_id']}
Nomor invoice: {data['invoice_id']}
Nominal dibayar: Rp {data['nominal']:,}
Waktu: {data['waktu_bayar']}
Metode: {data['metode']}

Kuitansi: {data['kuitansi_link']}
Tim Wiraky akan menindaklanjuti proses instalasi. Tidak ada pembayaran tambahan/tips di lokasi.""",

        "C": f"""Yth. Bapak/Ibu {data['nama']}, invoice Additional Cost {data['invoice_id']} sebesar Rp {data['nominal']:,} masih MENUNGGU PEMBAYARAN.
Silakan buka: {data['payment_link']}
Abaikan jika sudah dibayar. Jangan transfer ke rekening pribadi petugas.""",

        "D": f"""Yth. Bapak/Ibu {data['nama']}, masa berlaku invoice {data['invoice_id']} telah berakhir.
Mohon gunakan {data['status_link']} untuk meminta tagihan baru. Jika sudah membayar, hubungi layanan resmi.""",

        "E": f"""🔔 NOTIFIKASI INTERNAL WIRAKY
Additional Cost telah dibayar.
Order: {data['order_id']} | Invoice: {data['invoice_id']}
Pelanggan: {data['nama']} | Nominal: Rp {data['nominal']:,}
Metode: {data['metode']} | Waktu: {data['waktu_bayar']}
Status: LUNAS (terverifikasi gateway)
Tindak lanjut: Cek kesiapan material & jadwal.
Dashboard: {os.getenv('APP_URL')}/dashboard"""
    }

    message = templates.get(template_type)
    if not message:
        return False

    try:
        # Contoh payload untuk provider WhatsApp umum
        payload = {
            "target": phone,
            "message": message,
            "countryCode": "62"
        }
        headers = {"Authorization": WA_API_TOKEN}
        
        # NOTE: Uncomment baris di bawah ini saat API Token sudah siap
        # response = requests.post(WA_API_URL, json=payload, headers=headers)
        # return response.status_code == 200
        
        print(f"[WA SIMULATION] To: {phone} | Template: {template_type}\n{message}\n{'-'*40}")
        return True # Return True untuk simulasi
    except Exception as e:
        print(f"[WA ERROR] Failed to send: {str(e)}")
        # Sesuai PDF: "Apabila WhatsApp gagal terkirim, invoice tetap sah; sistem mencatat kegagalan"
        return False