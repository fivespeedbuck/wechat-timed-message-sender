@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  python -m pip install -r requirements.txt
  pushd build_specs
  python -m PyInstaller --noconfirm --clean --distpath ..\dist --workpath ..\build ntp_key_timer_wechat.spec
) else (
  py -m pip install -r requirements.txt
  pushd build_specs
  py -m PyInstaller --noconfirm --clean --distpath ..\dist --workpath ..\build ntp_key_timer_wechat.spec
)
set BUILD_ERROR=%ERRORLEVEL%
popd
if not "%BUILD_ERROR%"=="0" pause
exit /b %BUILD_ERROR%

