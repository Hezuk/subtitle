@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 영어 자막 프로그램

:: 설치 확인
if not exist "venv" (
    echo ❌ 설치가 필요합니다. '설치.bat' 을 먼저 실행해주세요.
    pause
    exit /b 1
)

echo ========================================
echo   영어 자막 프로그램을 시작합니다...
echo ========================================
echo.

:: 기존 서버 종료
taskkill /f /fi "WINDOWTITLE eq 자막 서버*" >nul 2>&1

:: 가상환경 활성화
call venv\Scripts\activate.bat

:: 서버를 별도 창에서 실행
start "자막 서버" /min venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000

:: 서버 준비 대기
echo 서버 시작 중...
timeout /t 4 /nobreak >nul

:: 브라우저 열기
start http://localhost:8000

echo.
echo ========================================
echo   프로그램이 실행 중입니다!
echo   브라우저에서 사용하세요.
echo.
echo   ⚠  프로그램을 종료하려면:
echo      작업 표시줄의 '자막 서버' 창을 닫으세요
echo ========================================
echo.
pause
