@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ITS 레이더를 엽니다...
echo 브라우저가 자동으로 열립니다. 이 창을 닫으면 종료됩니다.
echo.
start "" http://localhost:8765/index.html
python -m http.server 8765
