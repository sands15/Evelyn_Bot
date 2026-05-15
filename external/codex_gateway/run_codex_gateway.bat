@echo off
setlocal
cd /d %~dp0
set "PYEXE=%~dp0..\..\.venv-voyager\Scripts\python.exe"
if exist "%PYEXE%" (
  "%PYEXE%" -m uvicorn codex_gateway:app --host 127.0.0.1 --port 8787
) else (
  python -m uvicorn codex_gateway:app --host 127.0.0.1 --port 8787
)
