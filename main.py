import uuid, os, re, subprocess, shutil
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
@app.get("/status/{job_id}")
async def status(job_id: str):
    return jobs.get(job_id, {"status": "not_found"})

# ── Subtitle preview (WebVTT) ──────────────────────────────────────────────────
@app.get("/subtitle/{job_id}")
async def subtitle(job_id: str):
    srt_path = UPLOADS / f"{job_id}_en.srt"
    if not srt_path.exists():
        return JSONResponse({"error": "Not found"}, status_code=404)
    content = srt_path.read_text(encoding="utf-8")
    # SRT → WebVTT 변환 (콤마를 점으로)
    vtt = "WEBVTT\n\n" + re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", content)
    return Response(content=vtt, media_type="text/vtt")

# ── Encode (번인 시작) ──────────────────────────────────────────────────────────
@app.post("/encode/{job_id}")
async def encode(job_id: str):
    job = jobs.get(job_id, {})
    if job.get("status") != "ready_to_encode":
        return JSONResponse({"error": "Not ready"}, status_code=400)
    jobs[job_id].update({"status": "encoding", "message": "🎬 자막 인코딩 중...", "progress": 82})
    executor.submit(run_encode, job_id)
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
        jobs[job_id].update({"status": "transcribing", "message": "🎤 음성 인식 중... (시간이 걸릴 수 있습니다)"})

        global whisper_model
        if whisper_model is None:
            whisper_model = whisper.load_model("large-v3-turbo")

        result = whisper_model.transcribe(input_path, fp16=False, language="ko")
        detected_lang = result.get("language", "unknown")

        srt_content = segments_to_srt(result["segments"])
        srt_path.write_text(srt_content, encoding="utf-8")

        # Step 2: Translate
        jobs[job_id].update({"status": "translating", "message": f"🌍 영어로 번역 중... (감지된 언어: {detected_lang})"})

        blocks = parse_srt(srt_content)
        total  = len(blocks)
        all_translated = translate_with_gemini(blocks)
        jobs[job_id].update({"message": f"🌍 번역 완료 ({total}/{total})", "progress": 75})

        en_lines = []
        for b in blocks:
            text = all_translated.get(b["idx"], b["text"])
            en_lines.append(f"{b['idx']}\n{b['timestamp']}\n{text}\n")
        en_srt_path.write_text("\n".join(en_lines), encoding="utf-8")

        # 번역 완료 — 사용자 확인 대기
        jobs[job_id].update({
            "status": "ready_to_encode",
            "message": "✅ 번역 완료! 자막을 확인한 후 번인을 시작하세요.",
            "progress": 80,
            "input_path": input_path,
            "detected_lang": detected_lang,
        })

    except Exception as e:
        jobs[job_id].update({"status": "error", "message": f"❌ 오류: {e}"})
        for p in [Path(input_path), srt_path, en_srt_path]:
            try: p.unlink(missing_ok=True)
            except Exception: pass

    finally:
        try: srt_path.unlink(missing_ok=True)
        except Exception: pass

# ── Encode: 번인 ────────────────────────────────────────────────────────────────
def run_encode(job_id: str):
    job         = jobs[job_id]
    input_path  = job["input_path"]
    en_srt_path = UPLOADS / f"{job_id}_en.srt"
    output_path = OUTPUTS / f"{job_id}.mp4"

    try:
        srt_str = str(en_srt_path).replace("\\", "/").replace(":", "\\:")
        style = "FontName=Arial,FontSize=20,PrimaryColour=&HFFFFFF,OutlineColour=&H000000,Outline=2,Bold=1,Alignment=2"

        r = subprocess.run(
            [
                "ffmpeg", "-i", input_path,
                "-vf", f"subtitles={srt_str}:force_style='{style}'",
                "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                "-c:a", "copy",
                str(output_path), "-y",
            ],
            capture_output=True, timeout=7200,
        )
        if not output_path.exists():
            raise RuntimeError(f"ffmpeg failed:\n{r.stderr.decode()}")

        jobs[job_id].update({
            "status": "done",
            "message": "✅ 완료!",
            "progress": 100,
            "output": str(output_path),
        })

    except Exception as e:
        jobs[job_id].update({"status": "error", "message": f"❌ 오류: {e}"})

    finally:
        for p in [Path(input_path), en_srt_path]:
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
    blocks  = []
    pattern = r"(\d+)\n(\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3})\n([\s\S]*?)(?=\n\n\d+\n|\Z)"
    for idx, ts, text in re.findall(pattern, content.strip()):
        blocks.append({"idx": idx, "timestamp": ts, "text": text.strip()})
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
        "- Maintain consistent tone, terminology, and style throughout all segments\n"
        "- Ensure each segment flows coherently with the surrounding context\n"
        "- Keep translations concise to fit subtitle timing\n"
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
        result[b["idx"]] = re.sub(r'\s*---\s*', '', text).strip()
    return result
