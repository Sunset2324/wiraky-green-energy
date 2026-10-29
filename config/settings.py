import os
import secrets
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlparse


class Config:
    """Konfigurasi lama (MySQL, dipakai core/database.py). Dibiarkan apa adanya."""
    DB_HOST = os.getenv('DB_HOST', 'localhost')
    DB_USER = os.getenv('DB_USER', '')
    DB_PASS = os.getenv('DB_PASS', '')
    DB_NAME = os.getenv('DB_NAME', '')

    GATEWAY_SECRET = os.getenv('GATEWAY_SECRET', '')
    WA_API_KEY = os.getenv('WA_API_KEY', '')

    # URL Webhook yang akan didaftarkan ke Payment Gateway
    WEBHOOK_URL = os.getenv('WEBHOOK_URL', 'https://domainanda.com/webhook/payment')


# ---------------------------------------------------------------------------
# Standar API Integrated (Green Energy <-> Finance Wiraky), dokumen v1.0
# ---------------------------------------------------------------------------

class ConfigError(RuntimeError):
    """Konfigurasi integrasi tidak valid / tidak lengkap."""


@dataclass
class IntegrationSettings:
    mode: str                        # "SIMULATOR" atau "FINANCE_API"
    finance_api_url: str             # URL POST additional-costs
    finance_api_secret: str          # HMAC Green Energy -> Finance
    callback_secret: str             # HMAC Finance -> Green Energy
    callback_secret_previous: str    # secret lama (masa rotasi), boleh kosong
    green_energy_callback_url: str   # dipakai simulator untuk mengirim callback
    simulator_enabled: bool
    simulator_auto_pending: bool
    ephemeral_secrets: bool          # True = secret acak per proses (hanya mode simulator)
    request_timeout: float = 15.0
    outbox_max_attempts: int = 6
    outbox_backoff_base: float = 2.0
    outbox_backoff_cap: float = 300.0
    timestamp_max_age: int = 300
    reconcile_after_seconds: int = 120
    webhook_rate_limit_per_minute: int = 120
    worker_interval: float = 1.0
    expected_verifier: str = "WIRAKY_FINANCE_SYSTEM"

    def finance_status_url(self, additional_cost_id: str) -> str:
        return f"{self.finance_api_url.rstrip('/')}/{additional_cost_id}/status"

    def inbound_secrets(self):
        """Secret yang diterima untuk callback (secret saat ini + secret lama saat rotasi)."""
        return [s for s in (self.callback_secret, self.callback_secret_previous) if s]


def _truthy(value, default=False):
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _number(env, name, default, cast=int):
    raw = str(env.get(name, "")).strip()
    if not raw:
        return default
    try:
        return cast(raw)
    except ValueError:
        raise ConfigError(f"{name} harus berupa angka, nilai sekarang: {raw!r}")


def _is_loopback(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in ("127.0.0.1", "localhost", "::1")


def load_integration_settings(env: Mapping[str, str] = None) -> IntegrationSettings:
    """Baca konfigurasi standar API dari environment.

    Mode SIMULATOR (default bila WIRAKY_FINANCE_API_URL kosong): Finance API disimulasikan
    di aplikasi ini; secret boleh kosong (dibuat acak per proses, tidak pernah ditulis ke disk).
    Mode FINANCE_API: URL + dua secret berbeda wajib diisi, URL wajib HTTPS.
    """
    env = os.environ if env is None else env
    port = _number(env, "PORT", 5000)

    api_url = str(env.get("WIRAKY_FINANCE_API_URL", "")).strip()
    sim_raw = str(env.get("ENABLE_FINANCE_SIMULATOR", "")).strip()
    simulator = _truthy(sim_raw) if sim_raw else not api_url

    local_default = False
    if simulator and not api_url:
        api_url = f"http://127.0.0.1:{port}/finance-sim/api/v1/additional-costs"
        local_default = True
    if not api_url:
        raise ConfigError("WIRAKY_FINANCE_API_URL belum diisi (atau aktifkan ENABLE_FINANCE_SIMULATOR=1).")

    out_secret = str(env.get("WIRAKY_FINANCE_API_SECRET", "")).strip()
    cb_secret = str(env.get("WIRAKY_FINANCE_CALLBACK_SECRET", "")).strip()
    cb_previous = str(env.get("WIRAKY_FINANCE_CALLBACK_SECRET_PREVIOUS", "")).strip()

    ephemeral = False
    if simulator and local_default:
        if not out_secret:
            out_secret, ephemeral = secrets.token_hex(32), True
        if not cb_secret:
            cb_secret, ephemeral = secrets.token_hex(32), True
    else:
        missing = [name for name, val in (("WIRAKY_FINANCE_API_SECRET", out_secret),
                                          ("WIRAKY_FINANCE_CALLBACK_SECRET", cb_secret)) if not val]
        if missing:
            raise ConfigError("Variabel wajib belum diisi: " + ", ".join(missing))

    if out_secret == cb_secret:
        raise ConfigError("Secret arah kirim dan arah callback harus BERBEDA (standar bagian 07).")

    mode = "SIMULATOR" if simulator else "FINANCE_API"
    if mode == "FINANCE_API":
        if urlparse(api_url).scheme != "https" and not _is_loopback(api_url):
            raise ConfigError("WIRAKY_FINANCE_API_URL wajib HTTPS pada mode produksi.")
        short = [n for n, v in (("WIRAKY_FINANCE_API_SECRET", out_secret),
                                ("WIRAKY_FINANCE_CALLBACK_SECRET", cb_secret)) if len(v) < 16]
        if short:
            raise ConfigError("Secret terlalu pendek (minimal 16 karakter): " + ", ".join(short))

    callback_url = str(env.get("GREEN_ENERGY_CALLBACK_URL", "")).strip() or \
        f"http://127.0.0.1:{port}/api/v1/finance/payment-events"

    return IntegrationSettings(
        mode=mode,
        finance_api_url=api_url,
        finance_api_secret=out_secret,
        callback_secret=cb_secret,
        callback_secret_previous=cb_previous,
        green_energy_callback_url=callback_url,
        simulator_enabled=simulator,
        simulator_auto_pending=_truthy(env.get("SIMULATOR_AUTO_PENDING"), True),
        ephemeral_secrets=ephemeral,
        request_timeout=_number(env, "FINANCE_REQUEST_TIMEOUT", 15.0, float),
        outbox_max_attempts=_number(env, "OUTBOX_MAX_ATTEMPTS", 6),
        outbox_backoff_base=_number(env, "OUTBOX_BACKOFF_BASE", 2.0, float),
        outbox_backoff_cap=_number(env, "OUTBOX_BACKOFF_CAP", 300.0, float),
        timestamp_max_age=_number(env, "TIMESTAMP_MAX_AGE", 300),
        reconcile_after_seconds=_number(env, "RECONCILE_AFTER_SECONDS", 120),
        webhook_rate_limit_per_minute=_number(env, "WEBHOOK_RATE_LIMIT_PER_MINUTE", 120),
        worker_interval=_number(env, "WORKER_INTERVAL", 1.0, float),
        expected_verifier=str(env.get("FINANCE_VERIFIED_BY", "")).strip() or "WIRAKY_FINANCE_SYSTEM",
    )


# ---------------------------------------------------------------------------
# WhatsApp Business (Cloud API) - notifikasi ke pelanggan
# ---------------------------------------------------------------------------

@dataclass
class WhatsAppSettings:
    mode: str                     # "LOG" (tidak kirim apa pun, hanya catat) atau "CLOUD_API"
    api_version: str
    phone_number_id: str
    access_token: str
    app_secret: str               # untuk verifikasi X-Hub-Signature-256 webhook Meta
    verify_token: str             # untuk handshake GET webhook Meta
    language: str
    template_bill_issued: str
    template_payment_confirmed: str
    notify_kinds: tuple
    max_attempts: int = 5
    backoff_base: float = 5.0
    backoff_cap: float = 600.0
    request_timeout: float = 15.0

    @property
    def messages_url(self) -> str:
        return f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"


def load_whatsapp_settings(env: Mapping[str, str] = None) -> WhatsAppSettings:
    """Default mode LOG: aman untuk demo, tidak ada pesan nyata terkirim.

    Mode CLOUD_API aktif hanya bila WA_ENABLED=1; saat itu WA_PHONE_NUMBER_ID dan WA_ACCESS_TOKEN wajib.
    """
    env = os.environ if env is None else env
    enabled = _truthy(env.get("WA_ENABLED"), False)
    phone_id = str(env.get("WA_PHONE_NUMBER_ID", "")).strip()
    token = str(env.get("WA_ACCESS_TOKEN", "")).strip()
    if enabled:
        missing = [n for n, v in (("WA_PHONE_NUMBER_ID", phone_id), ("WA_ACCESS_TOKEN", token)) if not v]
        if missing:
            raise ConfigError("WA_ENABLED=1 tetapi belum diisi: " + ", ".join(missing))
        if not phone_id.isdigit():
            raise ConfigError("WA_PHONE_NUMBER_ID harus berupa angka (Phone number ID dari Meta, bukan nomor telepon).")
    kinds = tuple(k.strip().upper() for k in str(env.get("WA_NOTIFY_KINDS", "BILL_ISSUED,PAYMENT_CONFIRMED")).split(",")
                  if k.strip())
    unknown = [k for k in kinds if k not in ("BILL_ISSUED", "PAYMENT_CONFIRMED")]
    if unknown:
        raise ConfigError("WA_NOTIFY_KINDS tidak dikenal: " + ", ".join(unknown))
    return WhatsAppSettings(
        mode="CLOUD_API" if enabled else "LOG",
        api_version=str(env.get("WA_API_VERSION", "")).strip() or "v21.0",
        phone_number_id=phone_id, access_token=token,
        app_secret=str(env.get("WA_APP_SECRET", "")).strip(),
        verify_token=str(env.get("WA_VERIFY_TOKEN", "")).strip(),
        language=str(env.get("WA_TEMPLATE_LANGUAGE", "")).strip() or "id",
        template_bill_issued=str(env.get("WA_TEMPLATE_BILL_ISSUED", "")).strip() or "additional_cost_bill",
        template_payment_confirmed=str(env.get("WA_TEMPLATE_PAYMENT_CONFIRMED", "")).strip() or "additional_cost_paid",
        notify_kinds=kinds,
        max_attempts=_number(env, "WA_MAX_ATTEMPTS", 5),
        backoff_base=_number(env, "WA_BACKOFF_BASE", 5.0, float),
        backoff_cap=_number(env, "WA_BACKOFF_CAP", 600.0, float),
        request_timeout=_number(env, "WA_REQUEST_TIMEOUT", 15.0, float),
    )
