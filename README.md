# 한국어 영상 영어 자막 자동 생성기

한국어 영상을 업로드하면 AI가 자동으로 음성을 인식하고 영어 자막을 영상에 삽입해주는 프로그램입니다.

## 동작 방식

1. **음성 인식** — OpenAI Whisper로 한국어 음성을 텍스트로 변환
2. **번역** — Google Gemini API로 영어로 번역
3. **자막 삽입** — ffmpeg으로 영상에 영어 자막 하드인코딩

## 설치 방법 (Windows)

### 사전 준비
- [Python 3.10+](https://www.python.org/downloads/) 설치 필요
  - 설치 시 **"Add Python to PATH"** 반드시 체크
- Gemini API 키 필요 ([Google AI Studio](https://aistudio.google.com/)에서 무료 발급)

### 설치

1. 이 저장소를 다운로드
   ```
   git clone https://github.com/Hezuk/subtitle.git
   ```
2. `.env.example` 파일을 복사해서 `.env` 파일 생성
   ```
   GEMINI_API_KEY=여기에_API_키_입력
   ```
3. `설치.bat` 더블클릭 (10~20분 소요)

### 실행

`실행.bat` 더블클릭 → 브라우저 자동으로 열림

## 사용 기술

- [OpenAI Whisper](https://github.com/openai/whisper) `large-v3-turbo` — 음성 인식
- [Google Gemini API](https://ai.google.dev/) `gemini-3-flash-preview` — 번역
- [ffmpeg](https://ffmpeg.org/) — 자막 인코딩
- [FastAPI](https://fastapi.tiangolo.com/) — 웹 서버
