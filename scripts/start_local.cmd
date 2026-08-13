@echo off
setlocal

set ROOT=%~dp0..
for %%I in ("%ROOT%") do set ROOT=%%~fI
set BACKEND=%ROOT%\backend
set FRONTEND=%ROOT%\frontend

if not defined DEEPSEEK_API_KEY (
  for /f "usebackq tokens=2,*" %%A in (`reg query HKCU\Environment /v DEEPSEEK_API_KEY 2^>nul`) do set DEEPSEEK_API_KEY=%%B
)

if not defined LIMITUPLAB_LLM_ENABLED set LIMITUPLAB_LLM_ENABLED=true
if not defined LIMITUPLAB_LLM_BASE_URL set LIMITUPLAB_LLM_BASE_URL=https://api.deepseek.com
if not defined LIMITUPLAB_LLM_MODEL set LIMITUPLAB_LLM_MODEL=deepseek-v4-flash
if defined LIMITUPLAB_PROXY_URL (
  set HTTP_PROXY=%LIMITUPLAB_PROXY_URL%
  set HTTPS_PROXY=%LIMITUPLAB_PROXY_URL%
  set ALL_PROXY=%LIMITUPLAB_PROXY_URL%
) else (
  if "%HTTP_PROXY%"=="http://127.0.0.1:9" set HTTP_PROXY=
  if "%HTTPS_PROXY%"=="http://127.0.0.1:9" set HTTPS_PROXY=
  if "%ALL_PROXY%"=="http://127.0.0.1:9" set ALL_PROXY=
)

echo [1/3] Checking data freshness and local Agent health...
pushd "%BACKEND%"
".venv\Scripts\python.exe" scripts\dev_check.py --ensure-data
if errorlevel 1 (
  echo Local health check failed. Backend/frontend startup aborted.
  popd
  exit /b 1
)
popd

echo [2/3] Starting backend on http://127.0.0.1:8001 ...
start "LimitUpLab Backend" cmd /k "cd /d ""%BACKEND%"" && scripts\start_backend.cmd -Port 8001"

echo [3/3] Starting frontend on http://127.0.0.1:5173 ...
start "LimitUpLab Frontend" cmd /k "cd /d ""%FRONTEND%"" && npm.cmd run dev -- --host 127.0.0.1 --port 5173"

echo.
echo LimitUpLab local startup requested.
echo Frontend: http://127.0.0.1:5173
echo Backend:  http://127.0.0.1:8001
