"""Outbox + retry terkendali (Standar API Integrated bagian 05 dan 09).

Pengajuan disimpan dulu di outbox (dalam transaksi yang sama dengan record Additional Cost),
lalu dikirim oleh worker. Koneksi putus -> retry dengan event_id yang sama (exponential
backoff, batas percobaan). Percobaan habis / ditolak -> REVIEW + notifikasi admin; data tidak hilang.

CATATAN: dengan repository in-memory, outbox hanya bertahan selama proses hidup.
"""
import logging
import threading
from typing import List, Optional

from core import payment_logic as logic
from core.status import APPROVED, SENT
from services.finance_client import ACKED, REJECTED, RETRY, SKIPPED

log = logging.getLogger("wiraky.outbox")


class OutboxProcessor:
    def __init__(self, repo, client, settings, now):
        self.repo, self.client, self.settings, self.now = repo, client, settings, now
        self._lock = threading.Lock()   # satu pengirim aktif; Idempotency-Key tetap jadi pengaman kedua

    def process_due(self, limit: int = 50) -> List[dict]:
        """Kirim semua entri yang sudah jatuh tempo."""
        with self._lock:
            return [self._deliver(entry) for entry in self.repo.due_outbox(self.now())[:limit]]

    def send_now(self, event_id: str) -> Optional[dict]:
        """Kirim satu entri PENDING sekarang juga (mengabaikan jadwal backoff)."""
        with self._lock:
            entry = self.repo.get_outbox(event_id)
            if entry is None or entry["state"] != "PENDING":
                return None
            return self._deliver(entry)

    def _cancel(self, entry, reason):
        with self.repo.transaction():
            current = self.repo.get_outbox(entry["event_id"])
            if current:
                current["state"] = "CANCELLED"
                current["last_error"] = reason
                self.repo.put_outbox(current)
        return {"event_id": entry["event_id"], "outcome": "CANCELLED", "reason": reason}

    def _deliver(self, entry) -> dict:
        record = self.repo.get_cost(entry["additional_cost_id"], entry["revision"])
        if record is None or record["superseded"] or record["status"] not in (APPROVED, SENT):
            return self._cancel(entry, "tidak perlu dikirim (digantikan/sudah diproses)")

        result = self.client.submit(record["payload"])
        if result.outcome == ACKED:
            logic.record_send_success(self.repo, entry["event_id"], result.body, self.now, result.http_status)
        elif result.outcome == SKIPPED:
            return self._cancel(entry, result.error)
        else:
            logic.record_send_failure(
                self.repo, entry["event_id"], error=result.error or "UNKNOWN",
                retriable=(result.outcome == RETRY), now=self.now, settings=self.settings,
                http_status=result.http_status)
        return {"event_id": entry["event_id"], "outcome": result.outcome,
                "http_status": result.http_status, "error": result.error}


class BackgroundWorker:
    """Thread daemon yang menjalankan tugas berkala (outbox, rekonsiliasi)."""

    def __init__(self, tasks, interval: float = 1.0):
        self.tasks, self.interval = list(tasks), interval
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="wiraky-worker", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self):
        while not self._stop.wait(self.interval):
            for task in self.tasks:
                try:
                    task()
                except Exception:  # worker tidak boleh mati karena satu kegagalan
                    log.exception("tugas background gagal")
