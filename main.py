import uuid, re, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Body
from fastapi.responses import FileResponse, JSONResponse, Response
import aiofiles

from config import (
    WEB_DIR, UPLOADS, OUTPUTS,
    MAX_UPLOAD_MB, ALLOWED_EXTS,
    MAX_BLOCKS, MAX_TEXT_LEN, MAX_REQUIREMENT_LEN, MAX_RETRIES,
)
from store.jobs import jobs, _lock as jobs_lock, save_job, update_job, load_job, restore_jobs, cleanup_job_files, cleanup_stale
from utils.srt import (
    fmt_elapsed, parse_srt, segments_to_srt, srt_to_vtt,
    validate_blocks, load_blocks, save_blocks,
)
from utils.log import get_logger
from utils.errors import SubtitleError, EncodeError, CancelledError
from services.transcription import (
    AVAILABLE_WHISPER_MODELS,
    DEFAULT_WHISPER_MODEL,
    normalize_whisper_model,
    transcribe,
)
from services.translation import (
    MODEL_LABELS,
    normalize_translation_model,
    refine_blocks,
    retranslate_block,
    review_ko_blocks,
    review_blocks,
    translate_blocks,
)
from services.encoding import encode_video

log = get_logger("main")

# ── Executors ─────────────────────────────────────────────────────────────────
pipeline_executor = ThreadPoolExecutor(max_workers=1)
encode_executor   = ThreadPoolExecutor(max_workers=1)

app = FastAPI()


def _safe_path(requested: str, allowed_dir: Path) -> Path | None:
    """경로가 허용된 디렉토리 안에 있는지 검증. 벗어나면 None 반환."""
    try:
        p = Path(requested).resolve()
        if p.is_relative_to(allowed_dir.resolve()):
            return p
    except (ValueError, OSError):
        pass
    return None


@app.on_event("startup")
async def startup():
    restore_jobs()
    cleanup_stale()


# ── Frontend ──────────────────────────────────────────────────────────────────
@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")

@app.get("/player")
async def player():
    return FileResponse(WEB_DIR / "player.html")

@app.get("/editor")
async def editor():
    return FileResponse(WEB_DIR / "editor.html")

@app.get("/shared.js")
async def shared_js():
    return FileResponse(WEB_DIR / "shared.js", media_type="application/javascript")


# ── Upload ────────────────────────────────────────────────────────────────────
def _validate_translation_model(value: str | None) -> str:
    try:
        return normalize_translation_model(value)
    except ValueError:
        supported = ", ".join(MODEL_LABELS.keys())
        raise HTTPException(400, f"지원하지 않는 번역 모델입니다. ({supported})")


def _validate_whisper_model(value: str | None) -> str:
    try:
        return normalize_whisper_model(value)
    except ValueError:
        supported = ", ".join(AVAILABLE_WHISPER_MODELS)
        raise HTTPException(400, f"지원하지 않는 Whisper 모델입니다. ({supported})")


def _get_job_translation_model(job_id: str) -> str:
    job = load_job(job_id) or {}
    model = job.get("translation_model")
    try:
        return normalize_translation_model(model)
    except ValueError:
        log.warning("job=%s 잘못된 translation_model=%r, 기본값으로 대체", job_id, model)
        return normalize_translation_model(None)


def _get_job_whisper_model(job_id: str) -> str:
    job = load_job(job_id) or {}
    model = job.get("whisper_model")
    try:
        return normalize_whisper_model(model)
    except ValueError:
        log.warning("job=%s 잘못된 whisper_model=%r, 기본값으로 대체", job_id, model)
        return DEFAULT_WHISPER_MODEL


def _get_ko_prep_state(job_id: str, job: dict | None = None) -> tuple[bool, bool]:
    job = job or load_job(job_id) or {}
    raw_srt_path = UPLOADS / f"{job_id}_ko_raw.srt"

    ko_refined = job.get("ko_refined")
    if ko_refined is None:
        ko_refined = raw_srt_path.exists()

    ko_reviewed = job.get("ko_reviewed")
    if ko_reviewed is None:
        ko_reviewed = job.get("status") in (
            "ready_to_review_ko",
            "ready_to_translate",
            "ready_to_encode",
            "encoding",
            "done",
        )

    return bool(ko_refined), bool(ko_reviewed)


def _ready_to_translate_state(job_id: str, job: dict | None = None) -> dict:
    job = job or load_job(job_id) or {}
    refine_ko = job.get("refine_ko", True)
    source_has_ko_subtitle = job.get("source_has_ko_subtitle", False)
    ko_refined, ko_reviewed = _get_ko_prep_state(job_id, job)

    if source_has_ko_subtitle and not ko_reviewed:
        return {"progress": 45, "phase": "review_ko", "resume_label": "한국어 AI 검토 재시작"}
    if refine_ko and not ko_refined:
        return {"progress": 30, "phase": "refine_ko", "resume_label": "한국어 자막 다듬기 재시작"}
    if refine_ko and ko_refined and not ko_reviewed:
        return {"progress": 45, "phase": "review_ko", "resume_label": "한국어 AI 검토 재시작"}
    return {"progress": 30, "phase": "translate", "resume_label": "번역 재시도"}


async def _save_upload_file(upload: UploadFile, dest: Path, max_bytes: int | None = None):
    written = 0
    async with aiofiles.open(dest, "wb") as f:
        while chunk := await upload.read(1024 * 1024):
            written += len(chunk)
            if max_bytes is not None and written > max_bytes:
                await f.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(400, f"파일이 너무 큽니다. (최대 {MAX_UPLOAD_MB}MB)")
            await f.write(chunk)
    return written


def _load_uploaded_srt(path: Path) -> list[dict]:
    try:
        content = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(400, "한국어 자막은 UTF-8 인코딩의 SRT 파일만 업로드할 수 있습니다.")

    blocks = parse_srt(content)
    for check in (_check_blocks_limits, validate_blocks):
        err = check(blocks)
        if err:
            raise HTTPException(400, f"한국어 자막 파일이 올바르지 않습니다. {err}")

    save_blocks(path, blocks)
    return blocks


@app.post("/upload")
async def upload(
    file: UploadFile = File(...),
    subtitle_file: UploadFile | None = File(None),
    translation_model: str = Form("claude"),
    whisper_model: str = Form(DEFAULT_WHISPER_MODEL),
    refine_ko: bool = Form(True),
):
    if not file.content_type or not file.content_type.startswith("video/"):
        raise HTTPException(400, "동영상 파일만 업로드할 수 있습니다.")
    translation_model = _validate_translation_model(translation_model)
    whisper_model = _validate_whisper_model(whisper_model)

    ext = (Path(file.filename or "video.mp4").suffix or ".mp4").lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(400, f"지원하지 않는 형식입니다. ({', '.join(sorted(ALLOWED_EXTS))})")

    job_id = uuid.uuid4().hex
    input_path = UPLOADS / f"{job_id}{ext}"
    srt_path = UPLOADS / f"{job_id}.srt"
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    source_has_ko_subtitle = subtitle_file is not None and bool(subtitle_file.filename)

    await _save_upload_file(file, input_path, max_bytes=max_bytes)

    if source_has_ko_subtitle:
        subtitle_ext = (Path(subtitle_file.filename or "subtitle.srt").suffix or "").lower()
        if subtitle_ext != ".srt":
            input_path.unlink(missing_ok=True)
            raise HTTPException(400, "한국어 자막은 .srt 파일만 업로드할 수 있습니다.")
        await _save_upload_file(subtitle_file, srt_path)
        try:
            _load_uploaded_srt(srt_path)
        except HTTPException:
            input_path.unlink(missing_ok=True)
            srt_path.unlink(missing_ok=True)
            raise

    with jobs_lock:
        jobs[job_id] = {
            "status": "queued",
            "message": "대기 중... (한국어 자막 사용)" if source_has_ko_subtitle else "대기 중...",
            "filename": file.filename,
            "input_path": str(input_path),
            "ext": ext,
            "translation_model": translation_model,
            "whisper_model": whisper_model,
            "refine_ko": refine_ko,
            "ko_refined": False,
            "ko_reviewed": False,
            "progress": 30 if source_has_ko_subtitle else 0,
            "source_has_ko_subtitle": source_has_ko_subtitle,
            "created_at": time.time(),
        }
    save_job(job_id)
    log.info(
        "job=%s 업로드 완료: %s (%s, translation_model=%s, whisper_model=%s, source_has_ko_subtitle=%s)",
        job_id,
        file.filename,
        ext,
        translation_model,
        whisper_model,
        source_has_ko_subtitle,
    )
    if source_has_ko_subtitle:
        pipeline_executor.submit(run_translate_pipeline, job_id)
    else:
        pipeline_executor.submit(run_pipeline, job_id, str(input_path))
    return {
        "job_id": job_id,
        "translation_model": translation_model,
        "whisper_model": whisper_model,
        "source_has_ko_subtitle": source_has_ko_subtitle,
    }


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
    safe = _safe_path(input_path, UPLOADS) if input_path else None
    if not safe or not safe.exists():
        return JSONResponse({"error": "원본 영상을 찾을 수 없습니다."}, status_code=404)
    return FileResponse(safe, media_type="video/mp4")


# ── Encode (번인 시작) ────────────────────────────────────────────────────────
@app.post("/encode/{job_id}")
async def encode(job_id: str):
    job = load_job(job_id) or {}
    if job.get("status") != "ready_to_encode":
        return JSONResponse({"error": "준비되지 않은 상태입니다."}, status_code=400)
    update_job(job_id,
        status="encoding",
        message="🎬 자막 인코딩 중...",
        progress=82,
        phase="encode",
        subphase="encoding",
    )
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
    with jobs_lock:
        jobs[job_id]["cancel_requested"] = True
    log.info("job=%s 취소 요청 (status=%s)", job_id, status)
    # 실행 중인 프로세스가 없는 상태는 즉시 취소
    if status in ("queued", "ready_to_encode"):
        _do_cancel(job_id)
    return {"ok": True}


# ── Retry translate (번역 재시도) ──────────────────────────────────────────────
@app.post("/retry/{job_id}")
async def retry_translate(job_id: str, payload: dict | None = Body(default=None)):
    job = load_job(job_id) or {}
    prev_status = job.get("status")
    if prev_status not in ("ready_to_translate", "ready_to_review_ko"):
        return JSONResponse({"error": "번역을 시작할 수 있는 상태가 아닙니다."}, status_code=400)
    srt_path = UPLOADS / f"{job_id}.srt"
    if not srt_path.exists():
        return JSONResponse({"error": "한국어 자막 파일을 찾을 수 없습니다."}, status_code=404)
    payload = payload or {}
    translation_model = _validate_translation_model(payload.get("translation_model") or job.get("translation_model"))
    model_label = MODEL_LABELS[translation_model]
    resume_state = _ready_to_translate_state(job_id, job)
    queue_msg = f"대기 중... ({'번역 시작' if prev_status == 'ready_to_review_ko' else resume_state['resume_label']}: {model_label})"
    update_job(job_id,
        status="queued",
        message=queue_msg,
        progress=45 if prev_status == "ready_to_review_ko" else resume_state["progress"],
        phase="translate" if prev_status == "ready_to_review_ko" else resume_state["phase"],
        subphase="queued",
        translation_model=translation_model,
        error_reason=None,
        error_detail=None,
        next_action=None,
        retryable=None,
        error_code=None,
    )
    pipeline_executor.submit(run_translate_pipeline, job_id)
    return {"ok": True, "translation_model": translation_model}


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

    translation_model = _get_job_translation_model(job_id)
    try:
        text = retranslate_block(ko_block["text"], current_en, requirement, translation_model)
    except SubtitleError as e:
        payload = _error_fields(e)
        return JSONResponse({
            "error": payload["error_reason"],
            "detail": payload["error_detail"],
            "next_action": payload["next_action"],
            "retryable": payload["retryable"],
            "error_code": payload["error_code"],
        }, status_code=502 if payload["retryable"] else 400)
    return {"text": text}


# ── Download ──────────────────────────────────────────────────────────────────
@app.get("/download/{job_id}")
async def download(job_id: str):
    job = load_job(job_id) or {}
    if job.get("status") != "done":
        return JSONResponse({"error": "준비되지 않은 상태입니다."}, status_code=400)
    output = job.get("output", "")
    safe = _safe_path(output, OUTPUTS) if output else None
    if not safe or not safe.exists():
        return JSONResponse({"error": "파일을 찾을 수 없습니다."}, status_code=404)
    return FileResponse(
        safe,
        filename="subtitled_english.mp4",
        media_type="video/mp4",
    )

@app.get("/download_subtitle_ko/{job_id}")
async def download_subtitle_ko(job_id: str):
    job = load_job(job_id) or {}
    srt_path = UPLOADS / f"{job_id}.srt"
    if not srt_path.exists():
        return JSONResponse({"error": "한국어 자막 파일을 찾을 수 없습니다."}, status_code=404)
    base_name = Path(job.get("filename") or f"{job_id}.mp4").stem
    return FileResponse(
        srt_path,
        filename=f"{base_name}_ko.srt",
        media_type="application/x-subrip",
    )


# ── Cancel helpers ────────────────────────────────────────────────────────────
def _check_cancel(job_id: str):
    """취소 요청 시 CancelledError 발생."""
    with jobs_lock:
        requested = jobs.get(job_id, {}).get("cancel_requested")
    if requested:
        raise CancelledError()


def _run_cancellable(job_id: str, fn, *args, poll_interval: float = 0.5, **kwargs):
    """fn을 별도 스레드에서 실행하면서 취소 요청을 poll_interval마다 확인.

    fn이 완료되면 결과를 반환하고, 취소 요청이 감지되면 CancelledError를 발생시킨다.
    백그라운드 스레드는 즉시 중단할 수 없지만, shutdown(wait=False)로 대기 없이 반환한다.
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(fn, *args, **kwargs)
    try:
        while True:
            try:
                result = future.result(timeout=poll_interval)
                pool.shutdown(wait=False)
                return result
            except FuturesTimeout:
                _check_cancel(job_id)
    except:
        pool.shutdown(wait=False, cancel_futures=True)
        raise


def _do_cancel(job_id: str):
    """취소 상태로 전환 + 파일 정리."""
    log.info("job=%s 취소 처리 완료", job_id)
    update_job(job_id,
        status="cancelled",
        message="🚫 작업이 취소되었습니다.",
        progress=0,
    )
    cleanup_job_files(job_id)


def _short_error_detail(detail: str, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", (detail or "")).strip()
    if len(text) <= limit:
        return text
    return text[:limit - 1] + "…"


def _error_fields(err: SubtitleError | Exception) -> dict:
    if isinstance(err, SubtitleError):
        reason = err.user_message
        detail = _short_error_detail(err.detail)
        next_action = getattr(err, "hint", "") or ""
        retryable = bool(getattr(err, "retryable", False))
        error_code = getattr(err, "error_code", type(err).__name__)
    else:
        reason = "예기치 않은 오류가 발생했습니다."
        detail = _short_error_detail(str(err))
        next_action = "잠시 후 다시 시도해주세요."
        retryable = True
        error_code = "unexpected_error"

    return {
        "message": f"❌ {reason}",
        "error_reason": reason,
        "error_detail": detail,
        "next_action": next_action,
        "retryable": retryable,
        "error_code": error_code,
    }


# ── Korean refine/review steps (한국어 자막 다듬기 + AI 검토) ──────────────────
def _run_refine_step(job_id: str, blocks: list[dict], total: int, translation_model: str,
                     extra_timings: dict | None = None):
    """한국어 자막 다듬기 단계만 수행. 완료된 블록과 timings를 반환."""
    model_label = MODEL_LABELS[translation_model]
    srt_path = UPLOADS / f"{job_id}.srt"
    raw_srt_path = UPLOADS / f"{job_id}_ko_raw.srt"

    _check_cancel(job_id)
    t_refine = time.time()
    update_job(job_id,
        status="refining_ko",
        message=f"✏️ {total}개 한국어 자막 다듬는 중... ({model_label})",
        progress=32,
        phase="refine_ko",
        subphase="refining",
        stage_start=t_refine,
    )

    refined = _run_cancellable(job_id, refine_blocks, blocks, translation_model)
    _check_cancel(job_id)

    # 원본 KO SRT 보존 (최초 1회만)
    if not raw_srt_path.exists():
        raw_srt_path.write_text(srt_path.read_text(encoding="utf-8"), encoding="utf-8")

    # blocks 업데이트 + 파일 저장
    refined_blocks = [{**b, "text": refined.get(b["idx"], b["text"])} for b in blocks]
    save_blocks(srt_path, refined_blocks)

    refine_elapsed = fmt_elapsed(time.time() - t_refine)
    timings = {**(extra_timings or {}), "refine_ko": refine_elapsed}
    update_job(job_id,
        message=f"✏️ 한국어 자막 다듬기 완료 ({total}개, {model_label}) | {refine_elapsed}",
        progress=40,
        phase="refine_ko",
        subphase="done",
        timings=timings,
        ko_refined=True,
        ko_reviewed=False,
    )
    return refined_blocks, timings


def _run_review_ko_step(job_id: str, blocks: list[dict], total: int, translation_model: str,
                        extra_timings: dict | None = None):
    """다듬어진 한국어 자막을 AI로 검토한 뒤 사용자 확인 단계로 전환."""
    model_label = MODEL_LABELS[translation_model]
    srt_path = UPLOADS / f"{job_id}.srt"

    _check_cancel(job_id)
    t_review = time.time()
    update_job(job_id,
        status="reviewing_ko",
        message=f"🤖 {total}개 한국어 자막 AI 검토 중... ({model_label})",
        progress=42,
        phase="review_ko",
        subphase="reviewing",
        stage_start=t_review,
    )

    reviewed = _run_cancellable(job_id, review_ko_blocks, blocks, translation_model)
    _check_cancel(job_id)

    reviewed_blocks = [{**b, "text": reviewed.get(b["idx"], b["text"])} for b in blocks]
    save_blocks(srt_path, reviewed_blocks)

    review_elapsed = fmt_elapsed(time.time() - t_review)
    timings = {**(extra_timings or {}), "review_ko": review_elapsed}
    update_job(job_id,
        status="ready_to_review_ko",
        message="🤖 한국어 자막 AI 검토 완료! 자막을 확인한 후 번역을 시작하세요.",
        progress=45,
        phase="review_ko",
        subphase="waiting",
        timings=timings,
        ko_refined=True,
        ko_reviewed=True,
        error_reason=None,
        error_detail=None,
        next_action=None,
        retryable=None,
        error_code=None,
    )


def _run_ko_prep_steps(job_id: str, blocks: list[dict], total: int, translation_model: str,
                       extra_timings: dict | None = None):
    refined_blocks, timings = _run_refine_step(job_id, blocks, total, translation_model, extra_timings=extra_timings)
    _run_review_ko_step(job_id, refined_blocks, total, translation_model, extra_timings=timings)


# ── Translation steps (번역+검토 로직) ────────────────────────────────────────
def _run_translation_steps(job_id: str, blocks: list[dict], total: int, en_srt_path: Path,
                           translation_model: str, extra_timings: dict | None = None):
    """번역 → 검토 → 재번역 → SRT 저장 → ready_to_encode. 공통 로직."""
    model_label = MODEL_LABELS[translation_model]

    # Step: Translate (45~62%)
    _check_cancel(job_id)
    t1 = time.time()
    update_job(job_id,
        status="translating",
        message=f"🌍 {total}개 블록 번역 중... ({model_label})",
        progress=48,
        phase="translate",
        subphase="translating",
        current=0,
        total=total,
        stage_start=t1,
    )

    all_translated = _run_cancellable(job_id, translate_blocks, blocks, translation_model)
    _check_cancel(job_id)
    translate_elapsed = fmt_elapsed(time.time() - t1)
    with jobs_lock:
        jobs[job_id].update({
            "message": f"🌍 번역 완료 ({total}개, {model_label}) | {translate_elapsed}",
            "progress": 62,
            "subphase": "done",
        })

    current_en = {b["idx"]: all_translated.get(b["idx"], b["text"]) for b in blocks}

    # Step: Review — 품질 검토 + 재번역 (62~78%)
    _check_cancel(job_id)
    t2 = time.time()
    for attempt in range(MAX_RETRIES):
        _check_cancel(job_id)
        attempt_label = f" ({attempt+1}/{MAX_RETRIES})" if attempt > 0 else ""
        update_job(job_id,
            status="reviewing",
            message=f"🔍 번역 품질 검토 중... ({model_label}){attempt_label}",
            progress=65 + attempt * 6,
            phase="review",
            subphase="reviewing",
            stage_start=time.time(),
        )

        en_blocks_list = [
            {"idx": b["idx"], "timestamp": b["timestamp"], "text": current_en[b["idx"]]}
            for b in blocks
        ]
        reviewed = _run_cancellable(job_id, review_blocks, en_blocks_list, translation_model)

        for idx, text in reviewed.items():
            if text != "__RETRANSLATE__":
                current_en[idx] = text

        bad_idxs = {idx for idx, text in reviewed.items() if text == "__RETRANSLATE__"}

        if not bad_idxs:
            break

        if attempt < MAX_RETRIES - 1:
            update_job(job_id,
                message=f"🔄 불량 {len(bad_idxs)}개 재번역 중... ({model_label}, {attempt+2}/{MAX_RETRIES}회차)",
                progress=71 + attempt * 6,
                subphase="retranslating",
                stage_start=time.time(),
            )
            bad_ko_blocks = [b for b in blocks if b["idx"] in bad_idxs]
            retranslated = _run_cancellable(job_id, translate_blocks, bad_ko_blocks, translation_model)
            for idx, text in retranslated.items():
                current_en[idx] = text

    review_elapsed = fmt_elapsed(time.time() - t2)
    with jobs_lock:
        jobs[job_id].update({
            "message": f"✅ 검토 완료 ({total}개, {model_label}) | {review_elapsed}",
            "progress": 78,
            "subphase": "done",
        })

    en_lines = []
    for b in blocks:
        text = current_en.get(b["idx"], b["text"])
        en_lines.append(f"{b['idx']}\n{b['timestamp']}\n{text}\n")
    en_srt_path.write_text("\n".join(en_lines), encoding="utf-8")

    # 번역+검토 완료 — 사용자 확인 대기
    timings = {**(extra_timings or {}), "translate": translate_elapsed, "review": review_elapsed}
    update_job(job_id,
        status="ready_to_encode",
        message="✅ 번역 및 품질 검토 완료! 자막을 확인한 후 번인을 시작하세요.",
        progress=80,
        phase="ready",
        subphase="waiting",
        timings=timings,
        error_reason=None,
        error_detail=None,
        next_action=None,
        retryable=None,
        error_code=None,
    )


# ── Pipeline: 음성인식 + 번역 ──────────────────────────────────────────────────
def run_pipeline(job_id: str, input_path: str):
    srt_path = UPLOADS / f"{job_id}.srt"
    en_srt_path = UPLOADS / f"{job_id}_en.srt"

    try:
        _check_cancel(job_id)
        whisper_model = _get_job_whisper_model(job_id)

        # Step 1: Transcribe — 모델 로딩 + 음성 인식 (0~30%)
        t0 = time.time()
        update_job(job_id,
            status="transcribing",
            message=f"🎤 음성 인식 모델 준비 중... ({whisper_model})",
            progress=3,
            phase="transcribe",
            subphase="model_loading",
            stage_start=t0,
        )

        def on_model_ready(model_name: str):
            _check_cancel(job_id)
            with jobs_lock:
                jobs[job_id].update({
                    "message": f"🎤 음성 인식 중... ({model_name})",
                    "progress": 8,
                    "subphase": "transcribing",
                })

        result = _run_cancellable(
            job_id, transcribe, input_path,
            model_name=whisper_model, on_model_ready=on_model_ready,
        )
        _check_cancel(job_id)
        detected_lang = result.get("language", "unknown")
        translation_model = _get_job_translation_model(job_id)

        srt_content = segments_to_srt(result["segments"])
        srt_path.write_text(srt_content, encoding="utf-8")
        blocks = parse_srt(srt_content)
        total = len(blocks)
        transcribe_elapsed = fmt_elapsed(time.time() - t0)

        update_job(job_id, detected_lang=detected_lang)

        refine_ko = load_job(job_id).get("refine_ko", True)
        if refine_ko:
            _run_ko_prep_steps(job_id, blocks, total, translation_model,
                               extra_timings={"transcribe": transcribe_elapsed})
        else:
            _run_translation_steps(job_id, blocks, total, en_srt_path,
                                   translation_model=translation_model,
                                   extra_timings={"transcribe": transcribe_elapsed})

    except CancelledError:
        _do_cancel(job_id)

    except SubtitleError as e:
        log.error("job=%s 파이프라인 실패 (%s): %s", job_id, type(e).__name__, e.detail)
        if srt_path.exists():
            # 한국어 자막이 이미 생성된 상태 → 번역만 재시도 가능
            error_fields = _error_fields(e)
            resume_state = _ready_to_translate_state(job_id)
            update_job(job_id,
                status="ready_to_translate",
                progress=resume_state["progress"],
                phase=resume_state["phase"],
                subphase="failed",
                **error_fields,
            )
        else:
            update_job(job_id, status="error", **_error_fields(e))
            cleanup_job_files(job_id)

    except Exception as e:
        log.error("job=%s 파이프라인 예기치 않은 오류: %s", job_id, e, exc_info=True)
        if srt_path.exists():
            error_fields = _error_fields(e)
            resume_state = _ready_to_translate_state(job_id)
            update_job(job_id,
                status="ready_to_translate",
                progress=resume_state["progress"],
                phase=resume_state["phase"],
                subphase="failed",
                **error_fields,
            )
        else:
            update_job(job_id, status="error", **_error_fields(e))
            cleanup_job_files(job_id)


# ── Translate pipeline (번역 재시도용) ─────────────────────────────────────────
def run_translate_pipeline(job_id: str):
    """한국어 SRT가 이미 존재할 때, 번역 단계부터 재실행."""
    srt_path = UPLOADS / f"{job_id}.srt"
    en_srt_path = UPLOADS / f"{job_id}_en.srt"

    try:
        _check_cancel(job_id)
        srt_content = srt_path.read_text(encoding="utf-8")
        blocks = parse_srt(srt_content)
        total = len(blocks)
        translation_model = _get_job_translation_model(job_id)

        job = load_job(job_id) or {}
        refine_ko = job.get("refine_ko", True)
        source_has_ko_subtitle = job.get("source_has_ko_subtitle", False)
        ko_refined, ko_reviewed = _get_ko_prep_state(job_id, job)

        if source_has_ko_subtitle and not ko_reviewed:
            # 업로드한 한국어 SRT는 다듬기를 건너뛰고 AI 검토만 수행
            _run_review_ko_step(job_id, blocks, total, translation_model,
                                extra_timings=job.get("timings"))
        elif refine_ko and not ko_refined:
            # 아직 다듬지 않은 경우 → 다듬기 + AI 검토 후 ready_to_review_ko에서 대기
            _run_ko_prep_steps(job_id, blocks, total, translation_model,
                               extra_timings=job.get("timings"))
        elif refine_ko and ko_refined and not ko_reviewed:
            # 다듬기는 완료됐지만 AI 검토가 끝나지 않은 경우 → AI 검토 후 대기
            _run_review_ko_step(job_id, blocks, total, translation_model,
                                extra_timings=job.get("timings"))
        else:
            # AI 검토까지 끝났거나 refine_ko 비활성 → 번역 진행
            _run_translation_steps(job_id, blocks, total, en_srt_path,
                                   translation_model=translation_model,
                                   extra_timings=job.get("timings"))

    except CancelledError:
        _do_cancel(job_id)

    except SubtitleError as e:
        log.error("job=%s 번역 재시도 실패 (%s): %s", job_id, type(e).__name__, e.detail)
        error_fields = _error_fields(e)
        resume_state = _ready_to_translate_state(job_id)
        update_job(job_id,
            status="ready_to_translate",
            progress=resume_state["progress"],
            phase=resume_state["phase"],
            subphase="failed",
            **error_fields,
        )

    except Exception as e:
        log.error("job=%s 번역 재시도 예기치 않은 오류: %s", job_id, e, exc_info=True)
        error_fields = _error_fields(e)
        resume_state = _ready_to_translate_state(job_id)
        update_job(job_id,
            status="ready_to_translate",
            progress=resume_state["progress"],
            phase=resume_state["phase"],
            subphase="failed",
            **error_fields,
        )


# ── Encode: 번인 ──────────────────────────────────────────────────────────────
def run_encode(job_id: str):
    with jobs_lock:
        job = jobs[job_id]
        input_path = job["input_path"]
    en_srt_path = UPLOADS / f"{job_id}_en.srt"
    output_path = OUTPUTS / f"{job_id}.mp4"

    def cancel_check():
        with jobs_lock:
            return jobs.get(job_id, {}).get("cancel_requested", False)

    try:
        _check_cancel(job_id)
        t0 = time.time()
        with jobs_lock:
            jobs[job_id].update({"stage_start": t0})

        encode_video(input_path, en_srt_path, output_path, cancel_check=cancel_check)

        encode_elapsed = fmt_elapsed(time.time() - t0)
        with jobs_lock:
            prev_timings = jobs[job_id].get("timings", {})
        update_job(job_id,
            status="done",
            message=f"✅ 완료! | 인코딩 {encode_elapsed}",
            progress=100,
            phase="done",
            subphase="complete",
            output=str(output_path),
            timings={**prev_timings, "encode": encode_elapsed},
        )

        # 성공 후에도 원본 영상과 한국어 자막은 보존해 다시 확인할 수 있게 한다.
        cleanup_job_files(job_id, keep_original=True, keep_ko_srt=True)

    except CancelledError:
        try: output_path.unlink(missing_ok=True)
        except Exception: pass
        _do_cancel(job_id)

    except EncodeError as e:
        log.error("job=%s 인코딩 실패: %s", job_id, e.detail)
        try: output_path.unlink(missing_ok=True)
        except Exception: pass
        update_job(job_id,
            status="ready_to_encode",
            message=f"❌ {e.user_message}",
            progress=80,
        )

    except Exception as e:
        log.error("job=%s 인코딩 예기치 않은 오류: %s", job_id, e, exc_info=True)
        try: output_path.unlink(missing_ok=True)
        except Exception: pass
        update_job(job_id,
            status="ready_to_encode",
            message="❌ 인코딩 중 예기치 않은 오류 — 다시 시도해주세요.",
            progress=80,
        )
