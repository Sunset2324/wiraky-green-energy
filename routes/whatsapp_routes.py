"""WhatsApp Business: halaman monitoring notifikasi + webhook status dari Meta.

GET  /api/v1/whatsapp/webhook   handshake verifikasi Meta (hub.verify_token)
POST /api/v1/whatsapp/webhook   status pesan (sent/delivered/read/failed), WAJIB X-Hub-Signature-256 valid
GET  /whatsapp                  daftar notifikasi (internal)
POST /whatsapp/process          proses antrean sekarang
"""
import hashlib
import hmac
import json

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, url_for

wa_bp = Blueprint("whatsapp", __name__)


def _svc():
    return current_app.extensions["wiraky"]


def _valid_meta_signature(app_secret: str, body: bytes, header: str) -> bool:
    prefix = "sha256="
    if not app_secret or not header.startswith(prefix):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[len(prefix):].strip().lower().encode("utf-8"), expected.encode("utf-8"))


@wa_bp.get("/api/v1/whatsapp/webhook")
def verify_webhook():
    settings = _svc().settings_wa
    if settings.mode != "CLOUD_API" or not settings.verify_token:
        return jsonify(error="NOT_CONFIGURED"), 404
    supplied = request.args.get("hub.verify_token", "")
    if request.args.get("hub.mode") == "subscribe" and hmac.compare_digest(
            supplied.encode("utf-8"), settings.verify_token.encode("utf-8")):
        return request.args.get("hub.challenge", ""), 200, {"Content-Type": "text/plain; charset=utf-8"}
    return jsonify(error="FORBIDDEN"), 403


@wa_bp.post("/api/v1/whatsapp/webhook")
def receive_webhook():
    svc = _svc()
    settings = svc.settings_wa
    if settings.mode != "CLOUD_API":
        return jsonify(error="NOT_CONFIGURED"), 404
    if not settings.app_secret:                       # fail-closed: tanpa App Secret semua ditolak
        return jsonify(error="APP_SECRET_NOT_SET"), 503
    body = request.get_data(cache=False)
    if not _valid_meta_signature(settings.app_secret, body, request.headers.get("X-Hub-Signature-256", "")):
        return jsonify(error="INVALID_SIGNATURE"), 401
    try:
        data = json.loads(body)
    except ValueError:
        return jsonify(error="INVALID_JSON"), 400

    updated = 0
    entries = data.get("entry") if isinstance(data, dict) else None
    for entry in entries if isinstance(entries, list) else []:
        for change in (entry.get("changes") or []) if isinstance(entry, dict) else []:
            value = change.get("value") if isinstance(change, dict) else None
            for status in (value.get("statuses") or []) if isinstance(value, dict) else []:
                if isinstance(status, dict) and status.get("id") and isinstance(status.get("status"), str):
                    errors = status.get("errors") if isinstance(status.get("errors"), list) else None
                    if svc.whatsapp.apply_status_update(str(status["id"]), status["status"], errors) == "UPDATED":
                        updated += 1
    # Pesan masuk dari pelanggan sengaja tidak diproses (bukan jalur persetujuan/pembayaran).
    return jsonify(result="OK", updated=updated), 200


@wa_bp.get("/whatsapp")
def page():
    svc = _svc()
    return render_template("whatsapp.html", items=list(reversed(svc.repo.list_notifications())),
                           mode=svc.settings_wa.mode, wa=svc.settings_wa, msg=request.args.get("msg"))


@wa_bp.post("/whatsapp/process")
def process():
    results = _svc().whatsapp.send_now()
    return redirect(url_for("whatsapp.page", msg=f"{len(results)} notifikasi diproses."))
