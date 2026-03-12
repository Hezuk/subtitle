@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 영어 자막 프로그램 - 설치

echo ========================================
echo   영어 자막 프로그램 설치를 시작합니다
echo ========================================
echo.

:: Python 확인
echo [1/3] Python 확인 중...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo ❌ Python이 설치되지 않았습니다.
    echo.
    echo 지금 Python 다운로드 페이지를 열겠습니다.
    echo 설치할 때 반드시 "Add Python to PATH" 를 체크하세요!
    echo.
    start https://www.python.org/downloads/
    echo Python 설치 후 이 파일을 다시 실행해주세요.
    pause
    exit /b 1
)
echo Python 확인 완료 ✓

:: ffmpeg 확인 및 설치
echo.
echo [2/3] ffmpeg 설치 중...
ffmpeg -version >nul 2>&1
if %errorlevel% neq 0 (
    echo winget으로 ffmpeg 설치 중...
    winget install Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
    if %errorlevel% neq 0 (
        echo.
        echo ❌ ffmpeg 자동 설치 실패.
        echo 아래 사이트에서 직접 다운로드 후 설치해주세요:
        start https://www.gyan.dev/ffmpeg/builds/
        pause
        exit /b 1
    )
    echo ffmpeg 설치 완료 ✓
) else (
    echo ffmpeg 확인 완료 ✓
)

:: Python 패키지 설치
echo.
echo [3/3] 필요한 패키지 설치 중... (10~20분 소요될 수 있습니다)
echo      Whisper AI 모델도 함께 다운로드됩니다.
echo.
python -m venv venv
call venv\Scripts\activate.bat
python -m pip install --upgrade pip -q
pip install fastapi uvicorn aiofiles openai-whisper requests python-dotenv

echo.
echo ========================================
echo   설치가 완료되었습니다!
echo   이제 '실행.bat' 을 더블클릭하세요
echo ========================================
pause
