@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0..\.."

REM ===== 기본값: 더블클릭만 해도 evelyn 생성 =====
set "PROFILE_ID=evelyn"
set "SERVER=http://127.0.0.1:8880"

REM ===== 인자를 주면 기본값 덮어쓰기 =====
if not "%~1"=="" (
    set "PROFILE_ID=%~1"
)

if not "%~2"=="" (
    set "OMNIVOICE_REF_AUDIO=%~2"
)

if not "%~1"=="" (
    shift
    shift
    if not "%~1"=="" (
        set "OMNIVOICE_REF_TEXT=%*"
    )
)

if not exist "%OMNIVOICE_REF_AUDIO%" (
    echo 파일을 찾을 수 없습니다: %OMNIVOICE_REF_AUDIO%
    popd
    pause
    exit /b 1
)

echo [INFO] profile_id=%PROFILE_ID%
echo [INFO] OMNIVOICE_REF_AUDIO=%OMNIVOICE_REF_AUDIO%
echo [INFO] server=%SERVER%
echo [INFO] OMNIVOICE_REF_TEXT=%OMNIVOICE_REF_TEXT%
echo.

py -3 "%~dp0create_omnivoice_profile.py" ^
  "%PROFILE_ID%" ^
  "%OMNIVOICE_REF_AUDIO%" ^
  --ref-text "%OMNIVOICE_REF_TEXT%" ^
  --server "%SERVER%" ^
  --overwrite

echo.
echo 완료되었습니다.
popd
pause
endlocal
