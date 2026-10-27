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
set HTTP_PROXY=
set HTTPS_PROXY=
set ALL_PROXY=
set http_proxy=
set https_proxy=
set all_proxy=
if defined LIMITUPLAB_PROXY_URL (
  set HTTP_PROXY=%LIMITUPLAB_PROXY_URL%
  set HTTPS_PROXY=%LIMITUPLAB_PROXY_URL%
  set ALL_PROXY=%LIMITUPLAB_PROXY_URL%
  set http_proxy=%LIMITUPLAB_PROXY_URL%
  set https_proxy=%LIMITUPLAB_PROXY_URL%
  set all_proxy=%LIMITUPLAB_PROXY_URL%
)

echo [1/5] Checking configured PostgreSQL readiness...
pushd "%BACKEND%"
".venv\Scripts\python.exe" scripts\local_postgresql.py ensure
if errorlevel 1 (
  echo Database startup failed. Backend/frontend startup aborted.
  popd
  exit /b 1
)
popd

echo [2/5] Checking data freshness and local Agent health...
pushd "%BACKEND%"
".venv\Scripts\python.exe" scripts\dev_check.py --ensure-data
if errorlevel 1 (
  echo Local health check failed. Backend/frontend startup aborted.
  popd
  exit /b 1
)
popd

echo [3/5] Starting backend on http://127.0.0.1:8001 ...
start "LimitUpLab Backend" cmd /k "cd /d ""%BACKEND%"" && scripts\start_backend.cmd -Port 8001"

echo [4/5] Starting frontend on http://127.0.0.1:5173 ...
start "LimitUpLab Frontend" cmd /k "cd /d ""%FRONTEND%"" && npm.cmd run dev -- --host 127.0.0.1 --port 5173"

echo [5/5] Starting memory extraction worker...
start "LimitUpLab Memory Worker" cmd /k "cd /d ""%BACKEND%"" && .venv\Scripts\python.exe scripts\run_memory_extraction.py"

echo.
echo LimitUpLab local startup requested.
echo Frontend: http://127.0.0.1:5173
echo Backend:  http://127.0.0.1:8001
