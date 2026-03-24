import uuid, re, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
import aiofiles
from dotenv import load_dotenv

load_dotenv()

from config import BASE, UPLOADS, OUTPUTS
from store.jobs import jobs, save_job, load_job, restore_jobs
from utils.srt import (
    fmt_elapsed, parse_srt, segments_to_srt, srt_to_vtt,
    validate_blocks, load_blocks, save_blocks,
)
from utils.log import get_logger
from utils.errors import SubtitleError, EncodeError
from services.transcription import transcribe
from services.translation import translate_with_gemini, review_with_gemini, retranslate_with_gemini
from services.encoding import encode_video

log = get_logger("main")

# ── Executors ─────────────────────────────────────────────────────────────────
pipeline_executor = ThreadPoolExecutor(max_workers=1)
encode_executor   = ThreadPoolExecutor(max_workers=1)

app = FastAPI()


@app.on_event("startup")
async def startup():
    restore_jobs()


# ── Frontend ──────────────────────────────────────────────────────────────────
@app.get("/")
async def index():
    return FileResponse(BASE / "index.html")

@app.get("/player")
async def player():
    return FileResponse(BASE / "player.html")

@app.get("/editor")
async def editor():
    return FileResponse(BASE / "editor.html")


# ── Upload ────────────────────────────────────────────────────────────────────
@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    if not file.content_type or not file.content_type.startswith("video/"):
        raise HTTPException(400, "Video file required")

    job_id = uuid.uuid4().hex
    ext = Path(file.filename or "video.mp4").suffix or ".mp4"
    input_path = UPLOADS / f"{job_id}{ext}"

    async with aiofiles.open(input_path, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            await f.write(chunk)

    jobs[job_id] = {"status": "queued", "message": "대기 중...", "filename": file.filename}
    save_job(job_id)
    log.info("job=%s 업로드 완료: %s (%s)", job_id, file.filename, ext)
    pipeline_executor.submit(run_pipeline, job_id, str(input_path))
    return {"job_id": job_id}


# ── Status ────────────────────────────────────────────────────────────────────
@app.get("/status/{job_id}")
async def status(job_id: str):
    job = load_job(job_id)
    if not job:
        return {"status": "not_found"}
    result = dict(job)
    if "stage_start" in job and job["status"] not in ("done", "error", "ready_to_encode"):
        result["elapsed"] = fmt_elapsed(time.time() - job["stage_start"])
    return result


# ── Subtitle preview (WebVTT) ────────────────────────────────────────────────
@app.get("/subtitle/{job_id}")
async def subtitle_en(job_id: str):
    p = UPLOADS / f"{job_id}_en.srt"
    if not p.exists():
        return JSONResponse({"error": "Not found"}, status_code=404)
    return Response(content=srt_to_vtt(p), media_type="text/vtt")

@app.get("/subtitle_ko/{job_id}")
async def subtitle_ko(job_id: str):
    p = UPLOADS / f"{job_id}.srt"
    if not p.exists():
        return JSONResponse({"error": "Not found"}, status_code=404)
    return Response(content=srt_to_vtt(p), media_type="text/vtt")

@app.get("/subtitle_combined/{job_id}")
async def subtitle_combined(job_id: str):
    ko_path = UPLOADS / f"{job_id}.srt"
    en_path = UPLOADS / f"{job_id}_en.srt"
    if not ko_path.exists() or not en_path.exists():
        return JSONResponse({"error": "Not found"}, status_code=404)
    ko_blocks = parse_srt(ko_path.read_text(encoding="utf-8"))
    en_blocks = parse_srt(en_path.read_text(encoding="utf-8"))
    en_map = {b["idx"]: b["text"] for b in en_blocks}
    lines = ["WEBVTT\n"]
    for b in ko_blocks:
        ts = re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", b["timestamp"])
        en_text = en_map.get(b["idx"], "")
        lines.append(f"\n{b['idx']}\n{ts}\n{en_text}\n{b['text']}")
    return Response(content="\n".join(lines), media_type="text/vtt")


# ── Subtitles JSON (편집용) ──────────────────────────────────────────────────
@app.get("/subtitles/{job_id}")
async def get_subtitles(job_id: str):
    blocks = load_blocks(UPLOADS / f"{job_id}_en.srt")
    if blocks is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return {"blocks": blocks}

@app.post("/subtitles/{job_id}")
async def save_subtitles(job_id: str, payload: dict):
    path = UPLOADS / f"{job_id}_en.srt"
    if not path.exists():
        return JSONResponse({"error": "Not found"}, status_code=404)
    blocks = payload.get("blocks", [])
    err = validate_blocks(blocks)
    if err:
        return JSONResponse({"error": err}, status_code=400)
    save_blocks(path, blocks)
    return {"ok": True, "count": len(blocks)}

@app.get("/subtitles_ko/{job_id}")
async def get_subtitles_ko(job_id: str):
    blocks = load_blocks(UPLOADS / f"{job_id}.srt")
    if blocks is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return {"blocks": blocks}

@app.post("/subtitles_ko/{job_id}")
async def save_subtitles_ko(job_id: str, payload: dict):
    path = UPLOADS / f"{job_id}.srt"
    if not path.exists():
        return JSONResponse({"error": "Not found"}, status_code=404)
    blocks = payload.get("blocks", [])
    err = validate_blocks(blocks)
    if err:
        return JSONResponse({"error": err}, status_code=400)
    save_blocks(path, blocks)
    return {"ok": True, "count": len(blocks)}


# ── Original video ────────────────────────────────────────────────────────────
@app.get("/original/{job_id}")
async def original_video(job_id: str):
    job = load_job(job_id) or {}
    input_path = job.get("input_path")
    if not input_path or not Path(input_path).exists():
        return JSONResponse({"error": "Not found"}, status_code=404)
    return FileResponse(input_path, media_type="video/mp4")


# ── Encode (번인 시작) ────────────────────────────────────────────────────────
@app.post("/encode/{job_id}")
async def encode(job_id: str):
    job = load_job(job_id) or {}
    if job.get("status") != "ready_to_encode":
        return JSONResponse({"error": "Not ready"}, status_code=400)
    jobs[job_id].update({"status": "encoding", "message": "🎬 자막 인코딩 중...", "progress": 82})
    save_job(job_id)
    encode_executor.submit(run_encode, job_id)
    return {"ok": True}


# ── Retranslate (개별 자막 재번역) ─────────────────────────────────────────────
@app.post("/retranslate/{job_id}")
async def retranslate(job_id: str, payload: dict):
    idx = str(payload.get("idx", ""))
    requirement = payload.get("requirement", "").strip()

    ko_blocks = load_blocks(UPLOADS / f"{job_id}.srt")
    if ko_blocks is None:
        return JSONResponse({"error": "Korean SRT not found"}, status_code=404)
    ko_block = next((b for b in ko_blocks if b["idx"] == idx), None)
    if not ko_block:
        return JSONResponse({"error": "Block not found"}, status_code=404)

    en_blocks = load_blocks(UPLOADS / f"{job_id}_en.srt") or []
    en_block = next((b for b in en_blocks if b["idx"] == idx), None)
    current_en = en_block["text"] if en_block else ""

    text = retranslate_with_gemini(ko_block["text"], current_en, requirement)
    return {"text": text}


# ── Download ──────────────────────────────────────────────────────────────────
@app.get("/download/{job_id}")
async def download(job_id: str):
    job = load_job(job_id) or {}
    if job.get("status") != "done":
        return JSONResponse({"error": "Not ready"}, status_code=400)
    output = job.get("output", "")
    if not output or not Path(output).exists():
        return JSONResponse({"error": "File not found"}, status_code=404)
    return FileResponse(
        output,
        filename="subtitled_english.mp4",
        media_type="video/mp4",
    )


# ── Pipeline: 음성인식 + 번역 ──────────────────────────────────────────────────
def run_pipeline(job_id: str, input_path: str):
    srt_path = UPLOADS / f"{job_id}.srt"
    en_srt_path = UPLOADS / f"{job_id}_en.srt"

    try:
        # Step 1: Transcribe
        t0 = time.time()
        jobs[job_id].update({"status": "transcribing", "message": "🎤 음성 인식 중...", "stage_start": t0})
        save_job(job_id)

        result = transcribe(input_path)
        detected_lang = result.get("language", "unknown")

        srt_content = segments_to_srt(result["segments"])
        srt_path.write_text(srt_content, encoding="utf-8")
        transcribe_elapsed = fmt_elapsed(time.time() - t0)

        # Step 2: Translate
        t1 = time.time()
        jobs[job_id].update({
            "status": "translating",
            "message": f"🌍 영어로 번역 중... (감지된 언어: {detected_lang}) | 음성인식 {transcribe_elapsed}",
            "stage_start": t1,
        })
        save_job(job_id)

        blocks = parse_srt(srt_content)
        total = len(blocks)

        all_translated = translate_with_gemini(blocks)
        translate_elapsed = fmt_elapsed(time.time() - t1)
        jobs[job_id].update({"message": f"🌍 번역 완료 ({total}/{total}) | {translate_elapsed}", "progress": 70})

        current_en = {b["idx"]: all_translated.get(b["idx"], b["text"]) for b in blocks}

        # Step 3: 검토 + 불량 재번역 (최대 2회)
        MAX_RETRIES = 2
        t2 = time.time()
        for attempt in range(MAX_RETRIES):
            attempt_label = f" ({attempt+1}/{MAX_RETRIES})" if attempt > 0 else ""
            jobs[job_id].update({
                "message": f"🔍 번역 품질 검토 중...{attempt_label}",
                "progress": 73,
                "stage_start": time.time(),
            })

            en_blocks_list = [
                {"idx": b["idx"], "timestamp": b["timestamp"], "text": current_en[b["idx"]]}
                for b in blocks
            ]
            reviewed = review_with_gemini(en_blocks_list)

            for idx, text in reviewed.items():
                if text != "__RETRANSLATE__":
                    current_en[idx] = text

            bad_idxs = {idx for idx, text in reviewed.items() if text == "__RETRANSLATE__"}

            if not bad_idxs:
                break

            if attempt < MAX_RETRIES - 1:
                jobs[job_id].update({
                    "message": f"🔄 불량 {len(bad_idxs)}개 재번역 중... ({attempt+2}/{MAX_RETRIES}회차)",
                    "progress": 74,
                    "stage_start": time.time(),
                })
                bad_ko_blocks = [b for b in blocks if b["idx"] in bad_idxs]
                retranslated = translate_with_gemini(bad_ko_blocks)
                for idx, text in retranslated.items():
                    current_en[idx] = text

        review_elapsed = fmt_elapsed(time.time() - t2)
        jobs[job_id].update({"message": f"✅ 검토 완료 ({total}/{total}) | {review_elapsed}", "progress": 75})

        en_lines = []
        for b in blocks:
            text = current_en.get(b["idx"], b["text"])
            en_lines.append(f"{b['idx']}\n{b['timestamp']}\n{text}\n")
        en_srt_path.write_text("\n".join(en_lines), encoding="utf-8")

        # 번역+검토 완료 — 사용자 확인 대기
        jobs[job_id].update({
            "status": "ready_to_encode",
            "message": "✅ 번역 및 품질 검토 완료! 자막을 확인한 후 번인을 시작하세요.",
            "progress": 80,
            "input_path": input_path,
            "detected_lang": detected_lang,
            "timings": {
                "transcribe": transcribe_elapsed,
                "translate": translate_elapsed,
                "review": review_elapsed,
            },
        })
        save_job(job_id)

    except SubtitleError as e:
        log.error("job=%s 파이프라인 실패 (%s): %s", job_id, type(e).__name__, e.detail)
        jobs[job_id].update({"status": "error", "message": f"❌ {e.user_message}"})
        save_job(job_id)
        for p in [Path(input_path), srt_path, en_srt_path]:
            try: p.unlink(missing_ok=True)
            except Exception: pass

    except Exception as e:
        log.error("job=%s 파이프라인 예기치 않은 오류: %s", job_id, e, exc_info=True)
        jobs[job_id].update({"status": "error", "message": "❌ 예기치 않은 오류가 발생했습니다."})
        save_job(job_id)
        for p in [Path(input_path), srt_path, en_srt_path]:
            try: p.unlink(missing_ok=True)
            except Exception: pass


# ── Encode: 번인 ──────────────────────────────────────────────────────────────
def run_encode(job_id: str):
    job = jobs[job_id]
    input_path = job["input_path"]
    en_srt_path = UPLOADS / f"{job_id}_en.srt"
    output_path = OUTPUTS / f"{job_id}.mp4"

    try:
        t0 = time.time()
        jobs[job_id].update({"stage_start": t0})

        encode_video(input_path, en_srt_path, output_path)

        encode_elapsed = fmt_elapsed(time.time() - t0)
        prev_timings = jobs[job_id].get("timings", {})
        jobs[job_id].update({
            "status": "done",
            "message": f"✅ 완료! | 인코딩 {encode_elapsed}",
            "progress": 100,
            "output": str(output_path),
            "timings": {**prev_timings, "encode": encode_elapsed},
        })
        save_job(job_id)

        # 성공 시에만 임시 파일 삭제
        ko_srt_path = UPLOADS / f"{job_id}.srt"
        for p in [Path(input_path), en_srt_path, ko_srt_path]:
            try: p.unlink(missing_ok=True)
            except Exception: pass

    except EncodeError as e:
        log.error("job=%s 인코딩 실패: %s", job_id, e.detail)
        try: output_path.unlink(missing_ok=True)
        except Exception: pass
        jobs[job_id].update({
            "status": "ready_to_encode",
            "message": f"❌ {e.user_message}",
            "progress": 80,
        })
        save_job(job_id)

    except Exception as e:
        log.error("job=%s 인코딩 예기치 않은 오류: %s", job_id, e, exc_info=True)
        try: output_path.unlink(missing_ok=True)
        except Exception: pass
        jobs[job_id].update({
            "status": "ready_to_encode",
            "message": "❌ 인코딩 중 예기치 않은 오류 — 다시 시도해주세요.",
            "progress": 80,
        })
        save_job(job_id)
