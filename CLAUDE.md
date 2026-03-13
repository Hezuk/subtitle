# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the server

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

API key is loaded from `.env` file (see `.env.example`). To run with an explicit key:
```bash
GEMINI_API_KEY=... uvicorn main:app --host 0.0.0.0 --port 8000
```

To stop the server:
```bash
pkill -f "uvicorn main:app"
```

## Architecture

Single-file FastAPI backend (`main.py`) + two frontend HTML files (`index.html`, `player.html`).

**Pipeline (two-phase):**
1. `POST /upload` → saves video, submits `run_pipeline()` to `ThreadPoolExecutor`
2. `run_pipeline()`: Whisper transcription → Gemini translation → sets status `ready_to_encode`
3. User reviews subtitles in popup player via `GET /subtitle/{job_id}` (SRT served as WebVTT)
4. `POST /encode/{job_id}` → submits `run_encode()` to executor
5. `run_encode()`: ffmpeg burn-in → sets status `done`
6. `GET /download/{job_id}` → serves final MP4

**Job state machine:** `queued → transcribing → translating → ready_to_encode → encoding → done`

**Frontend flow:**
- `index.html`: upload, progress steps, "번인 시작" button
- `player.html`: standalone popup opened via `window.open('/player', ...)`, communicates with main window via `postMessage({type: 'original'|'job', ...})`
- Player polls `/status/{job_id}` independently and switches video source at each state

## Key technical details

- Whisper model: `large-v3-turbo`, loaded lazily on first job, kept in memory (`whisper_model` global)
- Translation: single Gemini API call for all subtitle blocks at once (segments joined with `\n---\n`)
- Gemini model: `gemini-3-flash-preview` via REST (`generativelanguage.googleapis.com/v1beta/models/...`)
- ffmpeg subtitle burn-in uses `libass` (`subtitles=` filter) — requires homebrew-ffmpeg build with `--with-libass`
- ffmpeg encoding: `libx264 -crf 18 -preset fast`
- SRT→WebVTT conversion: replace `,` with `.` in timestamps, prepend `WEBVTT\n\n`
- Temp files (`uploads/`) are deleted after each phase; output files stay in `outputs/`
