# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the server

```bash
cd /Users/sunghoonkim/claude/subtitle
uvicorn main:app --host 0.0.0.0 --port 8000
```

All settings are loaded from `.env` via `config.py` (see `.env.example`):
```
GEMINI_API_KEY=...          # 필수
GEMINI_MODEL=...            # 선택 (기본값 있음)
```

To stop:
```bash
pkill -f "uvicorn main:app"
```

GitHub: https://github.com/Hezuk/subtitle (private)

---

## Project structure

```
subtitle/
├── main.py                  ← FastAPI 라우트 + 파이프라인 오케스트레이션
├── config.py                ← 전역 설정 (경로, API, 모델, 제한값 — .env에서 로드)
├── store/
│   └── jobs.py              ← job 상태 관리 (메모리 dict + JSON 디스크 영속화)
├── utils/
│   ├── srt.py               ← SRT 파싱/변환, 블록 검증, wrap_subtitle, fmt_elapsed
│   ├── errors.py            ← 예외 클래스 (SubtitleError 계열)
│   └── log.py               ← 구조화 로거 설정
├── services/
│   ├── transcription.py     ← Whisper 음성인식 (모델 lazy load)
│   ├── translation.py       ← Gemini 번역/검토/재번역 (API 호출 캡슐화)
│   └── encoding.py          ← ffmpeg 자막 번인
├── shared.js                ← 프론트엔드 공통 유틸 (상수, 폴링, API 래퍼, 브로드캐스트)
├── index.html               ← 메인 페이지 (업로드, 상태 폴링, 취소, 다운로드)
├── player.html              ← 팝업 플레이어 (영상 미리보기 + 자막 오버레이)
├── editor.html              ← 팝업 에디터 (자막 편집, 재번역, SRT 가져오기/내보내기)
├── 설치.bat / 실행.bat       ← Windows 설치/실행 스크립트
├── uploads/                 ← 임시 파일 (원본 영상, SRT)
├── outputs/                 ← 완성 영상 (영구 보관)
└── jobs/                    ← job 상태 JSON 파일 (서버 재시작 시 복원용)
```

---

## Architecture

### Pipeline (two-phase)

```
POST /upload
  → run_pipeline() [pipeline_executor, max_workers=1]
      1. Whisper transcription       → {job_id}.srt  (Korean)
      2. Gemini translation          → {job_id}_en.srt (English)
      3. Gemini QA review + retry    → {job_id}_en.srt (corrected)
         - MAX_RETRIES=2: 불량 블록은 [RETRANSLATE] 마킹 후 재번역
      → status: ready_to_encode  (user reviews/edits subtitles)
      → player.html 자동 오픈 (/player?job_id=xxx)

POST /encode/{job_id}
  → run_encode() [encode_executor, max_workers=1]
      4. ffmpeg burn-in + -movflags +faststart  → outputs/{job_id}.mp4
      → status: done
```

`pipeline_executor`와 `encode_executor`는 분리되어 있어 번역 중에도 다른 작업의 인코딩이 가능.

### Job state machine

`queued → transcribing → translating → reviewing → ready_to_encode → encoding → done`

- 인코딩 실패 시 `ready_to_encode`로 복원 (파일 유지, 재시도 가능)
- 파이프라인 실패 시 `error` (임시 파일 삭제)
- 사용자 취소 시 `cancelled` (임시 파일 삭제)
- `POST /cancel/{job_id}`: 진행 중 작업 취소 (ffmpeg은 0.5초 내 프로세스 종료)

### Job persistence (store/jobs.py)

- 메모리 dict + `jobs/{job_id}.json` 디스크 저장
- 상태 변경 시 `save_job()` 호출로 디스크 동기화
- `load_job()`: 메모리 → 디스크 순서로 조회
- 서버 시작 시 `restore_jobs()`로 복원:
  - `done`: 출력 파일 존재 시 복원
  - `ready_to_encode`: 입력 파일 + SRT 존재 시 복원
  - 진행 중이던 작업: `error` 상태로 전환

### /status response fields

| Field | Description |
|-------|-------------|
| `status` | 현재 상태 |
| `message` | 단계별 상세 메시지 (소요 시간 포함) |
| `progress` | 0~100 범위 기반 진행률 (단계별 구간: 음성인식 0~30, 번역 30~55, 검토 55~78, 인코딩 82~98) |
| `phase` | 현재 대단계 (transcribe, translate, review, encode, done) |
| `subphase` | 세부 단계 (model_loading, transcribing, translating, reviewing, retranslating, encoding, ...) |
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
| POST | `/cancel/{job_id}` | 진행 중 작업 취소 |
| GET | `/download/{job_id}` | 완성 영상 다운로드 |
| GET | `/subtitle/{job_id}` | 영어 자막 WebVTT |
| GET | `/subtitle_ko/{job_id}` | 한국어 자막 WebVTT |
| GET | `/subtitle_combined/{job_id}` | 영어+한국어 동시 WebVTT |
| GET | `/subtitles/{job_id}` | 영어 자막 블록 JSON (편집용) |
| POST | `/subtitles/{job_id}` | 영어 자막 저장 (블록 검증 포함) |
| GET | `/subtitles_ko/{job_id}` | 한국어 자막 블록 JSON (편집용) |
| POST | `/subtitles_ko/{job_id}` | 한국어 자막 저장 (블록 검증 포함) |
| POST | `/retranslate/{job_id}` | 개별 자막 블록 재번역 (에디터용) |
| GET | `/shared.js` | 프론트엔드 공통 JS 모듈 |

---

## Configuration (config.py)

모든 설정값은 `config.py`에서 `os.environ.get()`으로 로드 (`.env` → 환경변수 → 기본값).

| 설정 | 환경변수 | 기본값 | 설명 |
|------|---------|--------|------|
| API | `GEMINI_API_KEY` | (필수) | Gemini API 키 |
| API | `GEMINI_MODEL` | `gemini-3-flash-preview` | Gemini 모델명 |
| API | `GEMINI_TIMEOUT` | `300` | Gemini API 타임아웃 (초) |
| API | `GEMINI_RETRANSLATE_TIMEOUT` | `60` | 개별 재번역 타임아웃 (초) |
| Whisper | `WHISPER_MODEL` | `large-v3-turbo` | Whisper 모델명 |
| Whisper | `WHISPER_LANGUAGE` | `ko` | 음성인식 언어 |
| Whisper | `WHISPER_FP16` | `false` | FP16 사용 여부 |
| ffmpeg | `FFMPEG_SUBTITLE_STYLE` | `FontName=Arial,...` | ASS 자막 스타일 |
| ffmpeg | `FFMPEG_CRF` | `18` | 인코딩 품질 |
| ffmpeg | `FFMPEG_PRESET` | `fast` | 인코딩 속도 |
| ffmpeg | `FFMPEG_TIMEOUT` | `7200` | 인코딩 타임아웃 (초) |
| 업로드 | `MAX_UPLOAD_MB` | `4096` | 최대 업로드 크기 (MB) |
| 자막 | `MAX_BLOCKS` | `5000` | 최대 자막 블록 수 |
| 자막 | `MAX_TEXT_LEN` | `1000` | 블록당 최대 글자 수 |
| 자막 | `MAX_SUBTITLE_CHARS` | `42` | 줄 자동 분할 기준 글자 수 |
| 자막 | `MAX_REQUIREMENT_LEN` | `500` | 재번역 요구사항 최대 길이 |
| 파이프라인 | `MAX_RETRIES` | `2` | QA 재번역 최대 횟수 |
| 정리 | `STALE_DAYS` | `7` | error job 보관 일수 |
| 정리 | `ORPHAN_HOURS` | `24` | orphan 파일 보관 시간 |

---

## Key technical details

### Whisper (services/transcription.py)
- 모델: `WHISPER_MODEL` (기본 `large-v3-turbo`, 메모리에 lazy load)
- `language=WHISPER_LANGUAGE`, `fp16=WHISPER_FP16`
- 첫 실행 시 모델 다운로드 (~1.5GB)
- 실패 시 `TranscriptionError` 발생

### Gemini 번역 + QA (services/translation.py)
- 모델: `GEMINI_MODEL` (기본 `gemini-3-flash-preview`)
- REST API: `generativelanguage.googleapis.com/v1beta/models/...`
- 전체 자막 블록을 한 번에 전송 (`\n---\n` 구분자)
- 번역 후 `wrap_subtitle()` 로 `MAX_SUBTITLE_CHARS` 초과 줄 자동 2줄 분할
- **번역 프롬프트**: natural/sophisticated English, complete sentence/clause
- **검토 프롬프트**: 미번역 잔존·비문·용어 일관성 확인, 수정 불가 시 `[RETRANSLATE]` 마킹
- **재시도 로직**: `[RETRANSLATE]` 블록은 원문 재번역 후 재검토 (최대 `MAX_RETRIES`회)
- **개별 재번역**: `/retranslate/{job_id}` — 에디터에서 요구사항 지정 가능
- API 키 미설정 시 `ConfigError`, 호출 실패 시 `TranslationError`/`ReviewError`

### ffmpeg (services/encoding.py)
- libass 필요: `brew tap homebrew-ffmpeg/ffmpeg && brew install homebrew-ffmpeg/ffmpeg/ffmpeg --with-libass`
- 번인 옵션: `libx264 -crf {FFMPEG_CRF} -preset {FFMPEG_PRESET} -movflags +faststart`
- 자막 스타일: `FFMPEG_SUBTITLE_STYLE` (기본: Arial, 20pt, 흰색 텍스트, 검정 외곽선)
- `-movflags +faststart` 필수: 없으면 moov atom 손상으로 파일 재생 불가
- `subprocess.Popen` + 0.5초 폴링으로 실행 (취소/타임아웃 시 프로세스 즉시 종료)
- `cancel_check` 콜백 지원: True 반환 시 `CancelledError` 발생
- 실패 시 `EncodeError` (stderr 요약 포함)

### 에러 체계 (utils/errors.py)

| 예외 | 발생 위치 | 사용자 메시지 |
|------|----------|-------------|
| `CancelledError` | 사용자 취소 | (별도 처리) |
| `ConfigError` | API 키 미설정 | 서버 설정 오류 |
| `TranscriptionError` | Whisper 모델/실행 | 음성 인식 실패 |
| `TranslationError` | Gemini 번역 API | 번역 실패 |
| `ReviewError` | Gemini 검토 API | 검토 실패 |
| `EncodeError` | ffmpeg 실행 | 인코딩 실패 |

`CancelledError`는 독립 예외. 나머지는 `SubtitleError` 기반 (`detail`(로그용) + `user_message`(사용자 표시용) 분리).

### 로깅 (utils/log.py)
- `subtitle.*` 네임스페이스 구조화 로거 (stderr 출력)
- 각 서비스 모듈이 `get_logger("모듈명")`으로 개별 로거 사용
- 형식: `2026-03-24 14:30:15 INFO  [subtitle.transcription] 메시지`

### 저장 API 입력 검증 (utils/srt.py → validate_blocks)
- idx 존재/중복, timestamp 형식, 시작<종료, 빈 텍스트 검사
- 검증 실패 시 400 + 문제 블록 번호 반환 (최대 5건)

### 파일 관리
- 임시 파일: `uploads/{job_id}.*` — 인코딩 성공 또는 취소 시 삭제 (`cleanup_job_files()`)
- 인코딩 실패 시 파일 유지 (자막 수정 후 재시도 가능)
- 출력 파일: `outputs/{job_id}.mp4` — 영구 보관
- job 메타데이터: `jobs/{job_id}.json` — 서버 재시작 시 복원용
- 서버 시작 시 `cleanup_stale()` 실행:
  - `STALE_DAYS` 초과 error/cancelled job 자동 삭제
  - 복원 불가 job 메타데이터 삭제
  - `ORPHAN_HOURS` 초과 orphan 파일 (job에 속하지 않는 uploads/) 삭제

### SRT 파싱 (utils/srt.py → parse_srt)
- `re.split(r'\n{2,}', content)` 블록 분리 방식 사용
- 구 regex 방식은 자막 텍스트에 숫자만 있는 줄이 있으면 오파싱됨 → 현재 방식으로 수정

---

## Frontend 구조

### shared.js — 공통 유틸리티
- `STATUS`, `TERMINAL`, `STATUS_LABELS`, `STEP_MAP`, `MSG` — 상태/메시지 상수
- `UPLOAD` — 업로드 제한값 (MAX_MB, ALLOWED_EXTS)
- `LABELS` — UI 문자열 상수 (버튼, 에러, 상태 메시지 — 다국어 대응 준비)
- `getJobId()` — URL 파라미터에서 job_id 추출
- `api()`, `apiPost()` — fetch 래퍼 (응답 파싱 + 에러 처리)
- `createPoller(jobId, onUpdate)` — 상태 폴링 (start/stop 제어)
- `fmtStatus(data)` — 상태 메시지 + 경과 시간 포맷
- `onSubtitleBroadcast()`, `broadcastSubtitleUpdated()` — BroadcastChannel 송수신

### 창 간 자막 동기화 (BroadcastChannel)
- `BroadcastChannel('subtitle-{jobId}')` 기반 — 어떤 창 조합에서도 동작
- editor → BroadcastChannel → player (직접 수신)
- editor → postMessage → index → player (fallback 경로)
- player는 두 경로 모두 수신하여 `reloadSubtitleTrack()` 호출

### player.html 오픈 방식
- URL 파라미터로 job_id 전달: `/player?job_id=xxx`
- player.html은 `URLSearchParams`로 job_id를 읽어 즉시 `/status` 조회
- `ready_to_encode` 상태에서 `/original/{job_id}`로 원본 영상 로드
- `done` 상태에서 `/download/{job_id}`로 완성 영상 로드
- `playerOpened` 플래그로 한 번만 열림 (폴링 시 중복 오픈 방지)

### player.html 동작
- URL에서 `job_id` 읽으면 즉시 status 조회 → `handleStatus()` 호출
- `ready_to_encode` 도달 시 `/original/{job_id}` 로드 + 자막 언어 선택 행 표시
- `done` 도달 시 `/download/{job_id}` 로드
- 배속: `0.5x / 1x / 1.5x / 2x / 3x`
- 스페이스바로 재생/일시정지 (input/button 포커스 시 제외)
- status 메시지에 경과 시간 표시 (폴링 2초마다 갱신)

### player.html 자동 오픈 시점
- `ready_to_encode`: 번역 완료 시 첫 오픈 (원본 영상 + 자막 미리보기)
- `done`: 인코딩 완료 시 재오픈 (완성 영상 로드)

### editor.html 기능
- 언어 탭: 영어 / 한국어 / 동시 편집
- 자막 검색, 번호로 이동
- 글자 수 경고 (42자 초과 노란색, 50자 초과 빨간색) — 가장 긴 줄 기준
- **재번역**: 각 영어 자막 블록에서 요구사항 입력 후 개별 재번역
- **저장** (Ctrl+S): 서버에 POST (블록 검증 포함), 플레이어 자막 자동 갱신
- **내보내기**: 영어 SRT / 한국어 SRT / 한국어+영어 동시 SRT 다운로드
- **가져오기**: 외부 .srt 파일을 영어 또는 한국어 자막으로 import
- textarea 높이: DOM 삽입 후 일괄 autoResize (삽입 전 호출 시 scrollHeight=0 문제)
- 재번역 실패 시 인라인 에러 표시 (alert 대신 `.inline-error` 요소, 5초 후 자동 제거)

### 텍스트 일관성 (국제화 준비)
- 모든 사용자 노출 텍스트는 한국어로 통일 (영문 브랜드명·기술 용어 제거)
- 백엔드 API 에러 메시지도 한국어 (`"Not found"` → `"찾을 수 없습니다."`)
- JS에서 참조하는 UI 문자열은 `shared.js`의 `LABELS` 상수로 중앙화
- HTML 마크업의 정적 텍스트는 인라인 유지 (변경 빈도 낮음)

### 접근성 (a11y)
- `aria-live="polite"`: 상태 메시지 영역 (index, player, editor)
- `role="alert"`: 에러 박스 (index)
- `aria-label`: 주요 버튼, 슬라이더, 입력 필드
- `:focus-visible` 키보드 포커스 스타일 (전 페이지)
- 팝업 차단 시 인라인 안내 (`#popup-hint`) 표시 (index, player)
- 처리 중 파일 입력 비활성화 (pointer-events + opacity)
- 업로드/인코딩 버튼에 CSS 스피너 애니메이션

---

## Windows 배포 (설치.bat / 실행.bat)
- Python 3.10+ 필요 (`Add to PATH` 체크 필수)
- ffmpeg: `winget install Gyan.FFmpeg`
- 가상환경: `venv/` 폴더에 자동 생성
- `.env` 파일을 별도로 전달 (GitHub에 올라가지 않음)
