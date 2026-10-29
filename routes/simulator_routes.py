"""Endpoint SIMULATOR Finance API (hanya aktif di mode simulator). Bukan sistem Finance asli."""
from urllib.parse import quote

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, url_for

sim_bp = Blueprint("finance_sim", __name__, url_prefix="/finance-sim")


def _sim():
    return current_app.extensions["wiraky"].simulator


@sim_bp.post("/api/v1/additional-costs")
def receive_additional_cost():
    status, body = _sim().handle_submission(request.headers, request.get_data(cache=False))
    return jsonify(body), status


@sim_bp.get("/api/v1/additional-costs/<additional_cost_id>/status")
def payment_status(additional_cost_id):
    revision = request.args.get("revision", type=int)
    status, body = _sim().handle_status(request.headers, additional_cost_id, revision)
    return jsonify(body), status


@sim_bp.get("/")
def page():
    return render_template("finance_sim.html", invoices=_sim().list_invoices(),
                           msg=request.args.get("msg"), actions=_sim().ACTIONS)


@sim_bp.post("/actions/<action>/<additional_cost_id>")
def run_action(action, additional_cost_id):
    try:
        result = _sim().action(action, additional_cost_id)
    except (KeyError, ValueError) as exc:
        message = f"Gagal: {exc}"
    else:
        delivery = result.get("delivery")
        if delivery is None:
            message = "Selesai (tanpa callback ke Green Energy)."
        else:
            message = (f"Callback {delivery.get('payment_status')} dikirim ke Green Energy: "
                       f"HTTP {delivery.get('http_status')} {delivery.get('response')}")
    return redirect(url_for("finance_sim.page", msg=message))
