@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Chua co Python 3. Cai tai https://www.python.org/downloads/ ^(tick "Add Python to PATH"^) roi chay lai.
  pause
  exit /b 1
)

if not exist .venv (
  echo Lan dau chay: dang cai moi truong, mat vai phut...
  python -m venv .venv || goto :fail
  .venv\Scripts\pip install -q -r requirements.txt || goto :fail
  .venv\Scripts\python -m playwright install chromium || goto :fail
)

rem tat server cu dang giu cong 8765
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8765 " ^| findstr LISTENING') do taskkill /f /pid %%p >nul 2>nul

start "" /b cmd /c "timeout /t 2 >nul & start http://127.0.0.1:8765"
echo Dang chay tai http://127.0.0.1:8765 - dong cua so nay de dung.
.venv\Scripts\python app.py
pause
exit /b 0

:fail
echo Cai dat that bai.
pause
exit /b 1
