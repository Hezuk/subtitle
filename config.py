from pathlib import Path

BASE = Path(__file__).parent.resolve()
UPLOADS = BASE / "uploads"
OUTPUTS = BASE / "outputs"
JOBS_DIR = BASE / "jobs"

UPLOADS.mkdir(exist_ok=True)
OUTPUTS.mkdir(exist_ok=True)
JOBS_DIR.mkdir(exist_ok=True)
