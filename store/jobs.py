import json, tempfile, time, threading
from pathlib import Path
from config import JOBS_DIR, UPLOADS, OUTPUTS, STALE_DAYS, ORPHAN_HOURS

jobs: dict[str, dict] = {}
_lock = threading.RLock()


def save_job(job_id: str):
    """메모리 → 디스크 저장 (jobs/{job_id}.json) — 원자적 쓰기."""
    with _lock:
        job = jobs.get(job_id)
        if not job:
            return
        data = json.dumps(job, ensure_ascii=False, indent=2).encode("utf-8")
    # 같은 디렉터리에 임시 파일 → rename (같은 파일시스템이므로 원자적)
    dest = JOBS_DIR / f"{job_id}.json"
    fd, tmp = tempfile.mkstemp(dir=JOBS_DIR, suffix=".tmp")
    try:
        with open(fd, "wb") as f:
            f.write(data)
        Path(tmp).replace(dest)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def update_job(job_id: str, **fields):
    """job 필드 업데이트 + 디스크 저장을 한 번에 처리."""
    with _lock:
        job = jobs.get(job_id)
        if not job:
            return
        job.update(fields)
    save_job(job_id)


def load_job(job_id: str) -> dict | None:
    """메모리 → 디스크 순서로 job 조회"""
    with _lock:
        if job_id in jobs:
            return jobs[job_id]
    path = JOBS_DIR / f"{job_id}.json"
    if path.exists():
        job = json.loads(path.read_text(encoding="utf-8"))
        with _lock:
            jobs[job_id] = job
        return job
    return None


def restore_jobs():
    """서버 시작 시 디스크에서 job 메타데이터 복원"""
    for p in JOBS_DIR.glob("*.json"):
        job_id = p.stem
        with _lock:
            if job_id in jobs:
                continue
        try:
            job = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        status = job.get("status", "")
        if status == "done":
            if job.get("output") and Path(job["output"]).exists():
                with _lock:
                    jobs[job_id] = job
        elif status == "ready_to_encode":
            input_ok = job.get("input_path") and Path(job["input_path"]).exists()
            ko_srt_ok = (UPLOADS / f"{job_id}.srt").exists()
            en_srt_ok = (UPLOADS / f"{job_id}_en.srt").exists()
            if input_ok and ko_srt_ok and en_srt_ok:
                with _lock:
                    jobs[job_id] = job
            else:
                missing = []
                if not input_ok:
                    missing.append("원본 영상")
                if not ko_srt_ok:
                    missing.append("한국어 자막")
                if not en_srt_ok:
                    missing.append("영어 자막")
                job.update({
                    "status": "error",
                    "message": f"❌ 복원에 필요한 임시 파일이 부족합니다: {', '.join(missing)}",
                })
                with _lock:
                    jobs[job_id] = job
                save_job(job_id)
        elif status == "ready_to_translate":
            input_ok = job.get("input_path") and Path(job["input_path"]).exists()
            ko_srt_ok = (UPLOADS / f"{job_id}.srt").exists()
            if input_ok and ko_srt_ok:
                with _lock:
                    jobs[job_id] = job
            else:
                job.update({
                    "status": "error",
                    "message": "❌ 복원에 필요한 파일이 부족합니다.",
                })
                with _lock:
                    jobs[job_id] = job
                save_job(job_id)
        elif status in ("queued", "transcribing", "translating", "reviewing", "encoding"):
            input_path = job.get("input_path")
            has_input = input_path and Path(input_path).exists()
            phase_label = {
                "queued": "대기",
                "transcribing": "음성 인식",
                "translating": "번역",
                "reviewing": "검토",
                "encoding": "인코딩",
            }.get(status, status)
            if has_input:
                msg = f"❌ 서버 재시작으로 {phase_label} 중 작업이 중단되었습니다. 다시 업로드해주세요."
            else:
                msg = f"❌ 서버 재시작으로 {phase_label} 중 작업이 중단되었으며 원본 파일도 유실되었습니다."
            job.update({"status": "error", "message": msg})
            with _lock:
                jobs[job_id] = job
            save_job(job_id)
        elif status in ("error", "cancelled"):
            with _lock:
                jobs[job_id] = job


# ── 파일 정리 ────────────────────────────────────────────────────────────────

def cleanup_job_files(job_id: str, keep_original: bool = False, keep_srts: bool = False):
    """job 관련 임시 파일 정리. 보존 플래그로 선택적 유지."""
    from utils.log import get_logger
    log = get_logger("cleanup")

    with _lock:
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
    with _lock:
        job = jobs.get(job_id, {})
        output = job.get("output")
    if output:
        try:
            Path(output).unlink(missing_ok=True)
        except Exception:
            pass
    # 메타데이터 삭제
    (JOBS_DIR / f"{job_id}.json").unlink(missing_ok=True)
    with _lock:
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
        with _lock:
            status = jobs.get(job_id, {}).get("status")
        if status in ("error", "cancelled"):
            if p.stat().st_mtime < stale_cutoff:
                log.info("오래된 error job 삭제: %s", job_id)
                delete_job(job_id)

    # 2. 복원되지 않은 job JSON 삭제 (메모리에 없음 = 복원 조건 불충족)
    for p in list(JOBS_DIR.glob("*.json")):
        job_id = p.stem
        with _lock:
            in_memory = job_id in jobs
        if not in_memory:
            if p.stat().st_mtime < stale_cutoff:
                log.info("복원 불가 job 메타데이터 삭제: %s", job_id)
                p.unlink(missing_ok=True)

    # 3. uploads/ orphan 파일 정리 (어떤 job에도 속하지 않는 파일)
    with _lock:
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
