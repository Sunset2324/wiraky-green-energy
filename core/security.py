"""HMAC-SHA256, validasi timestamp, dan rate limiter (Standar API Integrated, bagian 03/07)."""
import hashlib
import hmac
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Iterable, Optional

TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def utc_timestamp(now: Optional[datetime] = None) -> str:
    """Timestamp UTC ISO-8601 untuk header X-Timestamp."""
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc).strftime(TIMESTAMP_FORMAT)


def compute_signature(secret: str, timestamp: str, body: bytes) -> str:
    """HMAC-SHA256 dari: timestamp + "." + body (byte-for-byte), hasil hex."""
    signed = timestamp.encode("utf-8") + b"." + body
    return hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()


def verify_signature(secrets: Iterable[str], timestamp: str, body: bytes, supplied: str) -> bool:
    """True jika signature cocok dengan salah satu secret (secret baru + lama saat rotasi).

    Perbandingan dilakukan pada bytes dan constant-time (compare_digest melempar
    TypeError untuk str non-ASCII, jadi tidak boleh dipakai langsung pada header).
    """
    supplied_bytes = (supplied or "").strip().lower().encode("utf-8")
    ok = False
    for secret in secrets:
        if not secret:
            continue
        expected = compute_signature(secret, timestamp, body).encode("utf-8")
        if hmac.compare_digest(expected, supplied_bytes):
            ok = True  # lanjut loop agar waktu respons tidak bocor secret mana yang cocok
    return ok


def is_fresh(timestamp: str, now: Optional[datetime] = None, max_age_seconds: int = 300) -> bool:
    """Timestamp harus berformat tepat dan usianya (maju/mundur) <= max_age_seconds."""
    try:
        received = datetime.strptime(timestamp, TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return False
    now = now or datetime.now(timezone.utc)
    return abs((now - received).total_seconds()) <= max_age_seconds


class RateLimiter:
    """Rate limiter sliding-window sederhana (in-memory) per kunci, mis. alamat IP."""

    def __init__(self, limit_per_minute: int, window_seconds: float = 60.0, clock=time.monotonic):
        self.limit = limit_per_minute
        self.window = window_seconds
        self._clock = clock
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        if self.limit <= 0:       # 0 = dimatikan
            return True
        now = self._clock()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True
