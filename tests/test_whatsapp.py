"""Tes notifikasi WhatsApp Business (mode LOG dan CLOUD_API dengan transport palsu)."""
import hashlib
import hmac
import json
import unittest
from datetime import timedelta

from app import create_app
from config.settings import ConfigError, load_whatsapp_settings
from services.whatsapp_service import mask, normalize_recipient, render_text, WhatsAppError
from test_standard_integration import APPROVAL, BASE_ENV, CUSTOMER, ITEMS_700K, System

WA_ENV = {"WA_ENABLED": "1", "WA_PHONE_NUMBER_ID": "1234567890", "WA_ACCESS_TOKEN": "EAAB-token-rahasia",
          "WA_APP_SECRET": "app-secret-meta", "WA_VERIFY_TOKEN": "verify-token-x", "WA_BACKOFF_BASE": "5",
          "WA_MAX_ATTEMPTS": "3"}


class WaSystem(System):
    """System dari tes standar + transport WhatsApp palsu (dibuat lewat build())."""

    def _to_wa(self, method, url, headers, data, timeout):
        self.wa_calls.append({"url": url, "headers": headers, "body": json.loads(data)})
        resp = self.wa_response
        if isinstance(resp, Exception):
            raise resp
        if resp is None:
            return 200, json.dumps({"messages": [{"id": f"wamid.REAL{len(self.wa_calls)}"}]}).encode()
        return resp[0], json.dumps(resp[1]).encode()

    @property
    def wa(self):
        return self.svc.whatsapp


def build(**env):
    # System.__init__ membuat app sendiri; di sini kita ganti dengan app ber-transport WA.
    s = WaSystem.__new__(WaSystem)
    s.wa_calls, s.wa_response = [], None
    from test_standard_integration import FakeClock
    s.clock, s.finance_down, s.finance_override, s.drop_response_once, s.finance_calls = FakeClock(), False, None, False, []
    s.app = create_app({**BASE_ENV, **env}, finance_transport=s._to_finance, callback_transport=s._to_green,
                       now=s.clock, whatsapp_transport=s._to_wa)
    s.svc = s.app.extensions["wiraky"]
    s.sim = s.svc.simulator
    s.http = s.app.test_client()
    return s


class ConfigTests(unittest.TestCase):
    def test_default_is_log_mode_without_credentials(self):
        self.assertEqual(load_whatsapp_settings({}).mode, "LOG")

    def test_enabled_requires_credentials_and_numeric_phone_id(self):
        with self.assertRaises(ConfigError):
            load_whatsapp_settings({"WA_ENABLED": "1"})
        with self.assertRaises(ConfigError):
            load_whatsapp_settings({**WA_ENV, "WA_PHONE_NUMBER_ID": "+6281234"})
        self.assertEqual(load_whatsapp_settings(WA_ENV).mode, "CLOUD_API")

    def test_unknown_kind_rejected(self):
        with self.assertRaises(ConfigError):
            load_whatsapp_settings({"WA_NOTIFY_KINDS": "SPAM"})

    def test_helpers(self):
        self.assertEqual(normalize_recipient("+6281234567890"), "6281234567890")
        with self.assertRaises(WhatsAppError):
            normalize_recipient("abc")
        self.assertEqual(mask("+6281234567890"), "***7890")
        self.assertEqual(render_text("PAYMENT_CONFIRMED", ["A", "AC-1", "Rp 1", "W", "t"]).count("{{"), 0)


class LogModeTests(unittest.TestCase):
    def test_log_mode_never_calls_network_and_records_preview(self):
        s = build()
        s.approved_and_received()
        s.wa.process_due()
        items = s.svc.repo.list_notifications()
        self.assertEqual([i["kind"] for i in items], ["BILL_ISSUED"])
        self.assertEqual(items[0]["state"], "SENT")
        self.assertTrue(items[0]["simulated"])
        self.assertIn("Rp 700.000", items[0]["preview"])
        self.assertEqual(s.wa_calls, [])
        page = s.http.get("/whatsapp").get_data(as_text=True)
        self.assertIn("Mode LOG", page)
        self.assertNotIn("081234567890", page)                  # nomor dimask di UI


class CloudModeTests(unittest.TestCase):
    def setUp(self):
        self.s = build(**WA_ENV)

    def test_bill_then_paid_each_sent_once_with_template_payload(self):
        s = self.s
        ac_id = s.approved_and_received()
        s.wa.process_due()
        s.wa.process_due()                                      # idempotent: tidak kirim ganda
        self.assertEqual(len(s.wa_calls), 1)
        call = s.wa_calls[0]
        self.assertIn("/v21.0/1234567890/messages", call["url"])
        self.assertEqual(call["headers"]["Authorization"], "Bearer EAAB-token-rahasia")
        body = call["body"]
        self.assertEqual((body["to"], body["type"]), ("6281234567890", "template"))
        self.assertEqual(body["template"]["name"], "additional_cost_bill")
        self.assertEqual(body["template"]["language"]["code"], "id")
        params = [p["text"] for p in body["template"]["components"][0]["parameters"]]
        self.assertEqual(params, ["Pelanggan Contoh", "WIR-2026-0001", "Rp 700.000", ac_id])
        s.sim.action("pay", ac_id)
        s.wa.process_due()
        self.assertEqual(len(s.wa_calls), 2)
        self.assertEqual(s.wa_calls[1]["body"]["template"]["name"], "additional_cost_paid")
        paid_param = s.wa_calls[1]["body"]["template"]["components"][0]["parameters"][4]["text"]
        self.assertRegex(paid_param, r"^\d{2}-\d{2}-\d{4} \d{2}:\d{2} WIB$")
        self.assertEqual(s.cost(ac_id)["status"], "PAID")

    def test_paid_before_bill_sent_skips_bill(self):
        s = self.s
        ac_id = s.approved_and_received()
        s.sim.action("pay", ac_id)
        s.wa.process_due()
        kinds = {(n["kind"], n["state"]) for n in s.svc.repo.list_notifications()}
        self.assertEqual(kinds, {("PAYMENT_CONFIRMED", "SENT")})
        self.assertEqual(len(s.wa_calls), 1)

    def test_no_notification_for_unbilled_or_superseded(self):
        s = self.s
        s.create(order="WIR-NOL", items=[], approval=None)
        s.wa.process_due()
        self.assertEqual(s.svc.repo.list_notifications(), [])
        ac_id = s.approved_and_received("WIR-REV")
        s.wa.process_due()
        rev = s.create(order="WIR-REV", items=[{"part_code": "JASA-001", "qty": 2}], additional_cost_id=ac_id)   # revisi 2
        self.assertEqual(rev.status_code, 201)
        s.send()
        s.wa.process_due()
        revs = sorted((n["revision"], n["kind"]) for n in s.svc.repo.list_notifications())
        self.assertEqual(revs, [(1, "BILL_ISSUED"), (2, "BILL_ISSUED")])

    def test_retry_with_backoff_then_success_without_duplicate_state_change(self):
        s = self.s
        ac_id = s.approved_and_received()
        s.wa_response = (503, {"error": {"code": 2, "message": "Service unavailable"}})
        s.wa.process_due()
        n = s.svc.repo.list_notifications()[0]
        self.assertEqual((n["state"], n["attempts"]), ("RETRY", 1))
        s.wa.process_due()
        self.assertEqual(len(s.wa_calls), 1)                    # belum jatuh tempo
        s.clock.advance(5)
        s.wa_response = None
        s.wa.process_due()
        self.assertEqual(s.svc.repo.list_notifications()[0]["state"], "SENT")
        self.assertEqual(s.cost(ac_id)["status"], "RECEIVED")

    def test_transport_error_retried_and_exhaustion_alerts_but_keeps_payment_state(self):
        s = self.s
        ac_id = s.approved_and_received()
        s.wa_response = TimeoutError("lambat")
        for _ in range(3):
            s.wa.process_due()
            s.clock.advance(60)
        n = s.svc.repo.list_notifications()[0]
        self.assertEqual((n["state"], n["attempts"]), ("FAILED", 3))
        self.assertIn("WA_SEND_FAILED", s.alert_codes())
        s.sim.action("pay", ac_id)                              # pembayaran tetap bisa PAID
        self.assertEqual(s.cost(ac_id)["status"], "PAID")

    def test_permanent_error_not_retried(self):
        s = self.s
        s.approved_and_received()
        s.wa_response = (400, {"error": {"code": 132001, "message": "Template name does not exist"}})
        s.wa.process_due()
        n = s.svc.repo.list_notifications()[0]
        self.assertEqual((n["state"], n["attempts"]), ("FAILED", 1))
        self.assertIn("132001", n["last_error"])
        s.clock.advance(1000)
        s.wa.process_due()
        self.assertEqual(len(s.wa_calls), 1)

    def test_200_without_message_id_is_not_trusted(self):
        s = self.s
        s.approved_and_received()
        s.wa_response = (200, {"unexpected": True})
        s.wa.process_due()
        self.assertEqual(s.svc.repo.list_notifications()[0]["state"], "RETRY")

    def test_invalid_phone_recorded_as_failed_with_alert_and_no_call(self):
        s = self.s
        s.create(order="WIR-BAD")
        # simulasi data lama dengan nomor rusak
        rec = s.svc.repo.list_costs()[0]
        rec["customer"]["whatsapp"] = "abc"
        s.svc.repo.save_cost(rec)
        s.send()
        s.wa.process_due()
        self.assertEqual(s.wa_calls, [])
        self.assertIn("WA_INVALID_RECIPIENT", s.alert_codes())

    def test_secrets_not_leaked_to_audit_or_ui(self):
        s = self.s
        s.approved_and_received()
        s.wa.process_due()
        blob = json.dumps([a for a in s.svc.repo.list_audit()], default=str)
        page = s.http.get("/whatsapp").get_data(as_text=True) + s.http.get("/dashboard").get_data(as_text=True)
        for text in (blob, page):
            self.assertNotIn("EAAB-token-rahasia", text)
            self.assertNotIn("6281234567890", text)


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.s = build(**WA_ENV)
        self.s.approved_and_received()
        self.s.wa.process_due()
        self.msg_id = self.s.svc.repo.list_notifications()[0]["message_id"]

    def post(self, payload, secret="app-secret-meta", sig=None, raw=None):
        body = raw if raw is not None else json.dumps(payload).encode()
        sig = sig if sig is not None else "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return self.s.http.post("/api/v1/whatsapp/webhook", data=body,
                                headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})

    def status(self, status, errors=None):
        st = {"id": self.msg_id, "status": status}
        if errors:
            st["errors"] = errors
        return {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {"statuses": [st]}}]}]}

    def delivery(self):
        return self.s.svc.repo.list_notifications()[0]["delivery_status"]

    def test_verification_handshake(self):
        c = self.s.http
        ok = c.get("/api/v1/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=verify-token-x&hub.challenge=12345")
        self.assertEqual((ok.status_code, ok.get_data(as_text=True)), (200, "12345"))
        self.assertEqual(c.get("/api/v1/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=salah&hub.challenge=1").status_code, 403)

    def test_status_progression_and_no_downgrade(self):
        self.assertEqual(self.post(self.status("delivered")).get_json()["updated"], 1)
        self.assertEqual(self.delivery(), "delivered")
        self.assertEqual(self.post(self.status("sent")).get_json()["updated"], 0)     # datang terlambat
        self.post(self.status("read"))
        self.assertEqual(self.delivery(), "read")

    def test_failed_delivery_alerts_but_does_not_touch_payment(self):
        self.post(self.status("failed", [{"code": 131026, "title": "Message undeliverable"}]))
        self.assertEqual(self.delivery(), "failed")
        self.assertIn("WA_DELIVERY_FAILED", self.s.alert_codes())
        self.assertEqual(self.s.svc.repo.list_costs()[0]["status"], "RECEIVED")

    def test_signature_required_and_secret_checked(self):
        self.assertEqual(self.post(self.status("read"), secret="salah").status_code, 401)
        self.assertEqual(self.post(self.status("read"), sig="").status_code, 401)
        self.assertEqual(self.post(self.status("read"), sig="sha256=" + "é" * 64).status_code, 401)
        self.assertEqual(self.s.http.post("/api/v1/whatsapp/webhook", json=self.status("read")).status_code, 401)
        self.assertEqual(self.delivery(), "accepted")          # tidak ada yang berubah

    def test_invalid_json_and_unknown_message(self):
        self.assertEqual(self.post(None, raw=b"{bukan json").status_code, 400)
        payload = self.status("read")
        payload["entry"][0]["changes"][0]["value"]["statuses"][0]["id"] = "wamid.TIDAK-ADA"
        self.assertEqual(self.post(payload).get_json()["updated"], 0)
        self.assertEqual(self.post({"entry": "aneh"}).status_code, 200)

    def test_fail_closed_without_app_secret_and_404_in_log_mode(self):
        env = {k: v for k, v in WA_ENV.items() if k != "WA_APP_SECRET"}
        s = build(**env)
        self.assertEqual(s.http.post("/api/v1/whatsapp/webhook", data=b"{}").status_code, 503)
        log_mode = build()
        self.assertEqual(log_mode.http.post("/api/v1/whatsapp/webhook", data=b"{}").status_code, 404)
        self.assertEqual(log_mode.http.get("/api/v1/whatsapp/webhook?hub.mode=subscribe").status_code, 404)

    def test_whatsapp_message_cannot_mark_paid(self):
        self.post({"entry": [{"changes": [{"value": {"messages": [{"from": "6281234567890", "text": {"body": "SUDAH BAYAR"}}]}}]}]})
        self.assertEqual(self.s.svc.repo.list_costs()[0]["status"], "RECEIVED")


if __name__ == "__main__":
    unittest.main()
