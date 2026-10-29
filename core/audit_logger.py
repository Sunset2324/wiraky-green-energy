import logging
from datetime import datetime

# 1. Buat logger KHUSUS untuk audit, agar tidak mengganggu logging Flask/Werkzeug
audit_logger = logging.getLogger('wiraky_audit')
audit_logger.setLevel(logging.INFO)

# 2. Handler untuk menulis ke file audit_trail.log
# Gunakan encoding utf-8 agar karakter Indonesia aman
file_handler = logging.FileHandler('audit_trail.log', encoding='utf-8')
file_handler.setLevel(logging.INFO)

# 3. Format standar yang AMAN (tidak memaksa field order_id di semua log sistem)
formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s')
file_handler.setFormatter(formatter)

# 4. Tambahkan handler ke logger (cek agar tidak duplikat saat reload)
if not audit_logger.handlers:
    audit_logger.addHandler(file_handler)


def log_audit(order_id: str, action: str, detail: str, actor: text_str = "SYSTEM"):
    """
    Mencatat setiap perubahan status kritis untuk keperluan audit dan rekonsiliasi.
    Sesuai PDF Halaman 5: "Laporan transaksi dan audit dapat ditelusuri per order, 
    invoice, waktu, serta petugas pengubah data."
    """
    # Gabungkan data ke dalam pesan string agar format logging tetap standar
    message = f"Order: {order_id} | Action: {action} | Detail: {detail} | Actor: {actor}"
    
    # Tulis ke file log
    audit_logger.info(message)
    
    # OPSIONAL: Simpan juga ke database (jika tabel audit_logs sudah dibuat)
    # from core.database import get_db_connection
    # conn = get_db_connection()
    # if conn:
    #     try:
    #         cursor = conn.cursor()
    #         cursor.execute(
    #             """INSERT INTO audit_logs (order_id, action, detail, actor, created_at) 
    #                VALUES (%s, %s, %s, %s, NOW())""", 
    #             (order_id, action, detail, actor)
    #         )
    #         conn.commit()
    #         cursor.close()
    #         conn.close()
    #     except Exception as e:
    #         print(f"[Audit DB Error] {str(e)}")