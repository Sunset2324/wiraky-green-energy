"""Green Energy - IndiHome Aura: integrasi Additional Cost dengan Sistem Keuangan Wiraky.

Mengikuti "Standar API Integrated" v1.0:
  Green Energy --POST additional-costs (HMAC)--> Finance API  (202 RECEIVED)
  Finance API  --POST payment-events  (HMAC)--> Green Energy  (status PAID dst.)
  Green Energy --GET status--> Finance API  (rekonsiliasi bila callback hilang)

Menjalankan:   python app.py          (PORT dan FLASK_DEBUG dibaca dari .env / environment)
Demo lokal:    tanpa WIRAKY_FINANCE_API_URL -> Finance disimulasikan di /finance-sim
"""
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, url_for

from config.settings import (ConfigError, IntegrationSettings, WhatsAppSettings, load_integration_settings,
                             load_whatsapp_settings)
from core.repository import InMemoryRepository
from core.security import RateLimiter
from services.finance_client import FinanceClient
from services.finance_simulator import FinanceSimulator
from services.outbox import BackgroundWorker, OutboxProcessor
from services.reconciliation import Reconciler
from services.whatsapp_service import WhatsAppNotifier

WIB = timezone(timedelta(hours=7))


@dataclass
class Services:
    settings: IntegrationSettings
    repo: InMemoryRepository
    client: FinanceClient
    outbox: OutboxProcessor
    reconciler: Reconciler
    limiter: RateLimiter
    now: Callable[[], datetime]
    settings_wa: WhatsAppSettings
    whatsapp: WhatsAppNotifier
    simulator: Optional[FinanceSimulator] = None
    worker: Optional[BackgroundWorker] = None

    def start_background(self):
        """Jalankan worker outbox + rekonsiliasi (dipanggil dari main, bukan dari create_app)."""
        if self.worker is None:
            self.worker = BackgroundWorker(
                [self.outbox.process_due, self.reconciler.reconcile_pending, self.whatsapp.process_due], self.settings.worker_interval)
        self.worker.start()


def _rupiah(value) -> str:
    try:
        return "Rp " + f"{int(value):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "-"


def _wib(value) -> str:
    if not isinstance(value, datetime):
        return "-"
    return value.astimezone(WIB).strftime("%d %b %H:%M:%S")


def _badge(status) -> str:
    return {
        "PAID": "b-lunas", "REFUNDED": "b-danger", "FAILED": "b-danger", "EXPIRED": "b-danger",
        "REVIEW": "b-danger", "NOT_REQUIRED": "b-muted", "CANCELLED": "b-muted", "PENDING": "b-pending",
    }.get(status, "b-pending")


def create_app(env=None, *, finance_transport=None, callback_transport=None, now=None,
               whatsapp_transport=None) -> Flask:
    """Application factory. Parameter transport/now hanya untuk pengujian (injeksi)."""
    env = os.environ if env is None else env
    settings = load_integration_settings(env)
    wa_settings = load_whatsapp_settings(env)
    clock = now or (lambda: datetime.now(timezone.utc))

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["SECRET_KEY"] = env.get("SECRET_KEY") or secrets.token_hex(32)
    app.config["JSON_AS_ASCII"] = False            # karakter Indonesia aman di JSON
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024  # body > 1 MB ditolak (413)

    repo = InMemoryRepository()
    client = FinanceClient(settings, transport=finance_transport, now=clock)
    svc = Services(
        settings=settings, repo=repo, client=client,
        outbox=OutboxProcessor(repo, client, settings, clock),
        reconciler=Reconciler(repo, client, settings, clock),
        limiter=RateLimiter(settings.webhook_rate_limit_per_minute),
        now=clock, settings_wa=wa_settings,
        whatsapp=WhatsAppNotifier(repo, wa_settings, clock, transport=whatsapp_transport),
    )
    if settings.simulator_enabled:
        svc.simulator = FinanceSimulator(settings, now=clock, callback_transport=callback_transport)
    app.extensions["wiraky"] = svc

    app.jinja_env.filters.update(rupiah=_rupiah, wib=_wib, badge=_badge)
    app.context_processor(lambda: {"has_simulator": svc.simulator is not None})

    from routes.api_routes import api_bp
    from routes.dashboard_routes import ui_bp
    from routes.webhook_routes import webhook_bp
    from routes.whatsapp_routes import wa_bp
    app.register_blueprint(webhook_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(ui_bp)
    app.register_blueprint(wa_bp)
    if svc.simulator is not None:
        from routes.simulator_routes import sim_bp
        app.register_blueprint(sim_bp)

    @app.get("/")
    def home():
        return redirect(url_for("ui.surveyor_form"))

    @app.get("/health")
    def health():
        return {"status": "online", "service": "Wiraky Green Energy - Additional Cost Integration",
                "mode": settings.mode, "whatsapp": wa_settings.mode}

    @app.errorhandler(404)
    def not_found(error):
        return {"error": "Endpoint tidak ditemukan"}, 404

    @app.errorhandler(413)
    def too_large(error):
        return {"error": "PAYLOAD_TOO_LARGE"}, 413

    @app.errorhandler(500)
    def internal_error(error):
        return {"error": "Terjadi kesalahan pada server"}, 500

    return app


def svc_wa_mode(app):
    wa = app.extensions["wiraky"].settings_wa
    return wa.mode + (" (pesan hanya dicatat, tidak dikirim)" if wa.mode == "LOG" else f" ({wa.api_version}, webhook /api/v1/whatsapp/webhook)")


def _banner(app, settings, port, debug):
    print("\n=== Green Energy - Integrasi Additional Cost (Standar API v1.0) ===")
    print(f"Mode        : {settings.mode}" + ("  (Finance DISIMULASIKAN, bukan sistem asli)" if settings.simulator_enabled else ""))
    print(f"Debug       : {'ON' if debug else 'OFF'}   Port: {port}")
    print(f"Webhook     : POST /api/v1/finance/payment-events")
    print(f"WhatsApp    : {svc_wa_mode(app)}")
    print(f"Finance API : {settings.finance_api_url}")
    if settings.ephemeral_secrets:
        print("Secret      : acak per proses (mode simulator, tidak disimpan). Penyimpanan: IN-MEMORY, hilang saat restart.")
    else:
        print("Penyimpanan : IN-MEMORY, hilang saat restart (belum untuk produksi).")
    print(f"Halaman     : /surveyor  /dashboard  /whatsapp" + ("  /finance-sim" if settings.simulator_enabled else "") + "\n")


def main():
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        app = create_app()
    except ConfigError as exc:
        raise SystemExit(f"[!] Konfigurasi tidak valid: {exc}")

    debug = os.getenv("FLASK_DEBUG", "0").lower() in ("1", "true", "yes")
    port = int(os.getenv("PORT", "5000"))
    svc = app.extensions["wiraky"]
    # Dengan reloader (debug), hanya proses anak yang boleh menjalankan worker
    if not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        svc.start_background()
    if not debug or os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        _banner(app, svc.settings, port, debug)
    app.run(debug=debug, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
