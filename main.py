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
@app.get("/status/{job_id}")
async def status(job_id: str):
    return jobs.get(job_id, {"status": "not_found"})

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
        ko_srt_path = UPLOADS / f"{job_id}.srt"
        for p in [Path(input_path), en_srt_path, ko_srt_path]:
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
        "- Keep each line under 42 characters — if a translation is longer, keep it short and concise\n"
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
