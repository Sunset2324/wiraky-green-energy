"""Penyimpanan in-memory dengan transaksi atomik (rollback otomatis bila terjadi exception).

PENTING: data hilang saat aplikasi restart, dan outbox TIDAK durable. Cocok untuk demo/UAT
lokal saja. Untuk produksi, ganti kelas ini dengan implementasi database (mis. MySQL)
yang memiliki antarmuka sama, supaya persyaratan "status + audit dalam SATU transaksi
database" dan "outbox durable" (standar bagian 05/08) terpenuhi.
"""
import copy
import threading
from contextlib import contextmanager

_AUDIT_LIMIT = 5000
_ALERT_LIMIT = 1000


class InMemoryRepository:
    def __init__(self):
        self._lock = threading.RLock()
        self._depth = 0
        self._state = {"costs": {}, "seq": {}, "events": {}, "outbox": {}, "notifications": {}, "audit": [], "alerts": []}

    # --- transaksi -------------------------------------------------------
    @contextmanager
    def transaction(self):
        """Semua perubahan di dalam blok berlaku atomik; exception = rollback semuanya."""
        with self._lock:
            snapshot = copy.deepcopy(self._state) if self._depth == 0 else None
            self._depth += 1
            try:
                yield self
            except BaseException:
                self._depth -= 1
                if self._depth == 0:
                    self._state = snapshot
                raise
            else:
                self._depth -= 1

    # --- sequence --------------------------------------------------------
    def next_sequence(self, name: str) -> int:
        with self._lock:
            value = self._state["seq"].get(name, 0) + 1
            self._state["seq"][name] = value
            return value

    # --- additional cost -------------------------------------------------
    @staticmethod
    def _key(additional_cost_id: str, revision: int) -> str:
        return f"{additional_cost_id}#{int(revision)}"

    def save_cost(self, record: dict) -> None:
        with self._lock:
            self._state["costs"][self._key(record["additional_cost_id"], record["revision"])] = copy.deepcopy(record)

    def latest_revision(self, additional_cost_id: str):
        with self._lock:
            revs = [r["revision"] for r in self._state["costs"].values()
                    if r["additional_cost_id"] == additional_cost_id]
            return max(revs) if revs else None

    def get_cost(self, additional_cost_id: str, revision=None):
        with self._lock:
            if revision is None:
                revision = self.latest_revision(additional_cost_id)
                if revision is None:
                    return None
            record = self._state["costs"].get(self._key(additional_cost_id, revision))
            return copy.deepcopy(record) if record else None

    def list_costs(self, order_id=None):
        with self._lock:
            rows = [r for r in self._state["costs"].values() if order_id is None or r["order_id"] == order_id]
            return copy.deepcopy(sorted(rows, key=lambda r: (r["created_at"], r["revision"])))

    # --- idempotency event masuk ----------------------------------------
    def has_event(self, event_id: str) -> bool:
        with self._lock:
            return event_id in self._state["events"]

    def mark_event(self, event_id: str, info: dict) -> None:
        with self._lock:
            self._state["events"][event_id] = copy.deepcopy(info)

    # --- outbox ----------------------------------------------------------
    def put_outbox(self, entry: dict) -> None:
        with self._lock:
            self._state["outbox"][entry["event_id"]] = copy.deepcopy(entry)

    def get_outbox(self, event_id: str):
        with self._lock:
            entry = self._state["outbox"].get(event_id)
            return copy.deepcopy(entry) if entry else None

    def list_outbox(self):
        with self._lock:
            return copy.deepcopy(sorted(self._state["outbox"].values(), key=lambda e: e["created_at"]))

    def due_outbox(self, now):
        with self._lock:
            rows = [e for e in self._state["outbox"].values()
                    if e["state"] == "PENDING" and e["next_attempt_at"] <= now]
            return copy.deepcopy(sorted(rows, key=lambda e: e["next_attempt_at"]))

    # --- audit & alert ---------------------------------------------------
    # --- notifikasi WhatsApp ---------------------------------------------
    def put_notification(self, entry: dict) -> None:
        with self._lock:
            self._state["notifications"][entry["key"]] = copy.deepcopy(entry)

    def get_notification(self, key: str):
        with self._lock:
            entry = self._state["notifications"].get(key)
            return copy.deepcopy(entry) if entry else None

    def find_notification_by_message_id(self, message_id: str):
        with self._lock:
            for entry in self._state["notifications"].values():
                if entry.get("message_id") == message_id:
                    return copy.deepcopy(entry)
        return None

    def list_notifications(self):
        with self._lock:
            return copy.deepcopy(sorted(self._state["notifications"].values(), key=lambda e: e["created_at"]))

    def append_audit(self, entry: dict) -> None:
        with self._lock:
            self._state["audit"].append(copy.deepcopy(entry))
            if len(self._state["audit"]) > _AUDIT_LIMIT:
                del self._state["audit"][: len(self._state["audit"]) - _AUDIT_LIMIT]

    def list_audit(self, additional_cost_id=None, limit=200):
        with self._lock:
            rows = [e for e in self._state["audit"]
                    if additional_cost_id is None or e.get("additional_cost_id") == additional_cost_id]
            return copy.deepcopy(rows[-limit:])

    def add_alert(self, alert: dict) -> None:
        with self._lock:
            self._state["alerts"].append(copy.deepcopy(alert))
            if len(self._state["alerts"]) > _ALERT_LIMIT:
                del self._state["alerts"][: len(self._state["alerts"]) - _ALERT_LIMIT]

    def list_alerts(self, limit=50):
        with self._lock:
            return copy.deepcopy(self._state["alerts"][-limit:])
