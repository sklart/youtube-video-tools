@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
pushd "%~dp0"
python "%~dp0video_tools.py" %*
set "exit_code=%errorlevel%"
popd
endlocal & exit /b %exit_code%
