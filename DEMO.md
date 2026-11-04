# Panduan Demo — Wiraky Green Energy

Panduan singkat menjalankan demo `demo_final.py` dan membukanya ke client lewat **VS Code Port Forwarding** (Dev Tunnels).

## 1. Persiapan (sekali saja)

1. Install dependency: `python -m pip install -r requirements.txt` (harus `xendit-python==5.0.0`).
2. Buat file `.env` dari template: `copy .env.example .env` (Windows) atau `cp .env.example .env` (Mac/Linux).
3. Isi `.env` dengan key **Test mode** dari Xendit Dashboard:
   - `XENDIT_SECRET_KEY`: Settings → API Keys → Generate secret key. Permission **Invoices = Write**.
   - `XENDIT_CALLBACK_TOKEN`: Settings → Webhooks → Verification token.

## 2. Jalankan app

| OS | Perintah |
|---|---|
| Windows (PowerShell, default terminal VS Code) | `.\start_demo.bat` |
| Windows (cmd) | `start_demo.bat` |
| Mac/Linux | `./start_demo.sh` (jika belum executable: `chmod +x start_demo.sh`) |

Default port **5500**. Untuk mengganti port:

- PowerShell: `$env:PORT=5000; .\start_demo.bat`
- cmd: `set PORT=5000` lalu `start_demo.bat`
- Mac/Linux: `PORT=5000 ./start_demo.sh`

Script selalu mematikan debug mode. Di PowerShell, file di folder saat ini harus diawali `.\` (tanpa itu muncul error "term is not recognized").

## 3. Buka ke client (VS Code Ports)

1. Panel **Ports** (di sebelah tab Terminal) → **Forward a Port** → isi port yang sama dengan app (default `5500`).
2. Login GitHub/Microsoft jika diminta.
3. Klik kanan port → **Port Visibility → Public** (kalau Private, client harus login).
4. Salin link `https://xxxx.devtunnels.ms` dan kirim ke client. Saat pertama dibuka, client mungkin melihat halaman peringatan Dev Tunnels: klik **Continue**.

## 4. Webhook (agar status pembayaran masuk)

Di Xendit Dashboard (**Test mode**) → Settings → Webhooks → **Invoices paid**, isi:

```
https://xxxx.devtunnels.ms/webhook/xendit
```

Link berubah jika tunnel dibuat ulang, jadi perbarui URL ini setiap kali. Setelah invoice sandbox dibayar, terminal Flask akan menampilkan `Webhook Masuk: Status PAID`.

## 5. Setelah demo

- Klik kanan port → **Stop Forwarding Port**, lalu hentikan app dengan `Ctrl+C`.
- Halaman demo tidak punya login. Selama forwarding aktif, siapa pun yang punya link bisa membuat invoice sandbox.
- Hanya pakai key **sandbox** untuk demo. Jangan pernah memakai key live.

## Troubleshooting

| Gejala | Penyebab | Solusi |
|---|---|---|
| `403 REQUEST_FORBIDDEN_ERROR` saat buat invoice | Permission key belum cukup | Buat secret key baru dengan **Invoices = Write** (Test mode) |
| `401` dari Xendit | Key salah/kosong | Cek `XENDIT_SECRET_KEY` di `.env`, lalu restart app |
| Halaman tidak jalan / 404 di link tunnel | Port yang di-forward bukan port app (mis. 5500 dipakai Live Server) | Samakan port forward dengan `PORT` di `.env`; matikan Live Server jika memakai 5500 |
| Webhook dibalas `503` | `XENDIT_CALLBACK_TOKEN` kosong | Isi token di `.env`, restart app |
| Webhook dibalas `403` | Token tidak cocok dengan Dashboard | Salin ulang Verification token dari Dashboard |
| Perubahan `.env` tidak berefek | `.env` hanya dibaca saat start | Restart app |

## Keamanan repo

`.env` sebelumnya sudah ter-commit. Keluarkan dari git (file lokal tetap aman): `git rm --cached .env` lalu commit. Key yang pernah ter-commit sebaiknya di-revoke dan diganti di Dashboard.


## Mode Standar API Integrated (app.py)

Menjalankan alur Approved → RECEIVED → PAID dengan Finance disimulasikan:

    APP_ENTRY=app.py ./start_demo.sh            # Windows PowerShell: $env:APP_ENTRY="app.py"; .\start_demo.bat

Buka `<link>/surveyor` (buat pengajuan), `<link>/dashboard?auto=1` (pantau), `<link>/finance-sim/` (tombol bayar, nominal salah, duplikat, signature palsu, dll.).
Penyimpanan in-memory: data hilang saat restart. Detail: `docs/STANDAR_API_IMPLEMENTASI.md`.
