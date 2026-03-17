import uuid, os, re, subprocess, shutil, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
import aiofiles
import requests
import whisper
from dotenv import load_dotenv

load_dotenv()

# ── Directories ────────────────────────────────────────────────────────────────
BASE = Path(__file__).parent.resolve()
UPLOADS = BASE / "uploads"
OUTPUTS = BASE / "outputs"
UPLOADS.mkdir(exist_ok=True)
OUTPUTS.mkdir(exist_ok=True)

# ── Globals ────────────────────────────────────────────────────────────────────
jobs: dict[str, dict] = {}
executor = ThreadPoolExecutor(max_workers=1)
whisper_model = None

app = FastAPI()

# ── Frontend ───────────────────────────────────────────────────────────────────
@app.get("/")
async def index():
    return FileResponse(BASE / "index.html")

@app.get("/player")
async def player():
    return FileResponse(BASE / "player.html")

@app.get("/editor")
async def editor():
    return FileResponse(BASE / "editor.html")

# ── Upload ─────────────────────────────────────────────────────────────────────
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

    jobs[job_id] = {"status": "queued", "message": "대기 중..."}
    executor.submit(run_pipeline, job_id, str(input_path))
    return {"job_id": job_id}

# ── Status ─────────────────────────────────────────────────────────────────────
def fmt_elapsed(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}초"
    return f"{s // 60}분 {s % 60}초"

@app.get("/status/{job_id}")
async def status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return {"status": "not_found"}
    result = dict(job)
    # 진행 중인 단계의 실시간 경과 시간
    if "stage_start" in job and job["status"] not in ("done", "error", "ready_to_encode"):
        result["elapsed"] = fmt_elapsed(time.time() - job["stage_start"])
    return result

# ── Subtitle preview (WebVTT) ──────────────────────────────────────────────────
def srt_to_vtt(path: Path) -> str:
    content = path.read_text(encoding="utf-8")
    return "WEBVTT\n\n" + re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", content)

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
    en_blocks  = parse_srt(en_path.read_text(encoding="utf-8"))
    en_map = {b["idx"]: b["text"] for b in en_blocks}
    lines = ["WEBVTT\n"]
    for b in ko_blocks:
        ts = re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", b["timestamp"])
        en_text = en_map.get(b["idx"], "")
        lines.append(f"\n{b['idx']}\n{ts}\n{en_text}\n{b['text']}")
    return Response(content="\n".join(lines), media_type="text/vtt")

# ── Subtitles JSON (편집용) ────────────────────────────────────────────────────
def _load_blocks(path: Path):
    if not path.exists():
        return None
    return parse_srt(path.read_text(encoding="utf-8"))

def _save_blocks(path: Path, blocks: list):
    lines = [f"{b['idx']}\n{b['timestamp']}\n{b['text']}\n" for b in blocks]
    path.write_text("\n".join(lines), encoding="utf-8")

@app.get("/subtitles/{job_id}")
async def get_subtitles(job_id: str):
    blocks = _load_blocks(UPLOADS / f"{job_id}_en.srt")
    if blocks is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return {"blocks": blocks}

@app.post("/subtitles/{job_id}")
async def save_subtitles(job_id: str, payload: dict):
    path = UPLOADS / f"{job_id}_en.srt"
    if not path.exists():
        return JSONResponse({"error": "Not found"}, status_code=404)
    blocks = payload.get("blocks", [])
    _save_blocks(path, blocks)
    return {"ok": True, "count": len(blocks)}

@app.get("/subtitles_ko/{job_id}")
async def get_subtitles_ko(job_id: str):
    blocks = _load_blocks(UPLOADS / f"{job_id}.srt")
    if blocks is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return {"blocks": blocks}

@app.post("/subtitles_ko/{job_id}")
async def save_subtitles_ko(job_id: str, payload: dict):
    path = UPLOADS / f"{job_id}.srt"
    if not path.exists():
        return JSONResponse({"error": "Not found"}, status_code=404)
    blocks = payload.get("blocks", [])
    _save_blocks(path, blocks)
    return {"ok": True, "count": len(blocks)}

# ── Encode (번인 시작) ──────────────────────────────────────────────────────────
@app.post("/encode/{job_id}")
async def encode(job_id: str):
    job = jobs.get(job_id, {})
    if job.get("status") != "ready_to_encode":
        return JSONResponse({"error": "Not ready"}, status_code=400)
    jobs[job_id].update({"status": "encoding", "message": "🎬 자막 인코딩 중...", "progress": 82})
    executor.submit(run_encode, job_id)
    return {"ok": True}

# ── Retranslate (개별 자막 재번역) ───────────────────────────────────────────────
@app.post("/retranslate/{job_id}")
async def retranslate(job_id: str, payload: dict):
    idx         = str(payload.get("idx", ""))
    requirement = payload.get("requirement", "").strip()

    ko_blocks = _load_blocks(UPLOADS / f"{job_id}.srt")
    if ko_blocks is None:
        return JSONResponse({"error": "Korean SRT not found"}, status_code=404)
    ko_block = next((b for b in ko_blocks if b["idx"] == idx), None)
    if not ko_block:
        return JSONResponse({"error": "Block not found"}, status_code=404)

    en_blocks   = _load_blocks(UPLOADS / f"{job_id}_en.srt") or []
    en_block    = next((b for b in en_blocks if b["idx"] == idx), None)
    current_en  = en_block["text"] if en_block else ""

    text = retranslate_with_gemini(ko_block["text"], current_en, requirement)
    return {"text": text}


def retranslate_with_gemini(ko_text: str, current_en: str, requirement: str) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY가 설정되지 않았습니다.")

    prompt = (
        "Translate the following Korean subtitle segment to English.\n"
        f"Korean: {ko_text}\n"
        f"Current translation: {current_en}\n"
    )
    if requirement:
        prompt += f"Additional requirement: {requirement}\n"
    prompt += (
        "Rules:\n"
        "- Use natural, sophisticated English — avoid literal word-for-word translation\n"
        "- Must be a complete sentence or complete clause — no fragments\n"
        "- Return ONLY the translated text, nothing else\n"
    )

    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash-preview:generateContent?key={api_key}",
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()


# ── Dub (더빙 + 번인 시작) ──────────────────────────────────────────────────────
@app.post("/dub/{job_id}")
async def dub(job_id: str):
    job = jobs.get(job_id, {})
    if job.get("status") != "ready_to_encode":
        return JSONResponse({"error": "Not ready"}, status_code=400)
    jobs[job_id].update({"status": "dubbing", "message": "🎙️ 더빙 오디오 생성 중...", "progress": 80})
    executor.submit(run_dub_and_encode, job_id)
    return {"ok": True}

# ── Download ───────────────────────────────────────────────────────────────────
@app.get("/download/{job_id}")
async def download(job_id: str):
    job = jobs.get(job_id, {})
    if job.get("status") != "done":
        return JSONResponse({"error": "Not ready"}, status_code=400)
    return FileResponse(
        job["output"],
        filename="subtitled_english.mp4",
        media_type="video/mp4",
    )

# ── Pipeline: 음성인식 + 번역 ────────────────────────────────────────────────────
def run_pipeline(job_id: str, input_path: str):
    srt_path    = UPLOADS / f"{job_id}.srt"
    en_srt_path = UPLOADS / f"{job_id}_en.srt"

    try:
        # Step 1: Transcribe
        t0 = time.time()
        jobs[job_id].update({"status": "transcribing", "message": "🎤 음성 인식 중...", "stage_start": t0})

        global whisper_model
        if whisper_model is None:
            whisper_model = whisper.load_model("large-v3-turbo")

        result = whisper_model.transcribe(input_path, fp16=False, language="ko")
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

        blocks = parse_srt(srt_content)
        total  = len(blocks)

        all_translated = translate_with_gemini(blocks)
        translate_elapsed = fmt_elapsed(time.time() - t1)
        jobs[job_id].update({"message": f"🌍 번역 완료 ({total}/{total}) | {translate_elapsed}", "progress": 70})

        # current_en: idx → 현재 영어 텍스트
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

            # 검토 결과 반영 (RETRANSLATE 제외)
            for idx, text in reviewed.items():
                if text != "__RETRANSLATE__":
                    current_en[idx] = text

            bad_idxs = {idx for idx, text in reviewed.items() if text == "__RETRANSLATE__"}

            if not bad_idxs:
                break  # 불량 없음 — 완료

            if attempt < MAX_RETRIES - 1:
                jobs[job_id].update({
                    "message": f"🔄 불량 {len(bad_idxs)}개 재번역 중... ({attempt+2}/{MAX_RETRIES}회차)",
                    "progress": 74,
                    "stage_start": time.time(),
                })
                bad_ko_blocks = [b for b in blocks if b["idx"] in bad_idxs]
                retranslated  = translate_with_gemini(bad_ko_blocks)
                for idx, text in retranslated.items():
                    current_en[idx] = text
            # 마지막 시도에서도 불량이면 현재 텍스트 유지

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

    except Exception as e:
        jobs[job_id].update({"status": "error", "message": f"❌ 오류: {e}"})
        for p in [Path(input_path), srt_path, en_srt_path]:
            try: p.unlink(missing_ok=True)
            except Exception: pass

# ── Encode: 번인 ────────────────────────────────────────────────────────────────
def run_encode(job_id: str):
    job         = jobs[job_id]
    input_path  = job["input_path"]
    en_srt_path = UPLOADS / f"{job_id}_en.srt"
    output_path = OUTPUTS / f"{job_id}.mp4"

    try:
        t0 = time.time()
        jobs[job_id].update({"stage_start": t0})

        srt_str = str(en_srt_path).replace("\\", "/").replace(":", "\\:")
        style = "FontName=Arial,FontSize=20,PrimaryColour=&HFFFFFF,OutlineColour=&H000000,Outline=2,Bold=1,Alignment=2"

        r = subprocess.run(
            [
                "ffmpeg", "-i", input_path,
                "-vf", f"subtitles={srt_str}:force_style='{style}'",
                "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                "-c:a", "copy",
                "-movflags", "+faststart",
                str(output_path), "-y",
            ],
            capture_output=True, timeout=7200,
        )
        if r.returncode != 0 or not output_path.exists():
            raise RuntimeError(f"ffmpeg failed:\n{r.stderr.decode()}")

        encode_elapsed = fmt_elapsed(time.time() - t0)
        prev_timings = jobs[job_id].get("timings", {})
        jobs[job_id].update({
            "status": "done",
            "message": f"✅ 완료! | 인코딩 {encode_elapsed}",
            "progress": 100,
            "output": str(output_path),
            "timings": {**prev_timings, "encode": encode_elapsed},
        })

    except Exception as e:
        jobs[job_id].update({"status": "error", "message": f"❌ 오류: {e}"})

    finally:
        ko_srt_path = UPLOADS / f"{job_id}.srt"
        for p in [Path(input_path), en_srt_path, ko_srt_path]:
            try: p.unlink(missing_ok=True)
            except Exception: pass


# ── Dub + Encode: 더빙 생성 후 번인 ─────────────────────────────────────────────
def run_dub_and_encode(job_id: str):
    job          = jobs[job_id]
    input_path   = job["input_path"]
    en_srt_path  = UPLOADS / f"{job_id}_en.srt"
    dub_wav_path = UPLOADS / f"{job_id}_dub.wav"
    output_path  = OUTPUTS / f"{job_id}.mp4"
    tada_python  = BASE / "tada_env" / "bin" / "python"
    dub_script   = BASE / "dub_worker.py"

    try:
        # Step 1: 더빙 WAV 생성 (dub_worker.py subprocess)
        t_dub = time.time()
        jobs[job_id].update({"stage_start": t_dub})

        proc = subprocess.Popen(
            [str(tada_python), str(dub_script), job_id],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("PROGRESS:"):
                parts = line.split(":", 2)
                pct   = 80 + int(parts[1]) * 0.1  # 80~90% 구간
                msg   = parts[2] if len(parts) > 2 else ""
                elapsed = fmt_elapsed(time.time() - t_dub)
                jobs[job_id].update({"message": f"🎙️ {msg} | {elapsed}", "progress": pct, "stage_start": t_dub})
            elif line.startswith("WARNING:"):
                pass  # 개별 실패는 무시
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError("더빙 생성 실패 (dub_worker 오류)")

        dub_elapsed = fmt_elapsed(time.time() - t_dub)

        # Step 2: ffmpeg — 자막 번인 + 더빙 오디오 믹싱
        t_enc = time.time()
        jobs[job_id].update({
            "status": "encoding",
            "message": f"🎬 자막 번인 및 더빙 합성 중... | 더빙 {dub_elapsed}",
            "progress": 90,
            "stage_start": t_enc,
        })

        srt_str = str(en_srt_path).replace("\\", "/").replace(":", "\\:")
        style   = "FontName=Arial,FontSize=20,PrimaryColour=&HFFFFFF,OutlineColour=&H000000,Outline=2,Bold=1,Alignment=2"
        filter_complex = (
            f"[0:v]subtitles={srt_str}:force_style='{style}'[vout];"
            f"[1:a]apad[aout]"
        )
        r = subprocess.run(
            [
                "ffmpeg", "-i", input_path, "-i", str(dub_wav_path),
                "-filter_complex", filter_complex,
                "-map", "[vout]", "-map", "[aout]",
                "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                "-c:a", "aac", "-b:a", "192k",
                "-shortest",
                "-movflags", "+faststart",
                str(output_path), "-y",
            ],
            capture_output=True, timeout=7200,
        )
        if r.returncode != 0 or not output_path.exists():
            raise RuntimeError(f"ffmpeg 실패:\n{r.stderr.decode()}")

        encode_elapsed = fmt_elapsed(time.time() - t_enc)
        prev_timings = jobs[job_id].get("timings", {})
        jobs[job_id].update({
            "status":   "done",
            "message":  f"✅ 완료! | 더빙 {dub_elapsed} / 인코딩 {encode_elapsed}",
            "progress": 100,
            "output":   str(output_path),
            "timings":  {**prev_timings, "dub": dub_elapsed, "encode": encode_elapsed},
        })

    except Exception as e:
        jobs[job_id].update({"status": "error", "message": f"❌ 오류: {e}"})

    finally:
        ko_srt_path = UPLOADS / f"{job_id}.srt"
        for p in [Path(input_path), en_srt_path, ko_srt_path, dub_wav_path]:
            try: p.unlink(missing_ok=True)
            except Exception: pass


# ── Helpers ────────────────────────────────────────────────────────────────────
def seconds_to_srt_time(s: float) -> str:
    ms = int(round((s % 1) * 1000))
    s = int(s)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02}:{m:02}:{sec:02},{ms:03}"


def segments_to_srt(segments) -> str:
    lines = []
    for i, seg in enumerate(segments, 1):
        start = seconds_to_srt_time(seg["start"])
        end   = seconds_to_srt_time(seg["end"])
        text  = seg["text"].strip()
        lines.append(f"{i}\n{start} --> {end}\n{text}\n")
    return "\n".join(lines)


def parse_srt(content: str) -> list[dict]:
    ts_pattern = re.compile(r'\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}')
    blocks = []
    for raw in re.split(r'\n{2,}', content.strip()):
        lines = raw.strip().splitlines()
        if len(lines) < 3:
            continue
        idx = lines[0].strip()
        ts  = lines[1].strip()
        if not idx.isdigit() or not ts_pattern.match(ts):
            continue
        text = '\n'.join(lines[2:]).strip()
        blocks.append({"idx": idx, "timestamp": ts, "text": text})
    return blocks


def translate_with_gemini(blocks: list[dict]) -> dict[str, str]:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY가 설정되지 않았습니다. .env 파일을 확인해주세요.")

    texts  = "\n---\n".join(f"[{b['idx']}] {b['text']}" for b in blocks)
    prompt = (
        "Translate the following subtitle segments to English.\n"
        "Rules:\n"
        "- Each segment is marked with [number]\n"
        "- Use natural, sophisticated English — avoid literal word-for-word translation\n"
        "- Each subtitle must be a complete sentence or complete clause — no fragments or mid-sentence breaks\n"
        "- Maintain consistent tone, terminology, and style throughout all segments\n"
        "- Ensure each segment flows coherently with the surrounding context\n"
        "- Return ONLY the translated segments with the same [number] markers\n\n"
        f"{texts}"
    )

    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash-preview:generateContent?key={api_key}",
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=300,
    )
    resp.raise_for_status()
    response = resp.json()["candidates"][0]["content"]["parts"][0]["text"]

    result: dict[str, str] = {}
    for b in blocks:
        m    = re.search(rf"\[{b['idx']}\]\s*(.*?)(?=\n\[|\Z)", response, re.DOTALL)
        text = m.group(1).strip() if m else b["text"]
        text = re.sub(r'\s*---\s*', '', text).strip()
        result[b["idx"]] = wrap_subtitle(text)
    return result


def review_with_gemini(blocks: list[dict]) -> dict[str, str]:
    """번역된 자막을 검토: 미번역 잔존 여부, 비문, 용어 일관성 확인 후 교정."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY가 설정되지 않았습니다.")

    texts  = "\n---\n".join(f"[{b['idx']}] {b['text']}" for b in blocks)
    prompt = (
        "You are a subtitle QA editor. Review and correct the following English subtitle segments.\n"
        "Fix any of these issues you find:\n"
        "1. Untranslated text: any non-English words must be translated to English\n"
        "2. Grammar: fix fragments, broken sentences, or unnatural phrasing so each segment reads as natural English\n"
        "3. Consistency: ensure the same terms, names, and style are used throughout all segments\n"
        "Rules:\n"
        "- Use natural, sophisticated English — avoid literal word-for-word translation\n"
        "- Each subtitle must be a complete sentence or complete clause — no fragments or mid-sentence breaks\n"
        "- Maintain consistent tone, terminology, and style throughout all segments\n"
        "- Ensure each segment flows coherently with the surrounding context\n"
        "- If a segment is too flawed to fix (wrong content, missing translation, or incomprehensible), "
        "output [RETRANSLATE] as its text so it can be retranslated from the source\n"
        "- Return ONLY the corrected segments with the same [number] markers, one per line\n"
        "- Do not add explanations, comments, or extra text\n\n"
        f"{texts}"
    )

    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash-preview:generateContent?key={api_key}",
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=300,
    )
    resp.raise_for_status()
    response = resp.json()["candidates"][0]["content"]["parts"][0]["text"]

    result: dict[str, str] = {}
    for b in blocks:
        m    = re.search(rf"\[{b['idx']}\]\s*(.*?)(?=\n\[|\Z)", response, re.DOTALL)
        text = m.group(1).strip() if m else b["text"]
        text = re.sub(r'\s*---\s*', '', text).strip()
        if re.search(r'\[RETRANSLATE\]|^RETRANSLATE$', text, re.IGNORECASE):
            result[b["idx"]] = "__RETRANSLATE__"
        else:
            result[b["idx"]] = wrap_subtitle(text)
    return result


def wrap_subtitle(text: str, max_chars: int = 42) -> str:
    """한 줄이 max_chars를 초과하면 중간 공백에서 두 줄로 분할."""
    # 이미 줄바꿈이 있으면 각 줄을 재처리
    lines = text.splitlines()
    wrapped = []
    for line in lines:
        line = line.strip()
        if len(line) <= max_chars:
            wrapped.append(line)
        else:
            # 중간 지점에서 가장 가까운 공백 찾기
            mid = len(line) // 2
            left  = line.rfind(' ', 0, mid + 1)
            right = line.find(' ', mid)
            if left == -1 and right == -1:
                wrapped.append(line)  # 공백 없으면 그대로
            elif left == -1:
                split_at = right
            elif right == -1:
                split_at = left
            else:
                split_at = left if (mid - left) <= (right - mid) else right
            wrapped.append(line[:split_at].strip())
            wrapped.append(line[split_at:].strip())
    return "\n".join(wrapped)
