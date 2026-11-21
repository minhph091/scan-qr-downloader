@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [LOI] Chua tim thay moi truong ao .venv!
    echo Hay chay lenh cai dat: py -3.12 -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

echo Dang khoi dong ScanQRDownloader...
".venv\Scripts\python.exe" run.py
pause
