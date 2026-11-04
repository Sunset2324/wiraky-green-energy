"""Rekonsiliasi: callback hilang/tertunda -> GET status dari Finance (standar bagian 02 tahap 6, bagian 09).

Hasil GET diproses lewat jalur validasi yang SAMA dengan webhook (process_payment_event),
jadi aturan nominal/idempotency/status identik dan tidak ada celah "jalur belakang".

Asumsi (dokumen tidak merinci bentuk respons GET): respons berbentuk sama dengan callback
bagian 06 (payment_status, invoice_id, amount_paid, dst). Bila event_id tidak ada, dibuat
event_id sintetis yang stabil agar polling berulang tidak diproses dua kali.
"""
from datetime import timedelta
from typing import List, Optional

from core import audit_logger
from core import payment_logic as logic
from core.payload import validate_payment_event
from core.status import NOT_REQUIRED, PAYMENT_PENDING, RECEIVED


class Reconciler:
    def __init__(self, repo, client, settings, now):
        self.repo, self.client, self.settings, self.now = repo, client, settings, now

    def candidates(self, min_age_seconds: Optional[int] = None) -> List[dict]:
        """Additional Cost yang sudah RECEIVED/PAYMENT_PENDING cukup lama tanpa kabar pembayaran."""
        age = self.settings.reconcile_after_seconds if min_age_seconds is None else min_age_seconds
        threshold = self.now() - timedelta(seconds=age)
        rows = []
        for record in self.repo.list_costs():
            if record["superseded"] or record["status"] not in (RECEIVED, PAYMENT_PENDING):
                continue
            since = record.get("last_reconcile_at") or record.get("received_at") or record["created_at"]
            if since <= threshold:
                rows.append(record)
        return rows

    def reconcile_pending(self) -> List[dict]:
        return [self.reconcile_one(r["additional_cost_id"], r["revision"]) for r in self.candidates()]

    def reconcile_one(self, additional_cost_id: str, revision: Optional[int] = None) -> dict:
        record = self.repo.get_cost(additional_cost_id, revision)
        if record is None or record["status"] == NOT_REQUIRED:
            return {"additional_cost_id": additional_cost_id, "result": "NOT_FOUND"}

        current = self.now()
        with self.repo.transaction():
            fresh = self.repo.get_cost(record["additional_cost_id"], record["revision"])
            fresh["last_reconcile_at"] = current
            self.repo.save_cost(fresh)

        result = self.client.get_status(record["additional_cost_id"], record["revision"])
        base = {"additional_cost_id": record["additional_cost_id"], "revision": record["revision"]}
        if not result.ok:
            entry = audit_logger.audit_entry("RECONCILE_FAILED", "system", current, record["additional_cost_id"],
                                             record["revision"], {"error": result.error, "http_status": result.http_status})
            self.repo.append_audit(entry)
            audit_logger.emit_audit(entry)
            return {**base, "result": "ERROR", "error": result.error}

        event = dict(result.body)
        event.setdefault("schema_version", "1.0")
        event.setdefault("event_type", "PAYMENT_CONFIRMED")
        event.setdefault("order_id", record["order_id"])
        event.setdefault("additional_cost_id", record["additional_cost_id"])
        event.setdefault("revision", record["revision"])
        if not event.get("event_id"):
            event["event_id"] = f"GET-{record['additional_cost_id']}-R{record['revision']:02d}-{event.get('payment_status')}"

        problems = validate_payment_event(event)
        if problems:
            alert = audit_logger.alert_entry(
                "RECONCILE_INVALID_RESPONSE", "Respons GET status dari Finance tidak sesuai kontrak", current,
                record["additional_cost_id"], {"problems": problems})
            self.repo.add_alert(alert)
            audit_logger.emit_alert(alert)
            return {**base, "result": "INVALID_RESPONSE", "problems": problems}

        outcome = logic.process_payment_event(self.repo, event, now=self.now, settings=self.settings,
                                              source="RECONCILIATION")
        return {**base, "result": outcome.result, "error": outcome.error,
                "status": self.repo.get_cost(record["additional_cost_id"], record["revision"])["status"]}
