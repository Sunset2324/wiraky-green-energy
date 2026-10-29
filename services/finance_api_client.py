"""
Client untuk mengirim data Additional Cost ke Sistem Keuangan Wiraky.
Sesuai STANDAR_API_INTEGRATED v1.0 - Halaman 4 & 5.

Endpoint: POST /api/v1/additional-costs
"""

import os
import requests
from datetime import datetime, timezone
from services.hmac_utils import sign_request, generate_timestamp, serialize_payload


def send_additional_cost(payload: dict) -> dict:
    """
    Kirim pengajuan Additional Cost ke Sistem Keuangan Wiraky.
    
    Args:
        payload: Dict dengan struktur sesuai schema v1.0
            - schema_version: "1.0"
            - event_id: ID unik event (contoh: EVT-AC-2026-0001-R01)
            - event_type: "ADDITIONAL_COST_APPROVED"
            - occurred_at: ISO-8601 UTC
            - source: "GREEN_ENERGY"
            - order_id: ID order (contoh: WIR-2026-0001)
            - additional_cost_id: ID additional cost (contoh: AC-2026-0001)
            - revision: Integer (1, 2, 3, ...)
            - customer: {name, whatsapp, package}
            - additional_items: [{part_code, description, qty, unit, unit_price, subtotal}]
            - total_amount: Float (harus > 0)
            - currency: "IDR"
            - approval: {status: "APPROVED", reference, approved_at}
            - payment_status: "NOT_BILLED"
    
    Returns:
        Dict dengan hasil response:
            - success: bool
            - status_code: int
            - finance_reference: str (jika success)
            - error: str (jika gagal)
    """
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
    
    # Kirim request
    try:
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
            return {
                "success": True,
                "status_code": 202,
                "finance_reference": data.get("finance_reference"),
                "message": data.get("message", "Pengajuan diterima untuk diproses")
            }
        else:
            # Error
            try:
                error_data = response.json()
                error_msg = error_data.get("error", error_data.get("message", str(response.text)))
            except:
                error_msg = response.text
            
            return {
                "success": False,
                "status_code": response.status_code,
                "finance_reference": None,
                "error": f"HTTP_{response.status_code}",
                "message": error_msg
            }
    
    except requests.exceptions.Timeout:
        return {
            "success": False,
            "status_code": None,
            "finance_reference": None,
            "error": "TIMEOUT",
            "message": "Request timeout setelah 15 detik"
        }
    except requests.exceptions.RequestException as e:
        return {
            "success": False,
            "status_code": None,
            "finance_reference": None,
            "error": "NETWORK_ERROR",
            "message": str(e)
        }


def check_additional_cost_status(additional_cost_id: str) -> dict:
    """
    Cek status Additional Cost ke Finance API (untuk rekonsiliasi).
    Sesuai PDF Halaman 3: "GET status pembayaran untuk rekonsiliasi jika callback hilang"
    
    Endpoint: GET /api/v1/additional-costs/{id}/status
    """
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
        response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            return {
                "success": True,
                "status_code": 200,
                "data": response.json()
            }
        else:
            return {
                "success": False,
                "status_code": response.status_code,
                "error": f"HTTP_{response.status_code}",
                "message": response.text
            }
    except requests.exceptions.RequestException as e:
        return {
            "success": False,
            "error": "NETWORK_ERROR",
            "message": str(e)
        }