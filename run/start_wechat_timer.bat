@echo off
cd /d "%~dp0\.."
where py >nul 2>nul
if errorlevel 1 (
  python src\ntp_key_timer_wechat_revised.py
) else (
  py src\ntp_key_timer_wechat_revised.py
)
if errorlevel 1 pause

