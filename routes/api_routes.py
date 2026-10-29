"""API internal Green Energy (JSON). Pemicu pengajuan Additional Cost ke Finance."""
from flask import Blueprint, current_app, jsonify, request

from core import payment_logic as logic
from core.serialize import jsonable

api_bp = Blueprint("api", __name__, url_prefix="/api")


def _svc():
    return current_app.extensions["wiraky"]


def _error(exc: logic.BusinessRuleError):
    return jsonify(error=exc.code, message=exc.message), exc.http_status


@api_bp.post("/additional-costs")
def create_additional_cost():
    """Body JSON:
    {"order_id": "...", "additional_cost_id": (opsional, untuk revisi),
     "customer": {"name","whatsapp","package"},
     "items": [{"part_code": "JASA-001", "qty": 1}],
     "approval": {"status":"APPROVED","reference":"APR-...","engineering_verified":true,"approved_at":(opsional)}}
    Harga dihitung server dari master harga. items kosong/qty 0 -> NOT_REQUIRED (tidak ada panggilan Finance).
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="INVALID_JSON", message="Body harus JSON object"), 400
    items = data.get("items", [])
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        return jsonify(error="INVALID_ITEMS", message="items harus berupa list of object"), 400
    selection = {}
    for item in items:
        code, qty = item.get("part_code"), item.get("qty", 0)
        if not isinstance(code, str):
            return jsonify(error="INVALID_ITEMS", message="part_code harus berupa string"), 400
        if isinstance(qty, int) and not isinstance(qty, bool):
            selection[code] = selection.get(code, 0) + qty    # baris ganda untuk kode sama dijumlahkan
        else:
            selection[code] = qty                              # build_items menolak dengan pesan jelas
    try:
        record = logic.create_additional_cost(
            _svc().repo, order_id=data.get("order_id"), customer=data.get("customer"), selection=selection,
            approval=data.get("approval"), now=_svc().now, additional_cost_id=data.get("additional_cost_id"),
            actor="api")
    except logic.BusinessRuleError as exc:
        return _error(exc)
    return jsonify(jsonable(record)), 201


@api_bp.get("/additional-costs")
def list_additional_costs():
    return jsonify(jsonable(_svc().repo.list_costs()))


@api_bp.get("/additional-costs/<additional_cost_id>")
def get_additional_cost(additional_cost_id):
    svc = _svc()
    revisions = [c for c in svc.repo.list_costs() if c["additional_cost_id"] == additional_cost_id]
    if not revisions:
        return jsonify(error="NOT_FOUND"), 404
    outbox = [e for e in svc.repo.list_outbox() if e["additional_cost_id"] == additional_cost_id]
    return jsonify(jsonable({
        "latest": revisions[-1], "revisions": revisions, "outbox": outbox,
        "audit": svc.repo.list_audit(additional_cost_id),
    }))


@api_bp.post("/additional-costs/<additional_cost_id>/reconcile")
def reconcile(additional_cost_id):
    """Rekonsiliasi manual: GET status dari Finance bila callback hilang/tertunda."""
    return jsonify(jsonable(_svc().reconciler.reconcile_one(additional_cost_id)))


@api_bp.post("/outbox/process")
def process_outbox():
    """Kirim sekarang semua entri outbox yang sudah jatuh tempo (worker melakukannya otomatis)."""
    return jsonify(jsonable(_svc().outbox.process_due()))


@api_bp.get("/alerts")
def list_alerts():
    return jsonify(jsonable(_svc().repo.list_alerts()))
