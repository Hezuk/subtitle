import json, time
from pathlib import Path
from config import JOBS_DIR, UPLOADS, OUTPUTS, STALE_DAYS, ORPHAN_HOURS

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
        elif status in ("queued", "transcribing", "translating", "reviewing", "encoding"):
            job.update({"status": "error", "message": "❌ 서버 재시작으로 작업이 중단되었습니다."})
            jobs[job_id] = job
            save_job(job_id)
        elif status in ("error", "cancelled"):
            jobs[job_id] = job


# ── 파일 정리 ────────────────────────────────────────────────────────────────

def cleanup_job_files(job_id: str, keep_original: bool = False, keep_srts: bool = False):
    """job 관련 임시 파일 정리. 보존 플래그로 선택적 유지."""
    from utils.log import get_logger
    log = get_logger("cleanup")

    job = jobs.get(job_id, {})
    input_path = job.get("input_path")

    targets = []
    if not keep_original and input_path:
        targets.append(Path(input_path))
    if not keep_srts:
        targets.append(UPLOADS / f"{job_id}.srt")
        targets.append(UPLOADS / f"{job_id}_en.srt")

    for p in targets:
        try:
            if p.exists():
                p.unlink()
                log.info("삭제: %s", p)
        except Exception as e:
            log.warning("삭제 실패: %s (%s)", p, e)


def delete_job(job_id: str):
    """job 메타데이터 + 모든 관련 파일 완전 삭제."""
    cleanup_job_files(job_id)
    # 출력 파일도 삭제
    job = jobs.get(job_id, {})
    output = job.get("output")
    if output:
        try:
            Path(output).unlink(missing_ok=True)
        except Exception:
            pass
    # 메타데이터 삭제
    (JOBS_DIR / f"{job_id}.json").unlink(missing_ok=True)
    jobs.pop(job_id, None)


def cleanup_stale():
    """서버 시작 시 오래된 job과 orphan 파일 정리."""
    from utils.log import get_logger
    log = get_logger("cleanup")

    now = time.time()
    stale_cutoff = now - STALE_DAYS * 86400
    orphan_cutoff = now - ORPHAN_HOURS * 3600

    # 1. 오래된 error/cancelled job 삭제 (STALE_DAYS 초과)
    for p in list(JOBS_DIR.glob("*.json")):
        job_id = p.stem
        if job_id in jobs and jobs[job_id].get("status") in ("error", "cancelled"):
            if p.stat().st_mtime < stale_cutoff:
                log.info("오래된 error job 삭제: %s", job_id)
                delete_job(job_id)

    # 2. 복원되지 않은 job JSON 삭제 (메모리에 없음 = 복원 조건 불충족)
    for p in list(JOBS_DIR.glob("*.json")):
        job_id = p.stem
        if job_id not in jobs:
            if p.stat().st_mtime < stale_cutoff:
                log.info("복원 불가 job 메타데이터 삭제: %s", job_id)
                p.unlink(missing_ok=True)

    # 3. uploads/ orphan 파일 정리 (어떤 job에도 속하지 않는 파일)
    known_ids = set(jobs.keys())
    for p in UPLOADS.iterdir():
        if not p.is_file():
            continue
        # 파일명에서 job_id 추출 (job_id.ext 또는 job_id_en.srt)
        stem = p.stem.removesuffix("_en")
        if stem in known_ids:
            continue  # 활성 job에 속함
        if p.stat().st_mtime < orphan_cutoff:
            log.info("orphan 파일 삭제: %s", p.name)
            try:
                p.unlink()
            except Exception as e:
                log.warning("orphan 삭제 실패: %s (%s)", p.name, e)
