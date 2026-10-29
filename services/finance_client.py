"""Pengirim API Green Energy -> Finance Wiraky (Standar API Integrated bagian 03 dan 05).

Header POST: Content-Type, X-Timestamp, X-Signature (HMAC-SHA256 timestamp + "." + body),
X-Event-ID, Idempotency-Key. Retry memakai event_id yang SAMA sehingga tidak ada tagihan ganda.

Asumsi (dokumen tidak merinci): permintaan GET status juga ditandatangani dengan
X-Timestamp + X-Signature atas body kosong.
"""
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional, Tuple
from urllib.parse import urlparse

from core.payload import canonical_body
from core.security import compute_signature, utc_timestamp

Transport = Callable[[str, str, dict, bytes, float], Tuple[int, bytes]]

ACKED = "ACKED"        # Finance menjawab 202/200 RECEIVED yang valid
RETRY = "RETRY"        # timeout / jaringan / 429 / 5xx -> coba lagi dengan event_id sama
REJECTED = "REJECTED"  # 4xx: payload/otorisasi/konflik; retry buta tidak ada gunanya
SKIPPED = "SKIPPED"    # aturan pemicu tidak terpenuhi: tidak boleh memanggil Finance


class TransportError(Exception):
    """Kegagalan jaringan (timeout, koneksi putus, DNS, dsb)."""


def _is_loopback(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() in ("127.0.0.1", "localhost", "::1")


def requests_transport(method: str, url: str, headers: dict, data: bytes, timeout: float):
    """Transport HTTP default (library requests)."""
    import requests
    session = requests.Session()
    if _is_loopback(url):
        session.trust_env = False   # jangan lewat proxy sistem untuk alamat lokal
    try:
        response = session.request(method, url, headers=headers, data=data, timeout=timeout)
        return response.status_code, response.content
    except requests.RequestException as exc:
        raise TransportError(f"{type(exc).__name__}: {exc}")
    finally:
        session.close()


@dataclass
class SendResult:
    outcome: str
    http_status: Optional[int] = None
    body: Optional[dict] = None
    error: Optional[str] = None


@dataclass
class StatusResult:
    ok: bool
    http_status: Optional[int] = None
    body: Optional[dict] = None
    error: Optional[str] = None


def _json_or_none(raw: bytes):
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else None
    except (ValueError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


class FinanceClient:
    def __init__(self, settings, transport: Optional[Transport] = None, now=None):
        self.settings = settings
        self.transport = transport or requests_transport
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _signed_headers(self, body: bytes) -> dict:
        timestamp = utc_timestamp(self.now())
        return {
            "X-Timestamp": timestamp,
            "X-Signature": compute_signature(self.settings.finance_api_secret, timestamp, body),
        }

    def submit(self, payload: dict) -> SendResult:
        """Kirim ADDITIONAL_COST_APPROVED. Timestamp/signature baru tiap percobaan; body dan event_id tetap."""
        if (payload.get("approval") or {}).get("status") != "APPROVED":
            return SendResult(SKIPPED, error="NOT_SENT_NOT_APPROVED")
        if not isinstance(payload.get("total_amount"), int) or payload["total_amount"] <= 0:
            return SendResult(SKIPPED, error="NO_ADDITIONAL_COST")

        body = canonical_body(payload)
        headers = {"Content-Type": "application/json", **self._signed_headers(body),
                   "X-Event-ID": payload["event_id"], "Idempotency-Key": payload["event_id"]}
        try:
            status, raw = self.transport("POST", self.settings.finance_api_url, headers, body,
                                         self.settings.request_timeout)
        except TransportError as exc:
            return SendResult(RETRY, error=f"TRANSPORT: {exc}")

        parsed = _json_or_none(raw)
        if status in (200, 202):
            if (parsed and parsed.get("status") == "RECEIVED" and parsed.get("finance_reference")
                    and parsed.get("additional_cost_id") == payload["additional_cost_id"]):
                return SendResult(ACKED, status, parsed)
            # 2xx tetapi isi tidak sesuai kontrak: aman diulang (idempotent), dibatasi max attempts
            return SendResult(RETRY, status, parsed, "UNEXPECTED_RESPONSE_BODY")
        detail = (parsed or {}).get("error") or (raw[:200].decode("utf-8", "replace") if raw else "")
        if status == 429 or status >= 500:
            return SendResult(RETRY, status, parsed, f"HTTP_{status} {detail}".strip())
        return SendResult(REJECTED, status, parsed, f"HTTP_{status} {detail}".strip())

    def get_status(self, additional_cost_id: str, revision: Optional[int] = None) -> StatusResult:
        """GET status pembayaran untuk rekonsiliasi (callback hilang / tertunda)."""
        url = self.settings.finance_status_url(additional_cost_id)
        if revision is not None:
            url += f"?revision={int(revision)}"
        headers = self._signed_headers(b"")
        try:
            status, raw = self.transport("GET", url, headers, b"", self.settings.request_timeout)
        except TransportError as exc:
            return StatusResult(False, error=f"TRANSPORT: {exc}")
        parsed = _json_or_none(raw)
        if status == 200 and parsed is not None:
            return StatusResult(True, status, parsed)
        return StatusResult(False, status, parsed, f"HTTP_{status}")
