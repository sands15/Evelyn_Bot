@echo off
chcp 65001 >nul
setlocal

if "%~1"=="" (
  echo 사용법: create_omnivoice_profile.bat ^<profile_id^> ^<ref_audio.wav^> [ref_text]
  exit /b 1
)

set "PROFILE_ID=%~1"
set "REF_AUDIO=%~2"
set "REF_TEXT=%~3"

if "%REF_AUDIO%"=="" (
  echo ref_audio 경로를 넣어줘.
  exit /b 1
)

py -3 C:\Evelyn\create_omnivoice_profile.py "%PROFILE_ID%" "%REF_AUDIO%" --ref-text "%REF_TEXT%"

endlocal
