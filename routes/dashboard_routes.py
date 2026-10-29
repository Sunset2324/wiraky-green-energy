"""UI internal Green Energy: form surveyor, dashboard monitoring, detail Additional Cost."""
from flask import Blueprint, current_app, redirect, render_template, request, url_for

from core import payment_logic as logic
from core.master_price import MASTER_PRICE

ui_bp = Blueprint("ui", __name__)

DEFAULT_FORM = {
    "order_id": "", "customer_name": "Budi Santoso", "whatsapp": "081234567890",
    "package": "IndiHome Aura 3500", "approval_reference": "", "additional_cost_id": "",
}


def _svc():
    return current_app.extensions["wiraky"]


def _render_form(form, error=None, status=200):
    return render_template("surveyor_input.html", master_price=MASTER_PRICE, form=form, error=error,
                           qty={code: form.get(f"qty_{code}", "0") for code in MASTER_PRICE}), status


@ui_bp.get("/surveyor")
def surveyor_form():
    form = dict(DEFAULT_FORM)
    revise_id = request.args.get("revise")
    if revise_id:
        record = _svc().repo.get_cost(revise_id)
        if record:
            form.update(order_id=record["order_id"], customer_name=record["customer"]["name"],
                        whatsapp=record["customer"]["whatsapp"], package=record["customer"]["package"],
                        additional_cost_id=record["additional_cost_id"])
            for item in record["items"]:
                form[f"qty_{item['part_code']}"] = str(item["qty"])
    return _render_form(form)


@ui_bp.post("/surveyor")
def surveyor_submit():
    form = request.form.to_dict()
    selection = {}
    for code in MASTER_PRICE:
        raw = (form.get(f"qty_{code}") or "0").strip() or "0"
        try:
            selection[code] = int(raw)
        except ValueError:
            return _render_form(form, f"Jumlah untuk {code} harus bilangan bulat.", 422)

    approval = {
        "status": "APPROVED" if form.get("customer_approved") else "PENDING",
        "reference": (form.get("approval_reference") or "").strip(),
        "engineering_verified": bool(form.get("engineering_verified")),
    }
    try:
        record = logic.create_additional_cost(
            _svc().repo,
            order_id=form.get("order_id"),
            customer={"name": form.get("customer_name"), "whatsapp": form.get("whatsapp"),
                      "package": form.get("package")},
            selection=selection, approval=approval, now=_svc().now,
            additional_cost_id=(form.get("additional_cost_id") or None), actor="surveyor-ui")
    except logic.BusinessRuleError as exc:
        return _render_form(form, exc.message, exc.http_status)
    return redirect(url_for("ui.dashboard", focus=record["additional_cost_id"]))


@ui_bp.get("/dashboard")
def dashboard():
    svc = _svc()
    return render_template(
        "internal_dashboard.html",
        costs=list(reversed(svc.repo.list_costs())),
        alerts=list(reversed(svc.repo.list_alerts(10))),
        outbox=[e for e in svc.repo.list_outbox() if e["state"] in ("PENDING", "FAILED")],
        audit=list(reversed(svc.repo.list_audit(limit=15))),
        focus=request.args.get("focus"), msg=request.args.get("msg"), auto=request.args.get("auto"),
        mode=svc.settings.mode, has_simulator=svc.simulator is not None)


@ui_bp.get("/dashboard/<additional_cost_id>")
def cost_detail(additional_cost_id):
    svc = _svc()
    revisions = [c for c in svc.repo.list_costs() if c["additional_cost_id"] == additional_cost_id]
    if not revisions:
        return render_template("internal_dashboard.html", costs=[], alerts=[], outbox=[], audit=[], focus=None,
                               msg=f"{additional_cost_id} tidak ditemukan", auto=None,
                               mode=svc.settings.mode, has_simulator=svc.simulator is not None), 404
    return render_template("cost_detail.html", record=revisions[-1], revisions=revisions,
                           audit=svc.repo.list_audit(additional_cost_id), auto=request.args.get("auto"))


@ui_bp.post("/dashboard/reconcile/<additional_cost_id>")
def reconcile(additional_cost_id):
    result = _svc().reconciler.reconcile_one(additional_cost_id)
    detail = result.get("error") or result.get("result")
    return redirect(url_for("ui.dashboard", focus=additional_cost_id,
                            msg=f"Rekonsiliasi {additional_cost_id}: {detail}"))


@ui_bp.post("/dashboard/outbox/process")
def process_outbox():
    results = _svc().outbox.process_due()
    return redirect(url_for("ui.dashboard", msg=f"Outbox diproses: {len(results)} pengiriman"))
