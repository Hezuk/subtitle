import json
from pathlib import Path
from config import JOBS_DIR, UPLOADS

jobs: dict[str, dict] = {}


def save_job(job_id: str):
    """메모리 → 디스크 저장 (jobs/{job_id}.json)"""
    job = jobs.get(job_id)
    if not job:
        return
    (JOBS_DIR / f"{job_id}.json").write_text(
        json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_job(job_id: str) -> dict | None:
    """메모리 → 디스크 순서로 job 조회"""
    if job_id in jobs:
        return jobs[job_id]
    path = JOBS_DIR / f"{job_id}.json"
    if path.exists():
        job = json.loads(path.read_text(encoding="utf-8"))
        jobs[job_id] = job
        return job
    return None


def restore_jobs():
    """서버 시작 시 디스크에서 job 메타데이터 복원"""
    for p in JOBS_DIR.glob("*.json"):
        job_id = p.stem
        if job_id in jobs:
            continue
        try:
            job = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        status = job.get("status", "")
        if status == "done":
            if job.get("output") and Path(job["output"]).exists():
                jobs[job_id] = job
        elif status == "ready_to_encode":
            input_ok = job.get("input_path") and Path(job["input_path"]).exists()
            srt_ok = (UPLOADS / f"{job_id}_en.srt").exists()
            if input_ok and srt_ok:
                jobs[job_id] = job
        elif status in ("queued", "transcribing", "translating", "encoding"):
            job.update({"status": "error", "message": "❌ 서버 재시작으로 작업이 중단되었습니다."})
            jobs[job_id] = job
            save_job(job_id)
        elif status == "error":
            jobs[job_id] = job
