"""UAT otomatis Standar API Integrated (bagian 09 skenario + bagian 10 checklist UAT positif/negatif).

Menjalankan:  python -m unittest discover -s tests -v      (dari root repo)

Pengiriman Green Energy -> Finance dan callback Finance -> Green Energy dialihkan lewat
Flask test client (tanpa socket), jam dikendalikan FakeClock, sehingga retry/backoff/replay
bisa diuji deterministik.
"""
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock
from urllib.parse import urlparse

from app import create_app
from config.settings import ConfigError, load_integration_settings
from core import payment_logic as logic
from core.payload import canonical_body
from core.security import compute_signature, utc_timestamp
from services.finance_client import TransportError

OUT_SECRET = "out-secret-aaaaaaaa"
CB_SECRET = "cb-secret-bbbbbbbbbb"
BASE_ENV = {
    "PORT": "5500", "WIRAKY_FINANCE_API_SECRET": OUT_SECRET, "WIRAKY_FINANCE_CALLBACK_SECRET": CB_SECRET,
    "SIMULATOR_AUTO_PENDING": "0", "OUTBOX_BACKOFF_BASE": "2", "OUTBOX_MAX_ATTEMPTS": "4",
}
CUSTOMER = {"name": "Pelanggan Contoh", "whatsapp": "081234567890", "package": "IndiHome Aura 3500"}
APPROVAL = {"status": "APPROVED", "reference": "APR-2026-0001", "engineering_verified": True}
# 15 x 15.000 + 2 x 125.000 + 1 x 225.000 = 700.000 (sama dengan contoh bagian 04 dokumen)
ITEMS_700K = [{"part_code": "KBL-PV-004", "qty": 15}, {"part_code": "MCB-AC-002", "qty": 2},
              {"part_code": "JASA-001", "qty": 1}]


class FakeClock:
    def __init__(self):
        self.t = datetime(2026, 10, 29, 3, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += timedelta(seconds=seconds)


class System:
    """Satu instans aplikasi + simulator Finance dengan jaringan buatan."""

    def __init__(self, **env_extra):
        self.clock = FakeClock()
        self.finance_down = False
        self.finance_override = None        # (status, body_bytes) untuk memaksa respons Finance
        self.drop_response_once = False     # Finance memproses, tetapi respons hilang di jalan
        self.finance_calls = []
        self.app = create_app({**BASE_ENV, **env_extra}, finance_transport=self._to_finance,
                              callback_transport=self._to_green, now=self.clock)
        self.svc = self.app.extensions["wiraky"]
        self.sim = self.svc.simulator
        self.http = self.app.test_client()

    # --- jaringan buatan -------------------------------------------------
    def _dispatch(self, method, url, headers, data):
        parsed = urlparse(url)
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        resp = self.app.test_client().open(path, method=method, headers=headers, data=data)
        return resp.status_code, resp.get_data()

    def _to_finance(self, method, url, headers, data, timeout):
        self.finance_calls.append((method, url, headers.get("X-Event-ID")))
        if self.finance_down:
            raise TransportError("timeout")
        if self.finance_override:
            return self.finance_override[0], self.finance_override[1]
        result = self._dispatch(method, url, headers, data)
        if self.drop_response_once and method == "POST":
            self.drop_response_once = False
            raise TransportError("respons hilang")
        return result

    def _to_green(self, method, url, headers, data, timeout):
        return self._dispatch(method, url, headers, data)

    # --- helper skenario -------------------------------------------------
    def create(self, order="WIR-2026-0001", items=None, approval=APPROVAL, **extra):
        body = {"order_id": order, "customer": CUSTOMER, "items": ITEMS_700K if items is None else items,
                "approval": approval, **extra}
        return self.http.post("/api/additional-costs", json=body)

    def send(self):
        return self.svc.outbox.process_due()

    def cost(self, ac_id, revision=None):
        return self.svc.repo.get_cost(ac_id, revision)

    def approved_and_received(self, order="WIR-2026-0001"):
        ac_id = self.create(order).get_json()["additional_cost_id"]
        self.send()
        return ac_id

    def webhook(self, event, *, secret=CB_SECRET, timestamp=None, signature=None, raw=None, headers=None):
        body = raw if raw is not None else canonical_body(event)
        ts = timestamp or utc_timestamp(self.clock())
        sig = signature if signature is not None else compute_signature(secret, ts, body)
        h = {"Content-Type": "application/json", "X-Timestamp": ts, "X-Signature": sig, **(headers or {})}
        return self.http.post("/api/v1/finance/payment-events", data=body, headers=h)

    def pay_event(self, ac_id, **over):
        rec = self.cost(ac_id)
        event = {"schema_version": "1.0", "event_id": "EVT-PAY-T-0001", "event_type": "PAYMENT_CONFIRMED",
                 "order_id": rec["order_id"], "additional_cost_id": ac_id, "revision": rec["revision"],
                 "invoice_id": f"INV-{ac_id}", "payment_reference": "TRX-T-0001",
                 "amount_paid": rec["total_amount"], "currency": "IDR", "payment_status": "PAID",
                 "paid_at": "2026-10-29T11:45:00+07:00", "verified_by": "WIRAKY_FINANCE_SYSTEM"}
        event.update(over)
        return event

    def alert_codes(self):
        return [a["code"] for a in self.svc.repo.list_alerts()]

    def audit_actions(self, ac_id=None):
        return [a["action"] for a in self.svc.repo.list_audit(ac_id)]


# ===========================================================================
# UAT positif
# ===========================================================================
class PositiveFlowTests(unittest.TestCase):
    def setUp(self):
        self.s = System()

    def test_approved_received_paid(self):
        resp = self.s.create()
        self.assertEqual(resp.status_code, 201)
        record = resp.get_json()
        ac_id = record["additional_cost_id"]
        self.assertEqual((record["status"], record["total_amount"]), ("APPROVED", 700000))
        self.assertEqual(record["event_id"], f"EVT-{ac_id}-R01")

        self.s.send()                                         # outbox -> Finance
        rec = self.s.cost(ac_id)
        self.assertEqual(rec["status"], "RECEIVED")           # 202 RECEIVED bukan bukti bayar
        self.assertTrue(rec["finance_reference"].startswith("FIN-"))
        self.assertIsNone(rec["paid_at"])

        out = self.s.sim.action("pay", ac_id)                 # Finance: bank memverifikasi, kirim callback
        self.assertEqual(out["delivery"]["http_status"], 200)
        rec = self.s.cost(ac_id)
        self.assertEqual(rec["status"], "PAID")
        self.assertEqual(rec["verified_by"], "WIRAKY_FINANCE_SYSTEM")
        self.assertTrue(rec["payment_reference"].startswith("TRX-SIM-"))
        self.assertEqual(rec["history"][-1]["to"], "PAID")

    def test_payload_matches_standard_section_04(self):
        ac_id = self.s.create().get_json()["additional_cost_id"]
        payload = self.s.cost(ac_id)["payload"]
        self.assertEqual(payload["schema_version"], "1.0")
        self.assertEqual(payload["event_type"], "ADDITIONAL_COST_APPROVED")
        self.assertEqual(payload["source"], "GREEN_ENERGY")
        self.assertEqual(payload["currency"], "IDR")
        self.assertEqual(payload["payment_status"], "NOT_BILLED")
        self.assertEqual(sum(i["subtotal"] for i in payload["additional_items"]), payload["total_amount"])
        self.assertEqual(payload["customer"]["whatsapp"], "+6281234567890")
        self.assertEqual(set(payload["approval"]), {"status", "reference", "approved_at"})

    def test_request_headers_and_signature_sent_to_finance(self):
        captured = {}
        original = self.s.sim.handle_submission

        def spy(headers, body):
            captured.update(headers={k.title(): v for k, v in headers.items()}, body=body)
            return original(headers, body)
        self.s.sim.handle_submission = spy
        ac_id = self.s.create().get_json()["additional_cost_id"]
        self.s.send()
        h = captured["headers"]
        self.assertEqual(h["X-Event-Id"], f"EVT-{ac_id}-R01")
        self.assertEqual(h["Idempotency-Key"], h["X-Event-Id"])
        self.assertEqual(h["X-Signature"], compute_signature(OUT_SECRET, h["X-Timestamp"], captured["body"]))
        self.assertTrue(h["X-Timestamp"].endswith("Z"))

    def test_reconciliation_recovers_lost_callback(self):
        ac_id = self.s.approved_and_received()
        self.s.sim.action("pay_silent", ac_id)                # Finance sudah menerima uang, callback tidak dikirim
        self.assertEqual(self.s.cost(ac_id)["status"], "RECEIVED")
        result = self.s.svc.reconciler.reconcile_one(ac_id)   # GET status
        self.assertEqual((result["result"], result["status"]), ("RECORDED", "PAID"))
        self.assertEqual(self.s.svc.reconciler.reconcile_one(ac_id)["result"], "DUPLICATE")

    def test_reconcile_pending_only_after_threshold(self):
        ac_id = self.s.approved_and_received()
        self.s.sim.action("pay_silent", ac_id)
        self.assertEqual(self.s.svc.reconciler.reconcile_pending(), [])         # terlalu baru
        self.s.clock.advance(self.s.svc.settings.reconcile_after_seconds + 1)
        results = self.s.svc.reconciler.reconcile_pending()
        self.assertEqual([r["result"] for r in results], ["RECORDED"])
        self.assertEqual(self.s.cost(ac_id)["status"], "PAID")

    def test_pending_callback_moves_to_payment_pending(self):
        ac_id = self.s.approved_and_received()
        self.s.sim.emit(ac_id, "PENDING")
        self.assertEqual(self.s.cost(ac_id)["status"], "PAYMENT_PENDING")

    def test_revision_cancels_previous_and_pays_new(self):
        ac_id = self.s.approved_and_received()
        resp = self.s.create(items=[{"part_code": "JASA-001", "qty": 2}], additional_cost_id=ac_id)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.get_json()["revision"], 2)
        self.assertTrue(self.s.cost(ac_id, 1)["superseded"])
        self.s.send()
        self.assertEqual(self.s.cost(ac_id, 2)["status"], "RECEIVED")
        self.assertEqual({i["revision"]: i["status"] for i in self.s.sim.list_invoices()},
                         {1: "CANCELLED", 2: "PENDING"})
        self.s.sim.action("pay", ac_id)
        self.assertEqual(self.s.cost(ac_id, 2)["status"], "PAID")
        self.assertEqual(self.s.cost(ac_id, 2)["total_amount"], 450000)

    def test_refund_after_paid_is_kept_with_audit(self):
        ac_id = self.s.approved_and_received()
        self.s.sim.action("pay", ac_id)
        self.s.sim.action("refund", ac_id)
        rec = self.s.cost(ac_id)
        self.assertEqual(rec["status"], "REFUNDED")
        self.assertTrue(rec["needs_review"])
        self.assertEqual(rec["payment_reference"], rec["payment_reference"])     # jejak pembayaran tidak dihapus
        self.assertIn("REFUND_AFTER_PAID", self.s.alert_codes())
        self.assertIn("REFUND_RECORDED", self.s.audit_actions(ac_id))


# ===========================================================================
# Aturan pemicu (bagian 01)
# ===========================================================================
class TriggerRuleTests(unittest.TestCase):
    def setUp(self):
        self.s = System()

    def test_zero_additional_cost_makes_no_api_call(self):
        for items in ([], [{"part_code": "JASA-001", "qty": 0}]):
            resp = self.s.create(order=f"WIR-{len(items)}", items=items, approval=None)
            self.assertEqual(resp.status_code, 201)
            self.assertEqual(resp.get_json()["status"], "NOT_REQUIRED")
        self.assertEqual(self.s.send(), [])
        self.assertEqual(self.s.finance_calls, [])
        self.assertEqual(self.s.svc.repo.list_outbox(), [])

    def test_not_sent_without_engineering_or_customer_approval(self):
        for approval in (None, {"status": "APPROVED", "reference": "APR-1"},                      # tanpa Engineering
                         {"status": "PENDING", "reference": "APR-1", "engineering_verified": True},  # pelanggan belum
                         {"status": "APPROVED", "reference": " ", "engineering_verified": True}):   # tanpa referensi
            resp = self.s.create(order="WIR-X", approval=approval)
            self.assertEqual((resp.status_code, resp.get_json()["error"]), (422, "NOT_APPROVED"))
        self.assertEqual(self.s.svc.repo.list_costs(), [])
        self.assertEqual(self.s.finance_calls, [])

    def test_received_is_not_paid(self):
        ac_id = self.s.approved_and_received()
        rec = self.s.cost(ac_id)
        self.assertEqual(rec["status"], "RECEIVED")
        self.assertNotEqual(rec["status"], "PAID")

    def test_no_double_billing_for_same_order(self):
        self.s.create()
        again = self.s.create()
        self.assertEqual((again.status_code, again.get_json()["error"]), (409, "ACTIVE_COST_EXISTS"))
        self.s.send()
        self.assertEqual(len(self.s.sim.list_invoices()), 1)

    def test_revision_of_paid_cost_is_refused(self):
        ac_id = self.s.approved_and_received()
        self.s.sim.action("pay", ac_id)
        resp = self.s.create(items=[{"part_code": "JASA-001", "qty": 1}], additional_cost_id=ac_id)
        self.assertEqual((resp.status_code, resp.get_json()["error"]), (409, "REVISION_NOT_ALLOWED"))

    def test_input_validation(self):
        cases = [
            ({"items": [{"part_code": "TIDAK-ADA", "qty": 1}]}, 422, "INVALID_ITEMS"),
            ({"items": [{"part_code": "JASA-001", "qty": -1}]}, 422, "INVALID_ITEMS"),
            ({"items": [{"part_code": "JASA-001", "qty": "1"}]}, 422, "INVALID_ITEMS"),
            ({"customer": {**CUSTOMER, "whatsapp": "abc"}}, 422, "WHATSAPP_INVALID"),
            ({"customer": {**CUSTOMER, "name": ""}}, 422, "CUSTOMER_NAME_REQUIRED"),
            ({"order_id": ""}, 422, "ORDER_ID_REQUIRED"),
            ({"items": "bukan list"}, 400, "INVALID_ITEMS"),
            ({"items": [{"part_code": ["x"], "qty": 1}]}, 400, "INVALID_ITEMS"),
        ]
        for override, status, code in cases:
            body = {"order_id": "WIR-V", "customer": CUSTOMER, "items": ITEMS_700K, "approval": APPROVAL, **override}
            resp = self.s.http.post("/api/additional-costs", json=body)
            self.assertEqual((resp.status_code, resp.get_json()["error"]), (status, code), override)
        raw = self.s.http.post("/api/additional-costs", data="{bukan json", content_type="application/json")
        self.assertEqual(raw.status_code, 400)

    def test_price_is_computed_server_side(self):
        body = {"order_id": "WIR-P", "customer": CUSTOMER, "approval": APPROVAL,
                "items": [{"part_code": "JASA-001", "qty": 1, "unit_price": 1, "subtotal": 1}],
                "total_amount": 1}
        resp = self.s.http.post("/api/additional-costs", json=body)
        self.assertEqual(resp.get_json()["total_amount"], 225000)


# ===========================================================================
# Error, retry, idempotency pengiriman (bagian 05 dan 09)
# ===========================================================================
class SendingTests(unittest.TestCase):
    def setUp(self):
        self.s = System()

    def test_connection_down_retries_with_same_event_id_and_backoff(self):
        ac_id = self.s.create().get_json()["additional_cost_id"]
        self.s.finance_down = True
        self.s.send()
        entry = self.s.svc.repo.get_outbox(f"EVT-{ac_id}-R01")
        self.assertEqual((entry["state"], entry["attempts"]), ("PENDING", 1))
        self.assertEqual(self.s.cost(ac_id)["status"], "APPROVED")          # data tidak hilang
        self.assertEqual(self.s.send(), [])                                  # belum jatuh tempo
        self.s.clock.advance(2)
        self.s.send()                                                        # percobaan 2, jeda berikutnya 4 dtk
        entry = self.s.svc.repo.get_outbox(f"EVT-{ac_id}-R01")
        self.assertEqual(entry["attempts"], 2)
        self.assertEqual(entry["next_attempt_at"], self.s.clock() + timedelta(seconds=4))
        self.s.finance_down = False
        self.s.clock.advance(4)
        self.s.send()
        self.assertEqual(self.s.cost(ac_id)["status"], "RECEIVED")
        self.assertEqual({call[2] for call in self.s.finance_calls}, {f"EVT-{ac_id}-R01"})   # event_id SAMA
        self.assertEqual(len(self.s.sim.list_invoices()), 1)                                  # tanpa invoice ganda
        self.assertIn("SEND_RETRY_SCHEDULED", self.s.audit_actions(ac_id))

    def test_retry_exhausted_goes_to_review_and_alerts_admin(self):
        ac_id = self.s.create().get_json()["additional_cost_id"]
        self.s.finance_down = True
        for _ in range(4):                                                   # OUTBOX_MAX_ATTEMPTS=4
            self.s.send()
            self.s.clock.advance(60)
        rec = self.s.cost(ac_id)
        self.assertEqual((rec["status"], rec["needs_review"]), ("REVIEW", True))
        self.assertEqual(self.s.svc.repo.get_outbox(f"EVT-{ac_id}-R01")["state"], "FAILED")
        self.assertIn("SEND_FAILED", self.s.alert_codes())
        self.assertIsNotNone(rec["payload"])                                 # payload tetap tersimpan

    def test_finance_5xx_and_429_are_retried(self):
        for status in (500, 503, 429):
            s = System()
            ac_id = s.create().get_json()["additional_cost_id"]
            s.finance_override = (status, b'{"error":"BUSY"}')
            s.send()
            self.assertEqual(s.svc.repo.get_outbox(f"EVT-{ac_id}-R01")["state"], "PENDING", status)

    def test_finance_4xx_is_not_retried(self):
        for status in (400, 401, 403, 409, 422):
            s = System()
            ac_id = s.create().get_json()["additional_cost_id"]
            s.finance_override = (status, b'{"error":"X"}')
            s.send()
            self.assertEqual(s.svc.repo.get_outbox(f"EVT-{ac_id}-R01")["state"], "FAILED", status)
            self.assertEqual(s.cost(ac_id)["status"], "REVIEW", status)
            self.assertEqual(len(s.finance_calls), 1, status)

    def test_unexpected_2xx_body_is_retried_not_trusted(self):
        ac_id = self.s.create().get_json()["additional_cost_id"]
        self.s.finance_override = (202, b'{"status":"OK"}')
        self.s.send()
        self.assertEqual(self.s.cost(ac_id)["status"], "APPROVED")
        self.assertEqual(self.s.svc.repo.get_outbox(f"EVT-{ac_id}-R01")["state"], "PENDING")

    def test_lost_response_then_retry_returns_old_finance_reference(self):
        ac_id = self.s.create().get_json()["additional_cost_id"]
        self.s.drop_response_once = True
        self.s.send()                                                        # Finance memproses, respons hilang
        self.assertEqual(self.s.cost(ac_id)["status"], "APPROVED")
        self.assertEqual(len(self.s.sim.list_invoices()), 1)
        reference = self.s.sim.list_invoices()[0]["finance_reference"]
        self.s.clock.advance(2)
        self.s.send()                                                        # retry, event_id sama
        self.assertEqual(len(self.s.sim.list_invoices()), 1)                 # Idempotency-Key mencegah invoice ganda
        self.assertEqual(self.s.cost(ac_id)["finance_reference"], reference)
        self.assertEqual(self.s.cost(ac_id)["status"], "RECEIVED")

    def test_callback_before_ack_implies_ack_and_stops_resend(self):
        ac_id = self.s.create().get_json()["additional_cost_id"]
        self.s.drop_response_once = True
        self.s.send()
        self.s.sim.action("pay", ac_id)                                      # callback PAID datang saat masih APPROVED
        rec = self.s.cost(ac_id)
        self.assertEqual(rec["status"], "PAID")
        self.assertIn("ACK_IMPLIED_BY_CALLBACK", self.s.audit_actions(ac_id))
        calls = len(self.s.finance_calls)
        self.s.clock.advance(60)
        self.s.send()
        self.assertEqual(len(self.s.finance_calls), calls)                   # tidak ada kiriman ulang

    def test_finance_simulator_rejects_bad_requests(self):
        ac_id = self.s.create().get_json()["additional_cost_id"]
        payload = self.s.cost(ac_id)["payload"]
        body = canonical_body(payload)
        ts = utc_timestamp(self.s.clock())
        good = {"X-Timestamp": ts, "X-Signature": compute_signature(OUT_SECRET, ts, body),
                "X-Event-ID": payload["event_id"], "Idempotency-Key": payload["event_id"]}
        sim = self.s.sim
        self.assertEqual(sim.handle_submission({**good, "X-Signature": "0" * 64}, body)[1]["error"], "INVALID_SIGNATURE")
        stale = utc_timestamp(self.s.clock() - timedelta(minutes=10))
        self.assertEqual(sim.handle_submission({**good, "X-Timestamp": stale}, body)[1]["error"], "INVALID_TIMESTAMP")
        self.assertEqual(sim.handle_submission({**good, "Idempotency-Key": "lain"}, body)[1]["error"],
                         "IDEMPOTENCY_KEY_MISMATCH")
        broken = dict(payload, total_amount=1)                               # subtotal tidak cocok dengan total
        bbody = canonical_body(broken)
        sig = {**good, "X-Signature": compute_signature(OUT_SECRET, ts, bbody)}
        status, resp = sim.handle_submission(sig, bbody)
        self.assertEqual((status, resp["error"]), (422, "INVALID_PAYLOAD"))
        self.assertIn("ITEMS_TOTAL_MISMATCH", resp["details"])
        # idempotency: event_id sama dengan isi berbeda -> konflik; isi sama -> respons lama
        self.assertEqual(sim.handle_submission(good, body)[0], 202)
        again = sim.handle_submission({**good, "X-Signature": compute_signature(OUT_SECRET, ts, body)}, body)
        self.assertEqual(again[0], 202)
        changed = dict(payload, order_id="WIR-LAIN")
        cbody = canonical_body(changed)
        conflict = sim.handle_submission({**good, "X-Signature": compute_signature(OUT_SECRET, ts, cbody)}, cbody)
        self.assertEqual((conflict[0], conflict[1]["error"]), (409, "IDEMPOTENCY_CONFLICT"))


# ===========================================================================
# Webhook: autentikasi dan validasi (bagian 07 dan 08)
# ===========================================================================
class WebhookSecurityTests(unittest.TestCase):
    def setUp(self):
        self.s = System()
        self.ac_id = self.s.approved_and_received()

    def error(self, resp):
        return resp.get_json().get("error")

    def test_invalid_signature_is_401_and_changes_nothing(self):
        event = self.s.pay_event(self.ac_id)
        resp = self.s.webhook(event, signature="0" * 64)
        self.assertEqual((resp.status_code, self.error(resp)), (401, "INVALID_SIGNATURE"))
        self.assertEqual(self.s.cost(self.ac_id)["status"], "RECEIVED")

    def test_wrong_secret_and_outbound_secret_are_rejected(self):
        event = self.s.pay_event(self.ac_id)
        for secret in ("secret-penyerang", OUT_SECRET):      # secret arah kirim tidak boleh dipakai untuk callback
            self.assertEqual(self.s.webhook(event, secret=secret).status_code, 401)

    def test_tampered_body_is_rejected(self):
        event = self.s.pay_event(self.ac_id)
        ts = utc_timestamp(self.s.clock())
        sig = compute_signature(CB_SECRET, ts, canonical_body(event))
        tampered = canonical_body(dict(event, amount_paid=1))
        self.assertEqual(self.s.webhook(None, raw=tampered, timestamp=ts, signature=sig).status_code, 401)

    def test_stale_and_future_timestamps_are_rejected_replay(self):
        event = self.s.pay_event(self.ac_id)
        for delta in (-301, 301, -86400):
            ts = utc_timestamp(self.s.clock() + timedelta(seconds=delta))
            resp = self.s.webhook(event, timestamp=ts)
            self.assertEqual((resp.status_code, self.error(resp)), (401, "INVALID_TIMESTAMP"), delta)
        for bad in ("", "kemarin", "2026-10-29 03:00:00"):
            self.assertEqual(self.s.webhook(event, timestamp=bad, signature="x").status_code, 401)
        self.assertEqual(self.s.cost(self.ac_id)["status"], "RECEIVED")

    def test_missing_and_malformed_signature_headers(self):
        event = self.s.pay_event(self.ac_id)
        raw = self.s.http.post("/api/v1/finance/payment-events", data=canonical_body(event),
                               headers={"Content-Type": "application/json"})
        self.assertEqual(raw.status_code, 401)
        non_ascii = self.s.webhook(event, signature="é" * 64)                # tidak boleh jadi 500
        self.assertEqual(non_ascii.status_code, 401)
        upper = self.s.webhook(event, signature=compute_signature(CB_SECRET, utc_timestamp(self.s.clock()),
                                                                  canonical_body(event)).upper())
        self.assertEqual(upper.status_code, 200)

    def test_invalid_json_event_type_currency_and_structure(self):
        ts = utc_timestamp(self.s.clock())
        bad = b"{bukan json"
        resp = self.s.webhook(None, raw=bad, signature=compute_signature(CB_SECRET, ts, bad), timestamp=ts)
        self.assertEqual((resp.status_code, self.error(resp)), (400, "INVALID_JSON"))
        arr = b"[1,2]"
        resp = self.s.webhook(None, raw=arr, signature=compute_signature(CB_SECRET, ts, arr), timestamp=ts)
        self.assertEqual(resp.status_code, 400)
        for over, status, code in [({"event_type": "LAIN"}, 422, "INVALID_EVENT_TYPE"),
                                   ({"currency": "USD"}, 422, "INVALID_CURRENCY"),
                                   ({"payment_status": "ANEH"}, 422, "INVALID_PAYLOAD"),
                                   ({"event_id": ""}, 422, "INVALID_PAYLOAD"),
                                   ({"revision": "1"}, 422, "INVALID_PAYLOAD"),
                                   ({"paid_at": "kemarin"}, 422, "INVALID_PAYLOAD"),
                                   ({"amount_paid": "700000"}, 422, "INVALID_PAYLOAD")]:
            resp = self.s.webhook(self.s.pay_event(self.ac_id, **over))
            self.assertEqual((resp.status_code, self.error(resp)), (status, code), over)
        resp = self.s.webhook(self.s.pay_event(self.ac_id), headers={"X-Event-ID": "EVT-LAIN"})
        self.assertEqual((resp.status_code, self.error(resp)), (422, "EVENT_ID_MISMATCH"))
        self.assertEqual(self.s.cost(self.ac_id)["status"], "RECEIVED")

    def test_unverified_source_cannot_make_paid(self):
        resp = self.s.webhook(self.s.pay_event(self.ac_id, verified_by="WHATSAPP_CUSTOMER"))
        self.assertEqual((resp.status_code, self.error(resp)), (422, "INVALID_VERIFIER"))
        self.assertEqual(self.s.cost(self.ac_id)["status"], "RECEIVED")

    def test_unknown_reference_order_mismatch_and_revision(self):
        for over, code in [({"additional_cost_id": "AC-2026-9999"}, "UNKNOWN_REFERENCE"),
                           ({"order_id": "ORDER-LAIN"}, "ORDER_MISMATCH"),
                           ({"revision": 7}, "REVISION_MISMATCH")]:
            resp = self.s.webhook(self.s.pay_event(self.ac_id, event_id=f"EVT-{code}", **over))
            self.assertEqual((resp.status_code, self.error(resp)), (409, code))
        self.assertEqual(self.s.cost(self.ac_id)["status"], "RECEIVED")

    def test_event_for_superseded_revision_is_flagged(self):
        self.s.create(items=[{"part_code": "JASA-001", "qty": 2}], additional_cost_id=self.ac_id)
        resp = self.s.webhook(self.s.pay_event(self.ac_id, revision=1))
        self.assertEqual((resp.status_code, self.error(resp)), (409, "REVISION_SUPERSEDED"))
        self.assertIn("REVISION_SUPERSEDED", self.s.alert_codes())

    def test_rate_limit(self):
        s = System(WEBHOOK_RATE_LIMIT_PER_MINUTE="3")
        codes = [s.http.post("/api/v1/finance/payment-events", data=b"{}").status_code for _ in range(5)]
        self.assertEqual(codes[:3], [401, 401, 401])
        self.assertEqual(codes[3:], [429, 429])

    def test_secret_rotation_accepts_previous_secret_only_when_configured(self):
        s = System(WIRAKY_FINANCE_CALLBACK_SECRET_PREVIOUS="secret-lama-cccccccc")
        ac_id = s.approved_and_received()
        event = s.pay_event(ac_id)
        self.assertEqual(s.webhook(event, secret="secret-lama-cccccccc").status_code, 200)
        self.assertEqual(s.webhook(s.pay_event(ac_id, event_id="E2"), secret="entah-siapa").status_code, 401)
        self.assertEqual(self.s.webhook(self.s.pay_event(self.ac_id), secret="secret-lama-cccccccc").status_code, 401)

    def test_body_too_large(self):
        resp = self.s.http.post("/api/v1/finance/payment-events", data=b"x" * (1024 * 1024 + 1))
        self.assertEqual(resp.status_code, 413)


# ===========================================================================
# Validasi bisnis & idempotency penerimaan (bagian 08 dan 09)
# ===========================================================================
class PaymentValidationTests(unittest.TestCase):
    def setUp(self):
        self.s = System()
        self.ac_id = self.s.approved_and_received()

    def test_duplicate_paid_callback_is_idempotent(self):
        event = self.s.pay_event(self.ac_id)
        first = self.s.webhook(event)
        audit_after_first = len(self.s.svc.repo.list_audit())
        again = self.s.webhook(event)
        self.assertEqual((first.get_json()["result"], again.get_json()["result"]), ("RECORDED", "DUPLICATE"))
        self.assertEqual(again.status_code, 200)
        self.assertEqual(self.s.cost(self.ac_id)["status"], "PAID")
        history = [h["to"] for h in self.s.cost(self.ac_id)["history"]]
        self.assertEqual(history.count("PAID"), 1)                            # tidak ada perubahan kedua
        self.assertEqual(self.s.alert_codes(), [])
        self.assertLessEqual(len(self.s.svc.repo.list_audit()) - audit_after_first, 1)

    def test_replayed_valid_request_within_window_is_duplicate(self):
        event = self.s.pay_event(self.ac_id)
        ts = utc_timestamp(self.s.clock())
        body = canonical_body(event)
        sig = compute_signature(CB_SECRET, ts, body)
        self.assertEqual(self.s.webhook(None, raw=body, timestamp=ts, signature=sig).get_json()["result"], "RECORDED")
        self.s.clock.advance(30)
        replay = self.s.webhook(None, raw=body, timestamp=ts, signature=sig)
        self.assertEqual(replay.get_json()["result"], "DUPLICATE")
        self.s.clock.advance(400)                                             # di luar jendela: ditolak total
        self.assertEqual(self.s.webhook(None, raw=body, timestamp=ts, signature=sig).status_code, 401)

    def test_amount_mismatch_rejects_paid_logs_conflict_and_alerts_admin(self):
        for wrong in (699999, 700001, 0, 1400000, 700000.5):
            resp = self.s.webhook(self.s.pay_event(self.ac_id, amount_paid=wrong, event_id=f"EVT-W-{wrong}"))
            self.assertEqual((resp.status_code, resp.get_json()["error"]), (409, "AMOUNT_MISMATCH"), wrong)
        rec = self.s.cost(self.ac_id)
        self.assertEqual(rec["status"], "RECEIVED")                           # jangan auto-update
        self.assertIsNone(rec["paid_at"])
        self.assertIn("AMOUNT_MISMATCH", self.s.alert_codes())
        self.assertIn("REJECTED_AMOUNT_MISMATCH", self.s.audit_actions(self.ac_id))
        ok = self.s.webhook(self.s.pay_event(self.ac_id, event_id="EVT-W-OK"))   # nominal benar tetap bisa
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.s.cost(self.ac_id)["status"], "PAID")

    def test_float_and_int_amounts_compare_exactly(self):
        resp = self.s.webhook(self.s.pay_event(self.ac_id, amount_paid=700000.0))
        self.assertEqual(resp.status_code, 200)

    def test_second_paid_with_other_reference_is_flagged_as_double_payment(self):
        self.s.webhook(self.s.pay_event(self.ac_id))
        same_ref = self.s.webhook(self.s.pay_event(self.ac_id, event_id="EVT-2"))
        self.assertEqual(same_ref.get_json()["result"], "ALREADY_PAID")
        other = self.s.webhook(self.s.pay_event(self.ac_id, event_id="EVT-3", payment_reference="TRX-LAIN"))
        self.assertEqual((other.status_code, other.get_json()["error"]), (409, "PAYMENT_REFERENCE_CONFLICT"))
        self.assertEqual(self.s.cost(self.ac_id)["payment_reference"], "TRX-T-0001")

    def test_invoice_mismatch_after_pending_event(self):
        self.s.sim.emit(self.ac_id, "PENDING")                                # Finance memberi invoice_id
        resp = self.s.webhook(self.s.pay_event(self.ac_id, invoice_id="INV-LAIN"))
        self.assertEqual((resp.status_code, resp.get_json()["error"]), (409, "INVOICE_MISMATCH"))

    def test_currency_and_status_on_record_must_match(self):
        resp = self.s.webhook(self.s.pay_event(self.ac_id))
        self.assertEqual(resp.status_code, 200)

    def test_non_final_statuses_never_make_paid(self):
        for status, expected in (("PENDING", "PAYMENT_PENDING"), ("FAILED", "FAILED"), ("EXPIRED", "EXPIRED")):
            s = System()
            ac_id = s.approved_and_received()
            resp = s.webhook(s.pay_event(ac_id, payment_status=status, event_id=f"EVT-{status}"))
            self.assertEqual((resp.status_code, resp.get_json()["result"]), (200, "NOT_PAID"), status)
            self.assertEqual(s.cost(ac_id)["status"], expected)
            self.assertIsNone(s.cost(ac_id)["paid_at"])

    def test_paid_after_expired_is_accepted_but_flagged(self):
        self.s.webhook(self.s.pay_event(self.ac_id, payment_status="EXPIRED", event_id="EVT-EXP"))
        resp = self.s.webhook(self.s.pay_event(self.ac_id, event_id="EVT-LATE"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.s.cost(self.ac_id)["status"], "PAID")
        self.assertIn("LATE_PAYMENT", self.s.alert_codes())

    def test_paid_is_never_downgraded(self):
        self.s.webhook(self.s.pay_event(self.ac_id))
        for status in ("EXPIRED", "FAILED"):
            resp = self.s.webhook(self.s.pay_event(self.ac_id, payment_status=status, event_id=f"EVT-{status}"))
            self.assertEqual((resp.status_code, resp.get_json()["error"]), (409, "STATE_CONFLICT"))
        stale = self.s.webhook(self.s.pay_event(self.ac_id, payment_status="PENDING", event_id="EVT-OLD-PENDING"))
        self.assertEqual((stale.status_code, stale.get_json()["result"]), (200, "NOT_PAID"))
        self.assertEqual(self.s.cost(self.ac_id)["status"], "PAID")

    def test_refund_requires_paid(self):
        resp = self.s.webhook(self.s.pay_event(self.ac_id, payment_status="REFUNDED", event_id="EVT-RF"))
        self.assertEqual((resp.status_code, resp.get_json()["error"]), (409, "STATE_CONFLICT"))

    def test_zero_cost_order_cannot_be_paid(self):
        ac_id = self.s.create(order="WIR-NOL", items=[], approval=None).get_json()["additional_cost_id"]
        resp = self.s.webhook(self.s.pay_event(ac_id, order_id="WIR-NOL", amount_paid=0))
        self.assertEqual((resp.status_code, resp.get_json()["error"]), (409, "UNKNOWN_REFERENCE"))


# ===========================================================================
# Atomisitas & repository
# ===========================================================================
class AtomicityTests(unittest.TestCase):
    def test_failure_midway_rolls_back_status_event_and_audit(self):
        s = System()
        ac_id = s.approved_and_received()
        event = s.pay_event(ac_id)
        audit_before = len(s.svc.repo.list_audit())
        with mock.patch.object(s.svc.repo, "mark_event", side_effect=RuntimeError("DB mati")):
            with self.assertRaises(RuntimeError):
                logic.process_payment_event(s.svc.repo, event, now=s.clock, settings=s.svc.settings)
        self.assertEqual(s.cost(ac_id)["status"], "RECEIVED")
        self.assertFalse(s.svc.repo.has_event(event["event_id"]))
        self.assertEqual(len(s.svc.repo.list_audit()), audit_before)
        self.assertEqual(s.webhook(event).status_code, 200)                    # event yang sama masih bisa diproses
        self.assertEqual(s.cost(ac_id)["status"], "PAID")

    def test_failed_creation_leaves_no_partial_data(self):
        s = System()
        with mock.patch.object(s.svc.repo, "put_outbox", side_effect=RuntimeError("DB mati")):
            with self.assertRaises(RuntimeError):
                logic.create_additional_cost(s.svc.repo, order_id="WIR-ATOM", customer=CUSTOMER,
                                             selection={"JASA-001": 1}, approval=APPROVAL, now=s.clock)
        self.assertEqual(s.svc.repo.list_costs(), [])
        self.assertEqual(s.svc.repo.list_audit(), [])


# ===========================================================================
# Konfigurasi (bagian 07, 10)
# ===========================================================================
class ConfigTests(unittest.TestCase):
    REAL = {"WIRAKY_FINANCE_API_URL": "https://finance.wiraky.example/api/v1/additional-costs",
            "WIRAKY_FINANCE_API_SECRET": "a" * 20, "WIRAKY_FINANCE_CALLBACK_SECRET": "b" * 20}

    def test_secrets_must_differ_per_direction(self):
        with self.assertRaises(ConfigError):
            load_integration_settings({**BASE_ENV, "WIRAKY_FINANCE_CALLBACK_SECRET": OUT_SECRET})
        with self.assertRaises(ConfigError):
            load_integration_settings({**self.REAL, "WIRAKY_FINANCE_CALLBACK_SECRET": "a" * 20})

    def test_production_mode_requires_https_secrets_and_length(self):
        self.assertEqual(load_integration_settings(self.REAL).mode, "FINANCE_API")
        for override in ({"WIRAKY_FINANCE_API_URL": "http://finance.wiraky.example/x"},
                         {"WIRAKY_FINANCE_API_SECRET": ""}, {"WIRAKY_FINANCE_CALLBACK_SECRET": ""},
                         {"WIRAKY_FINANCE_API_SECRET": "pendek"}):
            with self.assertRaises(ConfigError, msg=str(override)):
                load_integration_settings({**self.REAL, **override})

    def test_simulator_default_uses_ephemeral_secrets_never_hardcoded(self):
        a = load_integration_settings({"PORT": "5500"})
        b = load_integration_settings({"PORT": "5500"})
        self.assertEqual(a.mode, "SIMULATOR")
        self.assertTrue(a.ephemeral_secrets)
        self.assertNotEqual(a.finance_api_secret, a.callback_secret)
        self.assertNotEqual(a.finance_api_secret, b.finance_api_secret)
        self.assertTrue(a.finance_api_url.startswith("http://127.0.0.1:5500/finance-sim/"))

    def test_production_mode_does_not_expose_simulator(self):
        app = create_app(dict(self.REAL))
        client = app.test_client()
        self.assertEqual(client.get("/finance-sim/").status_code, 404)
        self.assertIsNone(app.extensions["wiraky"].simulator)
        self.assertEqual(client.get("/health").get_json()["mode"], "FINANCE_API")

    def test_invalid_number_in_env(self):
        with self.assertRaises(ConfigError):
            load_integration_settings({**BASE_ENV, "OUTBOX_MAX_ATTEMPTS": "banyak"})


# ===========================================================================
# UI
# ===========================================================================
class UiTests(unittest.TestCase):
    def setUp(self):
        self.s = System()

    def form(self, **over):
        data = {"order_id": "WIR-UI-1", "customer_name": "Budi", "whatsapp": "081234567890",
                "package": "IndiHome Aura 3500", "qty_JASA-001": "1", "qty_KBL-PV-004": "15",
                "engineering_verified": "on", "customer_approved": "on", "approval_reference": "APR-UI-1"}
        data.update(over)
        return data

    def test_pages_render(self):
        for path in ("/surveyor", "/dashboard", "/finance-sim/", "/health"):
            self.assertEqual(self.s.http.get(path).status_code, 200, path)
        self.assertEqual(self.s.http.get("/").status_code, 302)
        self.assertEqual(self.s.http.get("/dashboard/AC-TIDAK-ADA").status_code, 404)
        self.assertEqual(self.s.http.get("/dashboard?auto=1").status_code, 200)

    def test_form_flow_to_paid_visible_on_dashboard(self):
        resp = self.s.http.post("/surveyor", data=self.form())
        self.assertEqual(resp.status_code, 302)
        ac_id = self.s.svc.repo.list_costs()[0]["additional_cost_id"]
        self.assertIn(ac_id, resp.headers["Location"])
        self.s.send()
        page = self.s.http.get(f"/dashboard/{ac_id}").get_data(as_text=True)
        self.assertIn("RECEIVED", page)
        sim_resp = self.s.http.post(f"/finance-sim/actions/pay/{ac_id}")
        self.assertEqual(sim_resp.status_code, 302)
        dashboard = self.s.http.get("/dashboard").get_data(as_text=True)
        self.assertIn("PAID", dashboard)
        self.assertIn("Rp 450.000", dashboard)                                   # format rupiah Indonesia
        self.assertEqual(self.s.cost(ac_id)["status"], "PAID")

    def test_form_without_approval_is_rejected_with_message(self):
        data = self.form()
        del data["customer_approved"]
        resp = self.s.http.post("/surveyor", data=data)
        self.assertEqual(resp.status_code, 422)
        self.assertIn("verifikasi Engineering", resp.get_data(as_text=True))
        self.assertEqual(self.s.svc.repo.list_costs(), [])

    def test_form_bad_quantity_and_html_escaping(self):
        resp = self.s.http.post("/surveyor", data=self.form(**{"qty_JASA-001": "abc"}))
        self.assertEqual(resp.status_code, 422)
        xss = self.s.http.post("/surveyor", data=self.form(order_id="<script>alert(1)</script>",
                                                           **{"qty_JASA-001": "x"}))
        self.assertNotIn("<script>alert(1)</script>", xss.get_data(as_text=True))

    def test_form_zero_quantity_means_no_billing(self):
        data = self.form(**{"qty_JASA-001": "0", "qty_KBL-PV-004": "0"})
        del data["customer_approved"]
        resp = self.s.http.post("/surveyor", data=data)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.s.svc.repo.list_costs()[0]["status"], "NOT_REQUIRED")
        self.assertEqual(self.s.svc.repo.list_outbox(), [])

    def test_dashboard_reconcile_button(self):
        ac_id = self.s.approved_and_received()
        self.s.sim.action("pay_silent", ac_id)
        resp = self.s.http.post(f"/dashboard/reconcile/{ac_id}")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.s.cost(ac_id)["status"], "PAID")

    def test_simulator_negative_actions_via_ui(self):
        ac_id = self.s.approved_and_received()
        expect = {"pay_wrong": "AMOUNT_MISMATCH", "bad_signature": "INVALID_SIGNATURE"}
        self.s.sim.emit(ac_id, "PENDING")
        for action, code in expect.items():
            out = self.s.sim.action(action, ac_id)
            self.assertIn(code, out["delivery"]["response"], action)
        self.assertNotEqual(self.s.cost(ac_id)["status"], "PAID")
        self.s.sim.action("pay", ac_id)
        self.assertEqual(self.s.sim.action("resend", ac_id)["delivery"]["response"], "DUPLICATE")
        self.assertEqual(self.s.sim.action("replay_old", ac_id)["delivery"]["response"], "INVALID_TIMESTAMP")
        self.assertEqual(self.s.cost(ac_id)["status"], "PAID")

    def test_late_auto_pending_does_not_overwrite_paid_invoice(self):
        ac_id = self.s.approved_and_received()
        self.s.sim.action("pay", ac_id)
        self.assertEqual(self.s.sim.emit(ac_id, "PENDING"), {"event": None, "delivery": None})
        self.assertEqual(self.s.sim.handle_status(self.signed_get(ac_id), ac_id)[1]["payment_status"], "PAID")

    def signed_get(self, ac_id):
        ts = utc_timestamp(self.s.clock())
        return {"X-Timestamp": ts, "X-Signature": compute_signature(OUT_SECRET, ts, b"")}

    def test_status_endpoint_requires_signature(self):
        ac_id = self.s.approved_and_received()
        unsigned = self.s.http.get(f"/finance-sim/api/v1/additional-costs/{ac_id}/status")
        self.assertEqual(unsigned.status_code, 401)
        signed = self.s.http.get(f"/finance-sim/api/v1/additional-costs/{ac_id}/status", headers=self.signed_get(ac_id))
        self.assertEqual((signed.status_code, signed.get_json()["payment_status"]), (200, "PENDING"))
        missing = self.s.http.get("/finance-sim/api/v1/additional-costs/AC-X/status", headers=self.signed_get("AC-X"))
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
