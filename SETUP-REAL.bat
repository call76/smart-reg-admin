@echo off
cd /d %~dp0
echo This creates a CLEAN database with NO demo data. Any existing kiza.db will be DELETED.
set /p ok=Type YES to continue: 
if /i not "%ok%"=="YES" exit /b
python db.py --real --force
echo.
echo Copy the passwords above, then run START-KIZA-AGRI.bat and log in as admin.
pause
