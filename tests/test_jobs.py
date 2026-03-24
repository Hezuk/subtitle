"""store/jobs.py 상태 관리 테스트."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from store.jobs import jobs, save_job, load_job


def test_save_and_load(tmp_path):
    """save_job → load_job 라운드트립."""
    with patch("store.jobs.JOBS_DIR", tmp_path):
        job_id = "test_abc123"
        jobs[job_id] = {"status": "done", "message": "ok", "output": "/tmp/out.mp4"}
        save_job(job_id)

        # 파일이 생성되었는지 확인
        f = tmp_path / f"{job_id}.json"
        assert f.exists()
        data = json.loads(f.read_text(encoding="utf-8"))
        assert data["status"] == "done"

        # 메모리에서 제거 후 디스크에서 복원
        del jobs[job_id]
        result = load_job(job_id)
        assert result is not None
        assert result["status"] == "done"
        assert job_id in jobs  # 캐시에 올라감

        # cleanup
        del jobs[job_id]


def test_load_missing(tmp_path):
    with patch("store.jobs.JOBS_DIR", tmp_path):
        assert load_job("nonexistent_id") is None


def test_save_empty_job():
    """존재하지 않는 job_id로 save_job 호출해도 에러 안 남."""
    save_job("nonexistent_job_xyz")  # should not raise
