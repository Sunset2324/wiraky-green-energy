"""
Client untuk mengirim data Additional Cost ke Sistem Keuangan Wiraky.
Sesuai STANDAR_API_INTEGRATED v1.0 - Halaman 4, 5, & 10.

Endpoint: POST /api/v1/additional-costs
Retry: GET /api/v1/additional-costs/{id}/status
"""

import os
import json
import logging
import requests
from datetime import datetime, timezone
from services.hmac_utils import sign_request, generate_timestamp, serialize_payload

# Setup logger untuk modul ini
logger = logging.getLogger('wiraky_finance_client')


def send_additional_cost(payload: dict, retry_count: int = 0) -> dict:
    """
    Kirim pengajuan Additional Cost ke Sistem Keuangan Wiraky.
    
    Args:
        payload: Dict dengan struktur sesuai schema v1.0
        retry_count: Jumlah retry yang sudah dilakukan (untuk internal use)
    
    Returns:
        Dict dengan hasil response
    """
    # ===== MODE SIMULASI (untuk testing lokal) =====
    if os.getenv("SIMULATE_FINANCE_API", "false").lower() == "true":
        logger.info(f"🧪 [SIMULASI] Finance API tidak dipanggil. Mode simulasi aktif.")
        return {
            "success": True,
            "status_code": 202,
            "finance_reference": f"FIN-SIM-{payload.get('additional_cost_id', 'UNKNOWN')}",
            "message": "Pengajuan diterima (mode simulasi)",
            "simulated": True
        }
    # ================================================
    
    # Validasi bisnis sebelum kirim
    if payload.get("approval", {}).get("status") != "APPROVED":
        return {
            "success": False,
            "status_code": None,
            "finance_reference": None,
            "error": "NOT_SENT_NOT_APPROVED",
            "message": "Status approval harus APPROVED sebelum dikirim"
        }
    
    if payload.get("total_amount", 0) <= 0:
        return {
            "success": False,
            "status_code": None,
            "finance_reference": None,
            "error": "NO_ADDITIONAL_COST",
            "message": "Total amount harus > 0"
        }
    
    if payload.get("currency") != "IDR":
        return {
            "success": False,
            "status_code": None,
            "finance_reference": None,
            "error": "INVALID_CURRENCY",
            "message": "Currency harus IDR"
        }
    
    # Validasi subtotal items cocok dengan total
    items = payload.get("additional_items", [])
    calculated_total = sum(item.get("subtotal", 0) for item in items)
    if abs(calculated_total - payload.get("total_amount", 0)) > 0.01:
        return {
            "success": False,
            "status_code": None,
            "finance_reference": None,
            "error": "AMOUNT_MISMATCH",
            "message": f"Total items ({calculated_total}) tidak cocok dengan total_amount ({payload.get('total_amount')})"
        }
    
    # Siapkan request
    api_url = os.getenv("WIRAKY_FINANCE_API_URL")
    api_secret = os.getenv("WIRAKY_FINANCE_API_SECRET")
    
    if not api_url or not api_secret:
        return {
            "success": False,
            "status_code": None,
            "finance_reference": None,
            "error": "CONFIG_MISSING",
            "message": "WIRAKY_FINANCE_API_URL atau WIRAKY_FINANCE_API_SECRET belum diset di .env"
        }
    
    # Serialisasi body
    body_bytes = serialize_payload(payload)
    timestamp = generate_timestamp()
    
    # Buat signature
    signature = sign_request(api_secret, timestamp, body_bytes)
    
    # Header sesuai standar v1.0
    headers = {
        "Content-Type": "application/json",
        "X-Timestamp": timestamp,
        "X-Signature": signature,
        "X-Event-ID": payload.get("event_id"),
        "Idempotency-Key": payload.get("event_id")  # Sama dengan event_id untuk idempotency
    }
    
    # Kirim request dengan retry mechanism
    max_retries = 3
    retry_delay = 2  # detik
    
    try:
        logger.info(f"📤 Mengirim Additional Cost ke Finance API: {api_url}")
        logger.info(f"   Event ID: {payload.get('event_id')}")
        logger.info(f"   Order ID: {payload.get('order_id')}")
        logger.info(f"   Amount: Rp {payload.get('total_amount'):,}")
        
        response = requests.post(
            api_url,
            data=body_bytes,
            headers=headers,
            timeout=15
        )
        
        # Parse response
        if response.status_code == 202:
            # Success - pengajuan diterima
            data = response.json()
            logger.info(f"✅ Finance API menerima pengajuan: {data.get('finance_reference')}")
            return {
                "success": True,
                "status_code": 202,
                "finance_reference": data.get("finance_reference"),
                "message": data.get("message", "Pengajuan diterima untuk diproses")
            }
        elif response.status_code in [429, 500, 502, 503, 504]:
            # Server error atau rate limit - retry
            if retry_count < max_retries:
                logger.warning(f"⚠️ Server error {response.status_code}, retry {retry_count + 1}/{max_retries}")
                import time
                time.sleep(retry_delay * (retry_count + 1))  # Exponential backoff
                return send_additional_cost(payload, retry_count + 1)
            else:
                logger.error(f"❌ Max retries exceeded. Status: {response.status_code}")
                return {
                    "success": False,
                    "status_code": response.status_code,
                    "finance_reference": None,
                    "error": f"MAX_RETRIES_EXCEEDED",
                    "message": f"Gagal setelah {max_retries} percobaan"
                }
        else:
            # Client error (400, 401, 403, 422, dll)
            try:
                error_data = response.json()
                error_msg = error_data.get("error", error_data.get("message", str(response.text)))
            except:
                error_msg = response.text
            
            logger.error(f"❌ Finance API menolak: {response.status_code} - {error_msg}")
            return {
                "success": False,
                "status_code": response.status_code,
                "finance_reference": None,
                "error": f"HTTP_{response.status_code}",
                "message": error_msg
            }
    
    except requests.exceptions.Timeout:
        logger.error(f"️ Request timeout setelah 15 detik")
        if retry_count < max_retries:
            logger.warning(f"🔄 Retry timeout: {retry_count + 1}/{max_retries}")
            import time
            time.sleep(retry_delay)
            return send_additional_cost(payload, retry_count + 1)
        else:
            return {
                "success": False,
                "status_code": None,
                "finance_reference": None,
                "error": "TIMEOUT",
                "message": "Request timeout setelah 15 detik"
            }
    except requests.exceptions.ConnectionError as e:
        logger.error(f"🔌 Connection error: {str(e)}")
        return {
            "success": False,
            "status_code": None,
            "finance_reference": None,
            "error": "NETWORK_ERROR",
            "message": f"Gagal terhubung ke server: {str(e)}"
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Request error: {str(e)}")
        return {
            "success": False,
            "status_code": None,
            "finance_reference": None,
            "error": "REQUEST_ERROR",
            "message": str(e)
        }


def check_additional_cost_status(additional_cost_id: str) -> dict:
    """
    Cek status Additional Cost ke Finance API (untuk rekonsiliasi).
    Sesuai PDF Halaman 3 & 10: "GET status pembayaran untuk rekonsiliasi jika callback hilang"
    
    Endpoint: GET /api/v1/additional-costs/{id}/status
    """
    # Mode simulasi
    if os.getenv("SIMULATE_FINANCE_API", "false").lower() == "true":
        logger.info(f"🧪 [SIMULASI] Cek status dummy untuk: {additional_cost_id}")
        return {
            "success": True,
            "status_code": 200,
            "data": {
                "additional_cost_id": additional_cost_id,
                "status": "PAID",
                "payment_status": "PAID",
                "amount_paid": 0,
                "paid_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            }
        }
    
    api_url = os.getenv("WIRAKY_FINANCE_API_URL")
    api_secret = os.getenv("WIRAKY_FINANCE_API_SECRET")
    
    if not api_url or not api_secret:
        return {
            "success": False,
            "error": "CONFIG_MISSING",
            "message": "Konfigurasi API belum lengkap"
        }
    
    url = f"{api_url}/additional-costs/{additional_cost_id}/status"
    timestamp = generate_timestamp()
    
    # Untuk GET request, body kosong
    body_bytes = b""
    signature = sign_request(api_secret, timestamp, body_bytes)
    
    headers = {
        "X-Timestamp": timestamp,
        "X-Signature": signature
    }
    
    try:
        logger.info(f"🔍 Cek status Additional Cost: {additional_cost_id}")
        response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            logger.info(f"✅ Status: {data.get('status')}")
            return {
                "success": True,
                "status_code": 200,
                "data": data
            }
        else:
            logger.error(f"❌ Gagal cek status: {response.status_code}")
            return {
                "success": False,
                "status_code": response.status_code,
                "error": f"HTTP_{response.status_code}",
                "message": response.text
            }
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Network error saat cek status: {str(e)}")
        return {
            "success": False,
            "error": "NETWORK_ERROR",
            "message": str(e)
        }