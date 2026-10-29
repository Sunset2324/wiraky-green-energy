#!/usr/bin/env bash
# Jalankan demo Wiraky Green Energy (Mac/Linux).
# Pemakaian:  ./start_demo.sh         (port default 5500, demo Xendit = demo_final.py)
#             APP_ENTRY=app.py ./start_demo.sh   (integrasi Standar API + simulator Finance)
#             PORT=5000 ./start_demo.sh
set -e
cd "$(dirname "$0")"

if [ "$APP_ENTRY" = "demo_final.py" ] && [ ! -f .env ]; then
  echo "[!] File .env belum ada. Jalankan:  cp .env.example .env  lalu isi nilainya."
  exit 1
fi

APP_ENTRY="${APP_ENTRY:-demo_final.py}"
PY=python3
command -v python3 >/dev/null 2>&1 || PY=python

if ! "$PY" -c "import flask, xendit, dotenv, requests" >/dev/null 2>&1; then
  echo "[!] Dependency belum terpasang. Jalankan:  $PY -m pip install -r requirements.txt"
  exit 1
fi

# Nilai dari shell menang atas .env (python-dotenv tidak menimpa env yang sudah ada)
export PORT="${PORT:-5500}"
export FLASK_DEBUG=0   # demo publik: debugger harus mati

cat <<EOF

=== Wiraky Green Energy - mode demo ===
App akan jalan di  http://127.0.0.1:${PORT}   (debug: OFF)

Langkah berikutnya di VS Code:
  1. Panel PORTS > Forward a Port > ${PORT}
  2. Klik kanan port tersebut > Port Visibility > Public
  3. Salin link *.devtunnels.ms dan kirim ke client
  4. demo_final.py: webhook Xendit (Test mode) = <link>/webhook/xendit
     app.py: buka <link>/surveyor, <link>/dashboard, <link>/finance-sim/
Selesai demo: klik kanan port > Stop Forwarding Port. Tekan Ctrl+C di sini untuk menghentikan app.

EOF

exec "$PY" "$APP_ENTRY"
