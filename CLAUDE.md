# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the server

```bash
cd /Users/sunghoonkim/claude/subtitle
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
      1. Whisper transcription  → {job_id}.srt  (Korean)
      2. Gemini translation     → {job_id}_en.srt (English)
      → status: ready_to_encode  (user reviews/edits subtitles)

POST /encode/{job_id}
  → run_encode()
      3. ffmpeg burn-in         → outputs/{job_id}.mp4
      → status: done
      (deletes input video, both SRT files after done)
```

### Job state machine

`queued → transcribing → translating → ready_to_encode → encoding → done`

### API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | index.html |
| GET | `/player` | player.html |
| GET | `/editor` | editor.html |
| POST | `/upload` | 영상 업로드, job 생성 |
| GET | `/status/{job_id}` | job 상태 폴링 |
| POST | `/encode/{job_id}` | 번인 시작 (ready_to_encode 상태에서만) |
| GET | `/download/{job_id}` | 완성 영상 다운로드 |
| GET | `/subtitle/{job_id}` | 영어 자막 WebVTT |
| GET | `/subtitle_ko/{job_id}` | 한국어 자막 WebVTT |
| GET | `/subtitle_combined/{job_id}` | 영어+한국어 동시 WebVTT |
| GET | `/subtitles/{job_id}` | 영어 자막 블록 JSON (편집용) |
| POST | `/subtitles/{job_id}` | 영어 자막 저장 |
| GET | `/subtitles_ko/{job_id}` | 한국어 자막 블록 JSON (편집용) |
| POST | `/subtitles_ko/{job_id}` | 한국어 자막 저장 |

---

## Key technical details

### Whisper
- 모델: `large-v3-turbo` (메모리에 lazy load, 전역 변수 유지)
- `language="ko"`, `fp16=False`
- 첫 실행 시 모델 다운로드 (~1.5GB)

### Gemini 번역
- 모델: `gemini-3-flash-preview`
- REST API: `generativelanguage.googleapis.com/v1beta/models/...`
- 전체 자막 블록을 한 번에 전송 (`\n---\n` 구분자)
- 번역 후 `wrap_subtitle()` 로 42자 초과 줄 자동 2줄 분할
- 프롬프트: natural/sophisticated English, consistent tone, 42자 이내

### ffmpeg
- libass 필요: `brew tap homebrew-ffmpeg/ffmpeg && brew install homebrew-ffmpeg/ffmpeg/ffmpeg --with-libass`
- 번인 옵션: `libx264 -crf 18 -preset fast`
- 자막 스타일: Arial, 20pt, 흰색 텍스트, 검정 외곽선

### 파일 관리
- 임시 파일: `uploads/{job_id}.*` — 각 단계 완료 후 삭제
- 한국어 SRT: `ready_to_encode` 상태에서 유지, `done` 후 삭제
- 영어 SRT: `done` 후 삭제
- 출력 파일: `outputs/{job_id}.mp4` — 영구 보관

---

## Frontend 통신 구조

### index.html ↔ player.html (postMessage)
```js
// index → player
{ type: 'original', url: objectURL }   // 원본 영상
{ type: 'job', id: jobId }             // job 시작
{ type: 'subtitle_updated' }           // 자막 편집 후 갱신

// player → (없음, player가 독립적으로 /status 폴링)
```

### index.html ↔ editor.html (postMessage)
```js
// editor → index (저장 후)
{ type: 'subtitle_updated' }

// index → player (중계)
{ type: 'reload_subtitle' }            // 플레이어 자막 트랙 갱신
```

### player.html 자막 언어 선택
- `끄기 / 영어 / 한국어 / 동시` 버튼
- 배속: `0.5x / 1x / 1.5x / 2x / 3x`

### editor.html 기능
- 언어 탭: 영어 / 한국어 / 동시 편집
- 자막 검색, 번호로 이동
- 글자 수 경고 (42자 초과 노란색, 50자 초과 빨간색)
- **저장** (Ctrl+S): 서버에 POST, 플레이어 자막 자동 갱신
- **내보내기**: 영어 SRT / 한국어 SRT / 한국어+영어 동시 SRT 다운로드
- **가져오기**: 외부 .srt 파일을 영어 또는 한국어 자막으로 import

---

## Windows 배포 (설치.bat / 실행.bat)
- Python 3.10+ 필요 (`Add to PATH` 체크 필수)
- ffmpeg: `winget install Gyan.FFmpeg`
- 가상환경: `venv/` 폴더에 자동 생성
- `.env` 파일을 별도로 전달 (GitHub에 올라가지 않음)
