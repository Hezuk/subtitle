import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── 경로 ──────────────────────────────────────────────────────────────────────
BASE = Path(__file__).parent.resolve()
UPLOADS = BASE / "uploads"
OUTPUTS = BASE / "outputs"
JOBS_DIR = BASE / "jobs"

UPLOADS.mkdir(exist_ok=True)
OUTPUTS.mkdir(exist_ok=True)
JOBS_DIR.mkdir(exist_ok=True)

# ── API ───────────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-pro-preview-03-25")
GEMINI_TIMEOUT = int(os.environ.get("GEMINI_TIMEOUT", "300"))
GEMINI_RETRANSLATE_TIMEOUT = int(os.environ.get("GEMINI_RETRANSLATE_TIMEOUT", "60"))

# ── Whisper ───────────────────────────────────────────────────────────────────
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "large-v3-turbo")
WHISPER_LANGUAGE = os.environ.get("WHISPER_LANGUAGE", "ko")
WHISPER_FP16 = os.environ.get("WHISPER_FP16", "false").lower() == "true"

# ── ffmpeg ────────────────────────────────────────────────────────────────────
FFMPEG_SUBTITLE_STYLE = os.environ.get(
    "FFMPEG_SUBTITLE_STYLE",
    "FontName=Arial,FontSize=20,PrimaryColour=&HFFFFFF,"
    "OutlineColour=&H000000,Outline=2,Bold=1,Alignment=2",
)
FFMPEG_CRF = os.environ.get("FFMPEG_CRF", "18")
FFMPEG_PRESET = os.environ.get("FFMPEG_PRESET", "fast")
FFMPEG_TIMEOUT = int(os.environ.get("FFMPEG_TIMEOUT", "7200"))

# ── 업로드 제한 ───────────────────────────────────────────────────────────────
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "4096"))
ALLOWED_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".wmv", ".m4v"}

# ── 자막 제한 ─────────────────────────────────────────────────────────────────
MAX_BLOCKS = int(os.environ.get("MAX_BLOCKS", "5000"))
MAX_TEXT_LEN = int(os.environ.get("MAX_TEXT_LEN", "1000"))
MAX_REQUIREMENT_LEN = int(os.environ.get("MAX_REQUIREMENT_LEN", "500"))
MAX_SUBTITLE_CHARS = int(os.environ.get("MAX_SUBTITLE_CHARS", "42"))

# ── 파이프라인 ────────────────────────────────────────────────────────────────
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "2"))

# ── 정리 정책 ─────────────────────────────────────────────────────────────────
STALE_DAYS = int(os.environ.get("STALE_DAYS", "7"))
ORPHAN_HOURS = int(os.environ.get("ORPHAN_HOURS", "24"))
