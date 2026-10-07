@echo off
cd /d %~dp0
echo Starting KIZA-AGRI ...
start "KIZA Python ML service" cmd /c python ml_service\api.py
start "KIZA App" cmd /c python app.py
timeout /t 3 >nul
start http://127.0.0.1:5000
