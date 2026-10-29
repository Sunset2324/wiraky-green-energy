@echo off
rem Jalankan demo Wiraky Green Energy (Windows).
rem Pemakaian (PowerShell): .\start_demo.bat                    (port default 5500)
rem                         $env:PORT=5000; .\start_demo.bat
rem Pemakaian (cmd):        start_demo.bat
rem                         (ganti port: jalankan "set PORT=5000" dulu, lalu start_demo.bat)
rem Pilih aplikasi: set APP_ENTRY=app.py (Standar API + simulator Finance). Default: demo_final.py
if "%APP_ENTRY%"=="" set APP_ENTRY=demo_final.py
cd /d "%~dp0"

if "%APP_ENTRY%"=="demo_final.py" if not exist .env echo [!] File .env belum ada. Jalankan: copy .env.example .env lalu isi nilainya. && pause && exit /b 1

python -c "import flask, xendit, dotenv, requests" >nul 2>nul
if errorlevel 1 echo [!] Dependency belum terpasang. Jalankan: python -m pip install -r requirements.txt && pause && exit /b 1

rem Nilai dari shell menang atas .env (python-dotenv tidak menimpa env yang sudah ada)
if "%PORT%"=="" set PORT=5500
rem Demo publik: debugger harus mati
set FLASK_DEBUG=0

echo.
echo === Wiraky Green Energy - mode demo ===
echo App akan jalan di  http://127.0.0.1:%PORT%   (debug: OFF)
echo.
echo Langkah berikutnya di VS Code:
echo   1. Panel PORTS ^> Forward a Port ^> %PORT%
echo   2. Klik kanan port tersebut ^> Port Visibility ^> Public
echo   3. Salin link *.devtunnels.ms dan kirim ke client
echo   4. Webhook Xendit (Test mode) = ^<link^>/webhook/xendit
echo Selesai demo: klik kanan port ^> Stop Forwarding Port. Tekan Ctrl+C di sini untuk menghentikan app.
echo.

python %APP_ENTRY%
pause
