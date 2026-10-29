"""
Utility HMAC-SHA256 untuk komunikasi dengan Sistem Keuangan Wiraky.
Sesuai STANDAR_API_INTEGRATED v1.0 - Halaman 5 & 7.

Signature = HMAC-SHA256(secret, timestamp + "." + body_bytes)
"""

import hmac
import hashlib
import json
from datetime import datetime, timezone


def sign_request(secret: str, timestamp: str, body_bytes: bytes) -> str:
    """
    Buat signature HMAC-SHA256 untuk DIKIRIM ke Finance API.
    
    Args:
        secret: API secret dari .env (WIRAKY_FINANCE_API_SECRET)
        timestamp: ISO-8601 UTC string (YYYY-MM-DDTHH:MM:SSZ)
        body_bytes: Body JSON yang sudah di-encode UTF-8
    
    Returns:
        Hex digest HMAC-SHA256
    """
    signed_data = timestamp.encode("utf-8") + b"." + body_bytes
    digest = hmac.new(
        secret.encode("utf-8"),
        signed_data,
        hashlib.sha256
    ).hexdigest()
    return digest


def verify_signature(secret: str, timestamp: str, body_bytes: bytes, supplied_signature: str) -> bool:
    """
    Verifikasi signature HMAC-SHA256 dari WEBHOOK Finance.
    Menggunakan compare_digest untuk mencegah timing attack.
    
    Args:
        secret: Callback secret dari .env (WIRAKY_FINANCE_CALLBACK_SECRET)
        timestamp: Header X-Timestamp dari request
        body_bytes: Raw body bytes dari request
        supplied_signature: Header X-Signature dari request
    
    Returns:
        True jika signature valid, False jika tidak
    """
    expected = sign_request(secret, timestamp, body_bytes)
    return hmac.compare_digest(expected, supplied_signature)


def generate_timestamp() -> str:
    """
    Generate timestamp UTC ISO-8601 untuk header X-Timestamp.
    Format: YYYY-MM-DDTHH:MM:SSZ
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_timestamp_fresh(timestamp: str, max_age_seconds: int = 300) -> bool:
    """
    Periksa apakah timestamp masih segar (mencegah replay attack).
    Sesuai PDF Halaman 7: "pemeriksaan usia request"
    
    Args:
        timestamp: ISO-8601 UTC string
        max_age_seconds: Maksimum usia timestamp dalam detik (default 5 menit)
    
    Returns:
        True jika timestamp masih dalam rentang waktu yang diizinkan
    """
    try:
        # Parse timestamp dengan timezone UTC
        received = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = abs((now - received).total_seconds())
        return delta <= max_age_seconds
    except (ValueError, TypeError):
        return False


def serialize_payload(payload: dict) -> bytes:
    """
    Serialisasi payload ke JSON bytes dengan format yang konsisten.
    Menggunakan separators=(",", ":") untuk compact JSON tanpa spasi.
    ensure_ascii=False agar karakter Indonesia (UTF-8) aman.
    """
    return json.dumps(
        payload,
        separators=(",", ":"),
        ensure_ascii=False
    ).encode("utf-8")