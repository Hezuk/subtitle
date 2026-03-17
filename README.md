# 한국어 영상 영어 자막 + 더빙 자동 생성기

한국어 영상을 업로드하면 AI가 자동으로 음성을 인식하고, 영어로 번역한 뒤 자막을 삽입하거나 영어 더빙 오디오까지 생성해주는 프로그램입니다.

## 동작 방식

1. **음성 인식** — OpenAI Whisper (`large-v3-turbo`)로 한국어 음성을 텍스트로 변환
2. **번역** — Google Gemini API로 영어로 번역
3. **품질 검토** — Gemini QA 에이전트가 번역 결과를 검토하고 불량 자막 재번역 (최대 2회)
4. **자막 편집** — 웹 에디터에서 자막 수정, 개별 재번역 가능
5. **자막 번인** — ffmpeg으로 영상에 영어 자막 하드인코딩
6. **영어 더빙** *(선택)* — TADA TTS로 영어 더빙 오디오 생성 후 자막과 함께 인코딩

## 주요 기능

- **자막 미리보기**: 번역 완료 후 팝업 플레이어에서 자막 확인 (영어/한국어/동시)
- **자막 에디터**: 글자 수 경고(노란색 42자↑, 빨간색 50자↑), 검색, 개별 재번역
- **재번역**: 마음에 안 드는 자막에 요구사항 입력 후 개별 재번역
- **영어 더빙**: TADA TTS 모델로 자막 타임스탬프에 맞춰 영어 더빙 생성
- **단계별 소요 시간**: 각 처리 단계의 경과/완료 시간 실시간 표시
- **SRT 내보내기/가져오기**: 영어·한국어·동시 SRT 다운로드 및 외부 SRT 가져오기

## 설치 방법 (Mac)

### 사전 준비
- Python 3.10+
- ffmpeg with libass: `brew install ffmpeg`
- Gemini API 키 ([Google AI Studio](https://aistudio.google.com/)에서 무료 발급)

### 설치

```bash
git clone https://github.com/Hezuk/subtitle.git
cd subtitle
pip install -r requirements.txt
cp .env.example .env
# .env 파일에 GEMINI_API_KEY 입력
```

### 실행

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

브라우저에서 `http://localhost:8000` 접속

### TADA 더빙 기능 설치 (선택)

더빙 기능을 사용하려면 별도 가상환경이 필요합니다.

```bash
python -m venv tada_env
tada_env/bin/pip install torch torchaudio
tada_env/bin/pip install -e ./tada
```

HuggingFace 로그인 및 [Llama 3.2 라이선스](https://huggingface.co/meta-llama/Llama-3.2-1B) 승인 필요:
```bash
tada_env/bin/huggingface-cli login --token hf_...
```

## 설치 방법 (Windows)

### 사전 준비
- [Python 3.10+](https://www.python.org/downloads/) 설치 시 **"Add Python to PATH"** 반드시 체크
- ffmpeg: `winget install Gyan.FFmpeg`
- Gemini API 키

### 설치 및 실행

1. 저장소 다운로드 후 `.env` 파일 생성 (`GEMINI_API_KEY` 입력)
2. `설치.bat` 더블클릭 (10~20분 소요)
3. `실행.bat` 더블클릭 → 브라우저 자동으로 열림

> **참고**: Windows에서는 TADA 더빙 기능을 지원하지 않습니다.

## 사용 기술

| 역할 | 기술 |
|------|------|
| 음성 인식 | [OpenAI Whisper](https://github.com/openai/whisper) `large-v3-turbo` |
| 번역 + QA | [Google Gemini API](https://ai.google.dev/) `gemini-3-flash-preview` |
| 영어 더빙 TTS | [TADA](https://github.com/HumeAI/tada) `HumeAI/tada-1b` (Llama 3.2 기반) |
| 자막 인코딩 | [ffmpeg](https://ffmpeg.org/) |
| 웹 서버 | [FastAPI](https://fastapi.tiangolo.com/) |
