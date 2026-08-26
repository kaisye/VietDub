@echo off
REM ============================================================================
REM  VietDub - dev launcher (chay thu NGHI-TTS + UI 2 nhom)
REM
REM  - Chay backend FastAPI tu SOURCE bang Python anaconda (da cai piper-tts,
REM    vietnormalizer, fastapi, sqlalchemy, onnxruntime...) tai 127.0.0.1:8386.
REM  - Bat AETHER_DESKTOP_SKIP_BACKEND=1 de Tauri KHONG spawn sidecar cu (stale,
REM    chua co NGHI-TTS) ma dung backend source o tren.
REM  - Mo app desktop (Tauri + Vite). Vite proxy /api... -> 127.0.0.1:8386.
REM
REM  Doi PYTHON ben duoi neu moi truong co piper-tts nam o cho khac.
REM ============================================================================
setlocal
set "REPO=d:\AgenticAI\VideoDubbing"
set "PYTHON=C:\Users\phong\anaconda3\python.exe"

if not exist "%PYTHON%" (
  echo [LOI] Khong tim thay Python: %PYTHON%
  echo       Sua bien PYTHON trong launch.bat cho dung moi truong co piper-tts.
  pause
  exit /b 1
)

echo === 1/2: Khoi dong backend FastAPI tai http://127.0.0.1:8386 (cua so rieng) ...
REM  Khong duoc de dau cach truoc "&&": cmd gan ca dau cach vao gia tri
REM  (PYTHONUTF8="1 "), Python coi la khong hop le va thoat ngay voi loi
REM  "Fatal Python error: preconfig_init_utf8_mode". Vi dung /k nen cua so
REM  van mo, trong nhu backend dang chay du khong co python.exe nao ca.
start "VietDub API (8386)" cmd /k "cd /d %REPO% && set PYTHONUTF8=1&& set KMP_DUPLICATE_LIB_OK=TRUE&& %PYTHON% -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8386 --app-dir apps\api"

echo === Cho backend khoi dong (6s) ...
timeout /t 6 /nobreak >nul

echo === 2/2: Mo app desktop (Tauri dev). Lan dau build Rust co the mat vai phut ...
cd /d %REPO%
set "AETHER_DESKTOP_SKIP_BACKEND=1"
call npm run desktop:dev

echo.
echo === App desktop da dong. Cua so "VietDub API (8386)" van chay - dong tay neu can. ===
endlocal
