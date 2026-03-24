# 한국어 영상 영어 자막 자동 생성기

한국어 영상을 업로드하면 AI가 자동으로 음성을 인식하고, 영어로 번역한 뒤 자막을 삽입해주는 프로그램입니다.

## 동작 방식

1. **음성 인식** — OpenAI Whisper (`large-v3-turbo`)로 한국어 음성을 텍스트로 변환
2. **번역** — Google Gemini API로 영어로 번역
3. **품질 검토** — Gemini QA 에이전트가 번역 결과를 검토하고 불량 자막 재번역 (최대 2회)
4. **자막 편집** — 웹 에디터에서 자막 수정, 개별 재번역 가능
5. **자막 번인** — ffmpeg으로 영상에 영어 자막 하드인코딩

## 주요 기능

- **자막 미리보기**: 번역 완료 후 팝업 플레이어에서 원본 영상 + 자막 확인 (영어/한국어/동시)
- **자막 에디터**: 글자 수 경고(노란색 42자↑, 빨간색 50자↑), 검색, 개별 재번역
- **재번역**: 마음에 안 드는 자막에 요구사항 입력 후 개별 재번역
- **단계별 소요 시간**: 각 처리 단계의 경과/완료 시간 실시간 표시
- **SRT 내보내기/가져오기**: 영어·한국어·동시 SRT 다운로드 및 외부 SRT 가져오기
- **작업 상태 영속화**: 서버 재시작 후에도 완료된 작업 다운로드, 대기 중인 작업 재시도 가능
- **인코딩 실패 복구**: 인코딩 실패 시 파일 유지, 자막 수정 후 재시도 가능

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

## 설치 방법 (Windows)

### 사전 준비
- [Python 3.10+](https://www.python.org/downloads/) 설치 시 **"Add Python to PATH"** 반드시 체크
- ffmpeg: `winget install Gyan.FFmpeg`
- Gemini API 키

### 설치 및 실행

1. 저장소 다운로드 후 `.env` 파일 생성 (`GEMINI_API_KEY` 입력)
2. `설치.bat` 더블클릭 (10~20분 소요)
3. `실행.bat` 더블클릭 → 브라우저 자동으로 열림

## 프로젝트 구조

```
subtitle/
├── main.py                  ← FastAPI 라우트 + 파이프라인 오케스트레이션
├── config.py                ← 경로 상수
├── store/jobs.py            ← job 상태 관리 (메모리 + JSON 디스크 영속화)
├── utils/
│   ├── srt.py               ← SRT 파싱/변환, 블록 검증
│   ├── errors.py            ← 예외 클래스 (사용자 메시지 분리)
│   └── log.py               ← 구조화 로거
├── services/
│   ├── transcription.py     ← Whisper 음성인식
│   ├── translation.py       ← Gemini 번역/검토/재번역
│   └── encoding.py          ← ffmpeg 자막 번인
├── index.html               ← 메인 페이지
├── player.html              ← 팝업 플레이어
└── editor.html              ← 팝업 에디터
```

## 사용 기술

| 역할 | 기술 |
|------|------|
| 음성 인식 | [OpenAI Whisper](https://github.com/openai/whisper) `large-v3-turbo` |
| 번역 + QA | [Google Gemini API](https://ai.google.dev/) `gemini-3-flash-preview` |
| 자막 인코딩 | [ffmpeg](https://ffmpeg.org/) |
| 웹 서버 | [FastAPI](https://fastapi.tiangolo.com/) |
