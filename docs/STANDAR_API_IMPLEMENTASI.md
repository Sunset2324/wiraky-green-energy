# Implementasi Standar API Integrated v1.0 (Green Energy ↔ Finance Wiraky)

Entry point: `python app.py` (atau `APP_ENTRY=app.py ./start_demo.sh`). `demo_final.py` (demo Xendit) tidak diubah.
Tes UAT otomatis: `python -m unittest discover -s tests -v` (63 skenario, positif + negatif).

## Pemetaan standar → kode
| Bagian standar | Implementasi |
|---|---|
| Pemicu & aturan (Additional Cost > 0, Engineering + persetujuan pelanggan, tanpa biaya = tidak ada API call) | `core/payment_logic.create_additional_cost`, `core/master_price.py` (harga dihitung server, input harga dari klien diabaikan) |
| Payload & event_id (`EVT-AC-2026-0001-R01`) | `core/payload.py` |
| Kirim ke Finance: HMAC `timestamp.body`, `X-Timestamp`, `X-Event-ID`, `Idempotency-Key` | `services/finance_client.py`, `core/security.py` |
| 202 RECEIVED bukan lunas | `record_send_success` → status `RECEIVED` |
| Outbox, retry exponential backoff, event_id sama, REVIEW + notifikasi admin | `services/outbox.py`, `record_send_failure` |
| Callback: HMAC dua arah, timestamp ≤ 300 dtk, rate limit, secret terpisah, rotasi | `routes/webhook_routes.py`, `config/settings.py` |
| Validasi PAID (nominal, invoice, payment_reference, currency, verified_by, revisi) | `process_payment_event` |
| Idempotensi penerimaan, PAID tidak diturunkan, EXPIRED/FAILED/PENDING bukan lunas | `process_payment_event`, `core/status.py` |
| Rekonsiliasi GET status bila callback hilang | `services/reconciliation.py` |
| Revisi biaya, refund setelah PAID | `create_additional_cost`, `process_payment_event` |
| Audit log + notifikasi admin | `core/audit_logger.py`, `/dashboard` |
| Simulator Finance untuk UAT | `services/finance_simulator.py`, `/finance-sim/` |

## Asumsi (konfirmasi dengan tim Finance sebelum produksi)
1. Endpoint `GET .../{additional_cost_id}/status` ditandatangani sama (X-Timestamp + X-Signature, body kosong) memakai secret arah Green Energy → Finance.
2. Respons GET berbentuk sama dengan payload callback; bila tanpa `event_id`, dibuat id sintetis stabil `GET-<id>-Rnn-<status>`.
3. URL callback Green Energy, URL Finance, dan secret nyata diberikan Finance lewat environment, tidak ada yang di-hardcode.

## Batasan (jujur)
- **Penyimpanan IN-MEMORY**: data & outbox hilang saat restart. Cukup untuk demo/UAT, **belum untuk produksi**. Produksi butuh database durable (antarmuka `core/repository.py` sengaja dipisah).
- Simulator **bukan** sistem Finance asli; hanya aktif bila `WIRAKY_FINANCE_API_URL` kosong.
- UI/API internal belum memiliki login/otorisasi pengguna.
- Persetujuan Engineering/pelanggan di form hanya dicentang oleh surveyor; verifikasi sebenarnya ada di proses bisnis Wiraky.
