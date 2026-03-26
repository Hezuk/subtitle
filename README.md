# 영상 자막 생성기

한국어 영상을 업로드하면 AI가 음성을 인식하고, 영어로 번역하여 자막을 입혀주는 웹 애플리케이션입니다.

> Whisper 음성인식 → Gemini 번역 · QA 검토 → 웹 에디터로 편집 → ffmpeg 자막 번인

## 주요 기능

- **자동 파이프라인** — 업로드만 하면 음성인식 → 번역 → QA 검토 → 자막 완성까지 자동 진행
- **AI 품질 검토** — Gemini가 번역 결과를 검토하고 불량 자막을 자동 재번역 (최대 2회)
- **자막 에디터** — 글자 수 경고, 검색, 개별 재번역(요구사항 입력 가능)
- **영상 미리보기** — 번역 완료 후 원본 영상에 자막을 얹어 바로 확인 (영어/한국어/동시)
- **SRT 내보내기·가져오기** — 영어·한국어·동시 SRT 다운로드, 외부 SRT 불러오기
- **작업 취소·재시도** — 진행 중 언제든 취소, 인코딩 실패 시 자막 수정 후 재시도

## 빠른 시작

### 사전 준비

| 항목 | Mac | Windows |
|------|-----|---------|
| Python | 3.10+ | [3.10+](https://www.python.org/downloads/) (설치 시 **Add Python to PATH** 체크) |
| ffmpeg | `brew install ffmpeg` | `winget install Gyan.FFmpeg` |
| API 키 | [Google AI Studio](https://aistudio.google.com/)에서 Gemini API 키 발급 ||

### Mac

```bash
git clone https://github.com/Hezuk/subtitle.git
cd subtitle
pip install fastapi uvicorn aiofiles openai-whisper requests python-dotenv
cp .env.example .env
# .env 파일을 열어 GEMINI_API_KEY 입력
uvicorn main:app --host 0.0.0.0 --port 8000
```

### Windows

```
1. 저장소 다운로드 → .env 파일 생성 후 GEMINI_API_KEY 입력
2. 설치.bat 더블클릭 (Python·ffmpeg·패키지 자동 설치)
3. 실행.bat 더블클릭 → 브라우저 자동 오픈
```

접속: `http://localhost:8000`

## 동작 방식

```
영상 업로드 → Whisper 음성인식 → Gemini 번역 → Gemini QA 검토
                                                    ↓
                        자막 편집 (웹 에디터) ← 번역 완료
                                                    ↓
                                    ffmpeg 자막 번인 → 완성 영상 다운로드
```

1. **음성 인식** — Whisper `large-v3-turbo`로 한국어 음성 → 텍스트
2. **번역** — Gemini API로 영어 번역
3. **품질 검토** — Gemini가 미번역·비문·용어 불일치 검출, 불량 자막 `[RETRANSLATE]` 마킹 후 재번역
4. **자막 편집** — 웹 에디터에서 수정, 개별 재번역, SRT 가져오기/내보내기
5. **자막 번인** — ffmpeg으로 영상에 영어 자막 하드코딩

## 프로젝트 구조

```
subtitle/
├── main.py               # FastAPI 라우트 + 파이프라인 오케스트레이션
├── config.py              # 전역 설정 (.env → 환경변수 → 기본값)
├── store/jobs.py          # job 상태 관리 (메모리 + JSON 디스크 영속화)
├── utils/
│   ├── srt.py             # SRT 파싱·변환·블록 검증
│   ├── errors.py          # 예외 클래스 (사용자 메시지 분리)
│   └── log.py             # 구조화 로거
├── services/
│   ├── transcription.py   # Whisper 음성인식
│   ├── translation.py     # Gemini 번역·검토·재번역
│   └── encoding.py        # ffmpeg 자막 번인
├── shared.js              # 프론트엔드 공통 유틸 (상수, 폴링, API 래퍼)
├── index.html             # 메인 페이지
├── player.html            # 팝업 플레이어
├── editor.html            # 팝업 에디터
├── 설치.bat / 실행.bat     # Windows 원클릭 설치·실행
└── .env.example           # 설정 템플릿
```

## 설정

`.env` 파일에서 관리. **`GEMINI_API_KEY`만 필수**이며 나머지는 기본값이 있어 생략 가능합니다.

| 환경변수 | 기본값 | 설명 |
|---------|--------|------|
| `GEMINI_API_KEY` | — | Gemini API 키 (필수) |
| `GEMINI_MODEL` | `gemini-3-flash-preview` | Gemini 모델명 |
| `WHISPER_MODEL` | `large-v3-turbo` | Whisper 모델명 |
| `FFMPEG_CRF` | `18` | 인코딩 품질 (낮을수록 고화질) |
| `FFMPEG_PRESET` | `fast` | 인코딩 속도 |
| `MAX_UPLOAD_MB` | `4096` | 최대 업로드 크기 (MB) |
| `MAX_SUBTITLE_CHARS` | `42` | 줄 자동 분할 기준 글자 수 |
| `MAX_RETRIES` | `2` | QA 재번역 최대 횟수 |

전체 설정 항목은 [`.env.example`](.env.example) 참조.

## 기술 스택

| 역할 | 기술 |
|------|------|
| 음성 인식 | [OpenAI Whisper](https://github.com/openai/whisper) |
| 번역 + QA | [Google Gemini API](https://ai.google.dev/) |
| 자막 번인 | [ffmpeg](https://ffmpeg.org/) (libass) |
| 웹 서버 | [FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/) |
| 프론트엔드 | Vanilla JS (프레임워크 없음) |
