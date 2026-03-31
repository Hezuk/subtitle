# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Running the server

```bash
cd /Users/sunghoonkim/Codex/subtitle
uvicorn main:app --host 0.0.0.0 --port 8000
```

API key is loaded from `.env` (see `.env.example`):
```
GEMINI_API_KEY=...
```

To stop:
```bash
pkill -f "uvicorn main:app"
```

GitHub: https://github.com/Hezuk/subtitle (private)

---

## Architecture

**Backend:** `main.py` — single-file FastAPI
**Frontend:** `index.html` (main), `player.html` (popup player), `editor.html` (popup editor)
**Windows installer:** `설치.bat`, `실행.bat`

### Pipeline (two-phase)

```
POST /upload
  → run_pipeline() [ThreadPoolExecutor, max_workers=1]
      1. Whisper transcription       → {job_id}.srt  (Korean)
      2. Gemini translation          → {job_id}_en.srt (English)
      3. Gemini QA review + retry    → {job_id}_en.srt (corrected)
         - MAX_RETRIES=2: 불량 블록은 [RETRANSLATE] 마킹 후 재번역
      → status: ready_to_encode  (user reviews/edits subtitles)
      → player.html 자동 오픈 (/player?job_id=xxx)

POST /encode/{job_id}
  → run_encode()
      4. ffmpeg burn-in + -movflags +faststart  → outputs/{job_id}.mp4
      → status: done
```

### Job state machine

`queued → transcribing → translating → ready_to_encode → encoding → done`

### /status response fields

| Field | Description |
|-------|-------------|
| `status` | 현재 상태 |
| `message` | 단계별 상세 메시지 (소요 시간 포함) |
| `progress` | 0~100 진행률 |
| `elapsed` | 현재 단계 경과 시간 (진행 중일 때만, e.g. "1분 23초") |
| `timings` | 각 단계 소요 시간 dict (완료 후) |

### API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | index.html |
| GET | `/player` | player.html |
| GET | `/editor` | editor.html |
| POST | `/upload` | 영상 업로드, job 생성 |
| GET | `/status/{job_id}` | job 상태 폴링 |
| GET | `/original/{job_id}` | 원본 영상 서빙 (player 미리보기용) |
| POST | `/encode/{job_id}` | 번인 시작 (ready_to_encode 상태에서만) |
| GET | `/download/{job_id}` | 완성 영상 다운로드 |
| GET | `/subtitle/{job_id}` | 영어 자막 WebVTT |
| GET | `/subtitle_ko/{job_id}` | 한국어 자막 WebVTT |
| GET | `/subtitle_combined/{job_id}` | 영어+한국어 동시 WebVTT |
| GET | `/subtitles/{job_id}` | 영어 자막 블록 JSON (편집용) |
| POST | `/subtitles/{job_id}` | 영어 자막 저장 |
| GET | `/subtitles_ko/{job_id}` | 한국어 자막 블록 JSON (편집용) |
| POST | `/subtitles_ko/{job_id}` | 한국어 자막 저장 |
| POST | `/retranslate/{job_id}` | 개별 자막 블록 재번역 (에디터용) |

---

## Key technical details

### Whisper
- 모델: `large-v3-turbo` (메모리에 lazy load, 전역 변수 유지)
- `language="ko"`, `fp16=False`
- 첫 실행 시 모델 다운로드 (~1.5GB)

### Gemini 번역 + QA
- 모델: `gemini-3-flash-preview`
- REST API: `generativelanguage.googleapis.com/v1beta/models/...`
- 전체 자막 블록을 한 번에 전송 (`\n---\n` 구분자)
- 번역 후 `wrap_subtitle()` 로 42자 초과 줄 자동 2줄 분할
- **번역 프롬프트**: natural/sophisticated English, complete sentence/clause
- **검토 프롬프트**: 미번역 잔존·비문·용어 일관성 확인, 수정 불가 시 `[RETRANSLATE]` 마킹
- **재시도 로직**: `[RETRANSLATE]` 블록은 원문 재번역 후 재검토 (최대 2회)
- **개별 재번역**: `/retranslate/{job_id}` — 에디터에서 요구사항 지정 가능

### ffmpeg
- libass 필요: `brew tap homebrew-ffmpeg/ffmpeg && brew install homebrew-ffmpeg/ffmpeg/ffmpeg --with-libass`
- 번인 옵션: `libx264 -crf 18 -preset fast -movflags +faststart`
- 자막 스타일: Arial, 20pt, 흰색 텍스트, 검정 외곽선
- `-movflags +faststart` 필수: 없으면 moov atom 손상으로 파일 재생 불가

### 파일 관리
- 임시 파일: `uploads/{job_id}.*` — 각 단계 완료 후 삭제
- 한국어 SRT: `ready_to_encode` 상태에서 유지, `done` 후 삭제
- 영어 SRT: `done` 후 삭제
- 출력 파일: `outputs/{job_id}.mp4` — 영구 보관

### SRT 파싱 (parse_srt)
- `re.split(r'\n{2,}', content)` 블록 분리 방식 사용
- 구 regex 방식은 자막 텍스트에 숫자만 있는 줄이 있으면 오파싱됨 → 현재 방식으로 수정

---

## Frontend 통신 구조

### player.html 오픈 방식
- URL 파라미터로 job_id 전달: `/player?job_id=xxx`
- player.html은 `URLSearchParams`로 job_id를 읽어 즉시 `/status` 조회
- `ready_to_encode` 상태에서 `/original/{job_id}`로 원본 영상 로드
- `done` 상태에서 `/download/{job_id}`로 완성 영상 로드
- `playerOpened` 플래그로 한 번만 열림 (폴링 시 중복 오픈 방지)

### index.html ↔ player.html (postMessage)
```js
// index → player (자막 편집 후 중계)
{ type: 'reload_subtitle' }
```

### index.html ↔ editor.html (postMessage)
```js
// editor → index (저장 후)
{ type: 'subtitle_updated' }

// index → player (중계)
{ type: 'reload_subtitle' }
```

### player.html 동작
- URL에서 `job_id` 읽으면 즉시 status 조회 → `handleStatus()` 호출
- `ready_to_encode` 도달 시 `/original/{job_id}` 로드 + 자막 언어 선택 행 표시
- `done` 도달 시 `/download/{job_id}` 로드
- 배속: `0.5x / 1x / 1.5x / 2x / 3x`
- status 메시지에 경과 시간 표시 (폴링 2초마다 갱신)

### player.html 자동 오픈 시점
- `ready_to_encode`: 번역 완료 시 첫 오픈 (원본 영상 + 자막 미리보기)
- `done`: 인코딩 완료 시 재오픈 (완성 영상 로드)

### editor.html 기능
- 언어 탭: 영어 / 한국어 / 동시 편집
- 자막 검색, 번호로 이동
- 글자 수 경고 (42자 초과 노란색, 50자 초과 빨간색) — 가장 긴 줄 기준
- **재번역**: 각 영어 자막 블록에서 요구사항 입력 후 개별 재번역
- **저장** (Ctrl+S): 서버에 POST, 플레이어 자막 자동 갱신
- **내보내기**: 영어 SRT / 한국어 SRT / 한국어+영어 동시 SRT 다운로드
- **가져오기**: 외부 .srt 파일을 영어 또는 한국어 자막으로 import
- textarea 높이: DOM 삽입 후 일괄 autoResize (삽입 전 호출 시 scrollHeight=0 문제)

---

## Windows 배포 (설치.bat / 실행.bat)
- Python 3.10+ 필요 (`Add to PATH` 체크 필수)
- ffmpeg: `winget install Gyan.FFmpeg`
- 가상환경: `venv/` 폴더에 자동 생성
- `.env` 파일을 별도로 전달 (GitHub에 올라가지 않음)
