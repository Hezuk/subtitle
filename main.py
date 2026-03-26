import uuid, re, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
import aiofiles

from config import (
    BASE, UPLOADS, OUTPUTS,
    MAX_UPLOAD_MB, ALLOWED_EXTS,
    MAX_BLOCKS, MAX_TEXT_LEN, MAX_REQUIREMENT_LEN, MAX_RETRIES,
)
from store.jobs import jobs, save_job, load_job, restore_jobs, cleanup_job_files, cleanup_stale
from utils.srt import (
    fmt_elapsed, parse_srt, segments_to_srt, srt_to_vtt,
    validate_blocks, load_blocks, save_blocks,
)
from utils.log import get_logger
from utils.errors import SubtitleError, EncodeError, CancelledError
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
    cleanup_stale()


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

@app.get("/shared.js")
async def shared_js():
    return FileResponse(BASE / "shared.js", media_type="application/javascript")


# ── Upload ────────────────────────────────────────────────────────────────────
@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    if not file.content_type or not file.content_type.startswith("video/"):
        raise HTTPException(400, "동영상 파일만 업로드할 수 있습니다.")

    ext = (Path(file.filename or "video.mp4").suffix or ".mp4").lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(400, f"지원하지 않는 형식입니다. ({', '.join(sorted(ALLOWED_EXTS))})")

    job_id = uuid.uuid4().hex
    input_path = UPLOADS / f"{job_id}{ext}"
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    written = 0

    async with aiofiles.open(input_path, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            written += len(chunk)
            if written > max_bytes:
                await f.close()
                input_path.unlink(missing_ok=True)
                raise HTTPException(400, f"파일이 너무 큽니다. (최대 {MAX_UPLOAD_MB}MB)")
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
    if "stage_start" in job and job["status"] not in ("done", "error", "cancelled", "ready_to_encode"):
        result["elapsed"] = fmt_elapsed(time.time() - job["stage_start"])
    return result


# ── Subtitle preview (WebVTT) ────────────────────────────────────────────────
@app.get("/subtitle/{job_id}")
async def subtitle_en(job_id: str):
    p = UPLOADS / f"{job_id}_en.srt"
    if not p.exists():
        return JSONResponse({"error": "자막을 찾을 수 없습니다."}, status_code=404)
    return Response(content=srt_to_vtt(p), media_type="text/vtt")

@app.get("/subtitle_ko/{job_id}")
async def subtitle_ko(job_id: str):
    p = UPLOADS / f"{job_id}.srt"
    if not p.exists():
        return JSONResponse({"error": "자막을 찾을 수 없습니다."}, status_code=404)
    return Response(content=srt_to_vtt(p), media_type="text/vtt")

@app.get("/subtitle_combined/{job_id}")
async def subtitle_combined(job_id: str):
    ko_path = UPLOADS / f"{job_id}.srt"
    en_path = UPLOADS / f"{job_id}_en.srt"
    if not ko_path.exists() or not en_path.exists():
        return JSONResponse({"error": "자막을 찾을 수 없습니다."}, status_code=404)
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
def _check_blocks_limits(blocks: list) -> str | None:
    if len(blocks) > MAX_BLOCKS:
        return f"블록 수가 너무 많습니다. (최대 {MAX_BLOCKS}개)"
    for b in blocks:
        if isinstance(b, dict) and len(b.get("text", "")) > MAX_TEXT_LEN:
            return f"블록 {b.get('idx', '?')}: 텍스트가 너무 깁니다. (최대 {MAX_TEXT_LEN}자)"
    return None

@app.get("/subtitles/{job_id}")
async def get_subtitles(job_id: str):
    blocks = load_blocks(UPLOADS / f"{job_id}_en.srt")
    if blocks is None:
        return JSONResponse({"error": "자막을 찾을 수 없습니다."}, status_code=404)
    return {"blocks": blocks}

@app.post("/subtitles/{job_id}")
async def save_subtitles(job_id: str, payload: dict):
    path = UPLOADS / f"{job_id}_en.srt"
    if not path.exists():
        return JSONResponse({"error": "자막을 찾을 수 없습니다."}, status_code=404)
    blocks = payload.get("blocks", [])
    for check in (_check_blocks_limits, validate_blocks):
        err = check(blocks)
        if err:
            return JSONResponse({"error": err}, status_code=400)
    save_blocks(path, blocks)
    return {"ok": True, "count": len(blocks)}

@app.get("/subtitles_ko/{job_id}")
async def get_subtitles_ko(job_id: str):
    blocks = load_blocks(UPLOADS / f"{job_id}.srt")
    if blocks is None:
        return JSONResponse({"error": "자막을 찾을 수 없습니다."}, status_code=404)
    return {"blocks": blocks}

@app.post("/subtitles_ko/{job_id}")
async def save_subtitles_ko(job_id: str, payload: dict):
    path = UPLOADS / f"{job_id}.srt"
    if not path.exists():
        return JSONResponse({"error": "자막을 찾을 수 없습니다."}, status_code=404)
    blocks = payload.get("blocks", [])
    for check in (_check_blocks_limits, validate_blocks):
        err = check(blocks)
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
        return JSONResponse({"error": "원본 영상을 찾을 수 없습니다."}, status_code=404)
    return FileResponse(input_path, media_type="video/mp4")


# ── Encode (번인 시작) ────────────────────────────────────────────────────────
@app.post("/encode/{job_id}")
async def encode(job_id: str):
    job = load_job(job_id) or {}
    if job.get("status") != "ready_to_encode":
        return JSONResponse({"error": "준비되지 않은 상태입니다."}, status_code=400)
    jobs[job_id].update({
        "status": "encoding",
        "message": "🎬 자막 인코딩 중...",
        "progress": 82,
        "phase": "encode",
        "subphase": "encoding",
    })
    save_job(job_id)
    encode_executor.submit(run_encode, job_id)
    return {"ok": True}


# ── Cancel (작업 취소) ────────────────────────────────────────────────────────
@app.post("/cancel/{job_id}")
async def cancel(job_id: str):
    job = load_job(job_id)
    if not job:
        return JSONResponse({"error": "Not found"}, status_code=404)
    status = job.get("status", "")
    if status in ("done", "error", "cancelled"):
        return JSONResponse({"error": "취소할 수 없는 상태입니다."}, status_code=400)
    jobs[job_id]["cancel_requested"] = True
    log.info("job=%s 취소 요청", job_id)
    # ready_to_encode는 실행 중인 프로세스가 없으므로 즉시 취소
    if status == "ready_to_encode":
        _do_cancel(job_id)
    return {"ok": True}


# ── Retranslate (개별 자막 재번역) ─────────────────────────────────────────────
@app.post("/retranslate/{job_id}")
async def retranslate(job_id: str, payload: dict):
    idx = str(payload.get("idx", "")).strip()
    if not idx:
        return JSONResponse({"error": "블록 번호가 필요합니다."}, status_code=400)
    requirement = payload.get("requirement", "").strip()
    if len(requirement) > MAX_REQUIREMENT_LEN:
        return JSONResponse({"error": f"요구사항이 너무 깁니다. (최대 {MAX_REQUIREMENT_LEN}자)"}, status_code=400)

    ko_blocks = load_blocks(UPLOADS / f"{job_id}.srt")
    if ko_blocks is None:
        return JSONResponse({"error": "한국어 자막을 찾을 수 없습니다."}, status_code=404)
    ko_block = next((b for b in ko_blocks if b["idx"] == idx), None)
    if not ko_block:
        return JSONResponse({"error": "해당 블록을 찾을 수 없습니다."}, status_code=404)

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
        return JSONResponse({"error": "준비되지 않은 상태입니다."}, status_code=400)
    output = job.get("output", "")
    if not output or not Path(output).exists():
        return JSONResponse({"error": "파일을 찾을 수 없습니다."}, status_code=404)
    return FileResponse(
        output,
        filename="subtitled_english.mp4",
        media_type="video/mp4",
    )


# ── Cancel helpers ────────────────────────────────────────────────────────────
def _check_cancel(job_id: str):
    """취소 요청 시 CancelledError 발생."""
    if jobs.get(job_id, {}).get("cancel_requested"):
        raise CancelledError()


def _do_cancel(job_id: str):
    """취소 상태로 전환 + 파일 정리."""
    log.info("job=%s 취소 처리 완료", job_id)
    jobs[job_id].update({
        "status": "cancelled",
        "message": "🚫 작업이 취소되었습니다.",
        "progress": 0,
    })
    save_job(job_id)
    cleanup_job_files(job_id)


# ── Pipeline: 음성인식 + 번역 ──────────────────────────────────────────────────
def run_pipeline(job_id: str, input_path: str):
    srt_path = UPLOADS / f"{job_id}.srt"
    en_srt_path = UPLOADS / f"{job_id}_en.srt"

    try:
        _check_cancel(job_id)

        # Step 1: Transcribe — 모델 로딩 + 음성 인식 (0~30%)
        t0 = time.time()
        jobs[job_id].update({
            "status": "transcribing",
            "message": "🎤 음성 인식 모델 준비 중...",
            "progress": 3,
            "phase": "transcribe",
            "subphase": "model_loading",
            "stage_start": t0,
        })
        save_job(job_id)

        def on_model_ready():
            _check_cancel(job_id)
            jobs[job_id].update({
                "message": "🎤 음성 인식 중...",
                "progress": 8,
                "subphase": "transcribing",
            })

        result = transcribe(input_path, on_model_ready=on_model_ready)
        _check_cancel(job_id)
        detected_lang = result.get("language", "unknown")

        srt_content = segments_to_srt(result["segments"])
        srt_path.write_text(srt_content, encoding="utf-8")
        blocks = parse_srt(srt_content)
        total = len(blocks)
        transcribe_elapsed = fmt_elapsed(time.time() - t0)

        # Step 2: Translate — Gemini 번역 (30~55%)
        _check_cancel(job_id)
        t1 = time.time()
        jobs[job_id].update({
            "status": "translating",
            "message": f"🌍 {total}개 블록 번역 중... (감지: {detected_lang}) | 음성인식 {transcribe_elapsed}",
            "progress": 35,
            "phase": "translate",
            "subphase": "translating",
            "current": 0,
            "total": total,
            "stage_start": t1,
        })
        save_job(job_id)

        all_translated = translate_with_gemini(blocks)
        _check_cancel(job_id)
        translate_elapsed = fmt_elapsed(time.time() - t1)
        jobs[job_id].update({
            "message": f"🌍 번역 완료 ({total}개) | {translate_elapsed}",
            "progress": 55,
            "subphase": "done",
        })

        current_en = {b["idx"]: all_translated.get(b["idx"], b["text"]) for b in blocks}

        # Step 3: Review — 품질 검토 + 재번역 (55~78%)
        _check_cancel(job_id)
        t2 = time.time()
        for attempt in range(MAX_RETRIES):
            _check_cancel(job_id)
            attempt_label = f" ({attempt+1}/{MAX_RETRIES})" if attempt > 0 else ""
            jobs[job_id].update({
                "status": "reviewing",
                "message": f"🔍 번역 품질 검토 중...{attempt_label}",
                "progress": 60 + attempt * 6,
                "phase": "review",
                "subphase": "reviewing",
                "stage_start": time.time(),
            })
            save_job(job_id)

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
                    "progress": 66 + attempt * 6,
                    "subphase": "retranslating",
                    "stage_start": time.time(),
                })
                save_job(job_id)
                bad_ko_blocks = [b for b in blocks if b["idx"] in bad_idxs]
                retranslated = translate_with_gemini(bad_ko_blocks)
                for idx, text in retranslated.items():
                    current_en[idx] = text

        review_elapsed = fmt_elapsed(time.time() - t2)
        jobs[job_id].update({
            "message": f"✅ 검토 완료 ({total}개) | {review_elapsed}",
            "progress": 78,
            "subphase": "done",
        })

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
            "phase": "ready",
            "subphase": "waiting",
            "input_path": input_path,
            "detected_lang": detected_lang,
            "timings": {
                "transcribe": transcribe_elapsed,
                "translate": translate_elapsed,
                "review": review_elapsed,
            },
        })
        save_job(job_id)

    except CancelledError:
        _do_cancel(job_id)

    except SubtitleError as e:
        log.error("job=%s 파이프라인 실패 (%s): %s", job_id, type(e).__name__, e.detail)
        jobs[job_id].update({"status": "error", "message": f"❌ {e.user_message}"})
        save_job(job_id)
        cleanup_job_files(job_id)

    except Exception as e:
        log.error("job=%s 파이프라인 예기치 않은 오류: %s", job_id, e, exc_info=True)
        jobs[job_id].update({"status": "error", "message": "❌ 예기치 않은 오류가 발생했습니다. 다시 시도해주세요."})
        save_job(job_id)
        cleanup_job_files(job_id)


# ── Encode: 번인 ──────────────────────────────────────────────────────────────
def run_encode(job_id: str):
    job = jobs[job_id]
    input_path = job["input_path"]
    en_srt_path = UPLOADS / f"{job_id}_en.srt"
    output_path = OUTPUTS / f"{job_id}.mp4"

    def cancel_check():
        return jobs.get(job_id, {}).get("cancel_requested", False)

    try:
        _check_cancel(job_id)
        t0 = time.time()
        jobs[job_id].update({"stage_start": t0})

        encode_video(input_path, en_srt_path, output_path, cancel_check=cancel_check)

        encode_elapsed = fmt_elapsed(time.time() - t0)
        prev_timings = jobs[job_id].get("timings", {})
        jobs[job_id].update({
            "status": "done",
            "message": f"✅ 완료! | 인코딩 {encode_elapsed}",
            "progress": 100,
            "phase": "done",
            "subphase": "complete",
            "output": str(output_path),
            "timings": {**prev_timings, "encode": encode_elapsed},
        })
        save_job(job_id)

        # 성공 시에만 임시 파일 삭제
        cleanup_job_files(job_id)

    except CancelledError:
        try: output_path.unlink(missing_ok=True)
        except Exception: pass
        _do_cancel(job_id)

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
