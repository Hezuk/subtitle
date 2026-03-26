"""store/jobs.py 상태 관리 테스트."""
import json
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch
from store.jobs import jobs, _lock as jobs_lock, save_job, update_job, load_job, restore_jobs


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


# ── restore_jobs: ready_to_encode 복원 조건 테스트 ──────────────────────────


def _write_job_json(jobs_dir, job_id, job_data):
    """헬퍼: jobs 디렉터리에 JSON 파일 작성."""
    (jobs_dir / f"{job_id}.json").write_text(
        json.dumps(job_data, ensure_ascii=False), encoding="utf-8"
    )


def test_restore_ready_to_encode_all_files(tmp_path):
    """input + ko.srt + en.srt 모두 존재하면 ready_to_encode 유지."""
    jobs_dir = tmp_path / "jobs"
    uploads = tmp_path / "uploads"
    jobs_dir.mkdir()
    uploads.mkdir()

    job_id = "restore_full"
    input_file = uploads / f"{job_id}.mp4"
    input_file.write_text("video")
    (uploads / f"{job_id}.srt").write_text("ko srt")
    (uploads / f"{job_id}_en.srt").write_text("en srt")

    _write_job_json(jobs_dir, job_id, {
        "status": "ready_to_encode",
        "input_path": str(input_file),
    })

    with patch("store.jobs.JOBS_DIR", jobs_dir), \
         patch("store.jobs.UPLOADS", uploads):
        jobs.pop(job_id, None)
        restore_jobs()

    assert jobs[job_id]["status"] == "ready_to_encode"
    jobs.pop(job_id, None)


def test_restore_ready_to_encode_missing_ko_srt(tmp_path):
    """한국어 SRT 없으면 error로 전환."""
    jobs_dir = tmp_path / "jobs"
    uploads = tmp_path / "uploads"
    jobs_dir.mkdir()
    uploads.mkdir()

    job_id = "restore_no_ko"
    input_file = uploads / f"{job_id}.mp4"
    input_file.write_text("video")
    # ko.srt 생성하지 않음
    (uploads / f"{job_id}_en.srt").write_text("en srt")

    _write_job_json(jobs_dir, job_id, {
        "status": "ready_to_encode",
        "input_path": str(input_file),
    })

    with patch("store.jobs.JOBS_DIR", jobs_dir), \
         patch("store.jobs.UPLOADS", uploads):
        jobs.pop(job_id, None)
        restore_jobs()

    assert jobs[job_id]["status"] == "error"
    assert "한국어 자막" in jobs[job_id]["message"]
    jobs.pop(job_id, None)


def test_restore_ready_to_encode_missing_en_srt(tmp_path):
    """영어 SRT 없으면 error로 전환."""
    jobs_dir = tmp_path / "jobs"
    uploads = tmp_path / "uploads"
    jobs_dir.mkdir()
    uploads.mkdir()

    job_id = "restore_no_en"
    input_file = uploads / f"{job_id}.mp4"
    input_file.write_text("video")
    (uploads / f"{job_id}.srt").write_text("ko srt")
    # en.srt 생성하지 않음

    _write_job_json(jobs_dir, job_id, {
        "status": "ready_to_encode",
        "input_path": str(input_file),
    })

    with patch("store.jobs.JOBS_DIR", jobs_dir), \
         patch("store.jobs.UPLOADS", uploads):
        jobs.pop(job_id, None)
        restore_jobs()

    assert jobs[job_id]["status"] == "error"
    assert "영어 자막" in jobs[job_id]["message"]
    jobs.pop(job_id, None)


def test_restore_ready_to_encode_missing_input(tmp_path):
    """원본 영상 없으면 error로 전환."""
    jobs_dir = tmp_path / "jobs"
    uploads = tmp_path / "uploads"
    jobs_dir.mkdir()
    uploads.mkdir()

    job_id = "restore_no_input"
    (uploads / f"{job_id}.srt").write_text("ko srt")
    (uploads / f"{job_id}_en.srt").write_text("en srt")

    _write_job_json(jobs_dir, job_id, {
        "status": "ready_to_encode",
        "input_path": str(uploads / f"{job_id}.mp4"),  # 존재하지 않는 경로
    })

    with patch("store.jobs.JOBS_DIR", jobs_dir), \
         patch("store.jobs.UPLOADS", uploads):
        jobs.pop(job_id, None)
        restore_jobs()

    assert jobs[job_id]["status"] == "error"
    assert "원본 영상" in jobs[job_id]["message"]
    jobs.pop(job_id, None)


def test_restore_ready_to_encode_missing_all(tmp_path):
    """세 파일 모두 없으면 error + 누락 목록 전부 표시."""
    jobs_dir = tmp_path / "jobs"
    uploads = tmp_path / "uploads"
    jobs_dir.mkdir()
    uploads.mkdir()

    job_id = "restore_none"
    _write_job_json(jobs_dir, job_id, {
        "status": "ready_to_encode",
        "input_path": str(uploads / f"{job_id}.mp4"),
    })

    with patch("store.jobs.JOBS_DIR", jobs_dir), \
         patch("store.jobs.UPLOADS", uploads):
        jobs.pop(job_id, None)
        restore_jobs()

    j = jobs[job_id]
    assert j["status"] == "error"
    assert "원본 영상" in j["message"]
    assert "한국어 자막" in j["message"]
    assert "영어 자막" in j["message"]
    jobs.pop(job_id, None)


def test_restore_ready_to_encode_error_persisted(tmp_path):
    """복원 실패 시 error 상태가 디스크에도 저장되는지 확인."""
    jobs_dir = tmp_path / "jobs"
    uploads = tmp_path / "uploads"
    jobs_dir.mkdir()
    uploads.mkdir()

    job_id = "restore_persist"
    _write_job_json(jobs_dir, job_id, {
        "status": "ready_to_encode",
        "input_path": str(uploads / f"{job_id}.mp4"),
    })

    with patch("store.jobs.JOBS_DIR", jobs_dir), \
         patch("store.jobs.UPLOADS", uploads):
        jobs.pop(job_id, None)
        restore_jobs()

    # 디스크에 error 상태가 저장되었는지
    saved = json.loads((jobs_dir / f"{job_id}.json").read_text(encoding="utf-8"))
    assert saved["status"] == "error"
    jobs.pop(job_id, None)


# ── update_job 헬퍼 테스트 ────────────────────────────────────────────────


def test_update_job(tmp_path):
    """update_job은 필드 업데이트 + 디스크 저장을 한 번에 처리."""
    with patch("store.jobs.JOBS_DIR", tmp_path):
        job_id = "update_test"
        jobs[job_id] = {"status": "queued", "message": "대기 중..."}
        update_job(job_id, status="transcribing", message="인식 중...", progress=10)

        assert jobs[job_id]["status"] == "transcribing"
        assert jobs[job_id]["progress"] == 10

        saved = json.loads((tmp_path / f"{job_id}.json").read_text(encoding="utf-8"))
        assert saved["status"] == "transcribing"
        assert saved["progress"] == 10

        jobs.pop(job_id, None)


def test_update_job_missing():
    """존재하지 않는 job에 update_job 호출해도 에러 안 남."""
    update_job("nonexistent_xyz", status="done")  # should not raise


# ── 중단된 작업 복원 메시지 테스트 ─────────────────────────────────────────


def test_restore_interrupted_with_input(tmp_path):
    """input_path가 있는 중단 작업은 재업로드 안내 메시지."""
    jobs_dir = tmp_path / "jobs"
    uploads = tmp_path / "uploads"
    jobs_dir.mkdir()
    uploads.mkdir()

    job_id = "interrupted_with"
    input_file = uploads / f"{job_id}.mp4"
    input_file.write_text("video")

    _write_job_json(jobs_dir, job_id, {
        "status": "translating",
        "input_path": str(input_file),
    })

    with patch("store.jobs.JOBS_DIR", jobs_dir), \
         patch("store.jobs.UPLOADS", uploads):
        jobs.pop(job_id, None)
        restore_jobs()

    j = jobs[job_id]
    assert j["status"] == "error"
    assert "번역" in j["message"]
    assert "다시 업로드" in j["message"]
    jobs.pop(job_id, None)


def test_restore_interrupted_without_input(tmp_path):
    """input_path가 없는 중단 작업은 유실 안내 메시지."""
    jobs_dir = tmp_path / "jobs"
    uploads = tmp_path / "uploads"
    jobs_dir.mkdir()
    uploads.mkdir()

    job_id = "interrupted_without"
    _write_job_json(jobs_dir, job_id, {
        "status": "transcribing",
        "input_path": str(uploads / f"{job_id}.mp4"),  # 존재하지 않음
    })

    with patch("store.jobs.JOBS_DIR", jobs_dir), \
         patch("store.jobs.UPLOADS", uploads):
        jobs.pop(job_id, None)
        restore_jobs()

    j = jobs[job_id]
    assert j["status"] == "error"
    assert "음성 인식" in j["message"]
    assert "유실" in j["message"]
    jobs.pop(job_id, None)


def test_restore_interrupted_queued(tmp_path):
    """queued 상태 중단도 올바른 단계명 표시."""
    jobs_dir = tmp_path / "jobs"
    uploads = tmp_path / "uploads"
    jobs_dir.mkdir()
    uploads.mkdir()

    job_id = "interrupted_queued"
    _write_job_json(jobs_dir, job_id, {
        "status": "queued",
    })

    with patch("store.jobs.JOBS_DIR", jobs_dir), \
         patch("store.jobs.UPLOADS", uploads):
        jobs.pop(job_id, None)
        restore_jobs()

    j = jobs[job_id]
    assert j["status"] == "error"
    assert "대기" in j["message"]
    assert "유실" in j["message"]  # input_path 없으므로
    jobs.pop(job_id, None)


# ── 동시성 테스트 ─────────────────────────────────────────────────────────


def test_concurrent_update_job(tmp_path):
    """여러 스레드가 동시에 update_job 호출해도 데이터 손상 없음."""
    with patch("store.jobs.JOBS_DIR", tmp_path):
        job_id = "concurrent_update"
        jobs[job_id] = {"status": "queued", "counter": 0}

        errors = []
        iterations = 200

        def increment():
            for _ in range(iterations):
                with jobs_lock:
                    current = jobs[job_id]["counter"]
                    jobs[job_id]["counter"] = current + 1
                save_job(job_id)

        threads = [threading.Thread(target=increment) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert jobs[job_id]["counter"] == iterations * 4

        # 디스크에도 최종 값이 반영됨
        saved = json.loads((tmp_path / f"{job_id}.json").read_text(encoding="utf-8"))
        assert saved["counter"] == iterations * 4

        jobs.pop(job_id, None)


def test_concurrent_cancel_and_update(tmp_path):
    """취소 플래그 설정과 상태 업데이트가 동시에 일어나도 안전."""
    with patch("store.jobs.JOBS_DIR", tmp_path):
        job_id = "concurrent_cancel"
        jobs[job_id] = {"status": "transcribing", "message": "진행 중..."}

        def set_cancel():
            with jobs_lock:
                jobs[job_id]["cancel_requested"] = True

        def do_updates():
            for i in range(50):
                update_job(job_id, progress=i)

        t1 = threading.Thread(target=set_cancel)
        t2 = threading.Thread(target=do_updates)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # cancel_requested가 다른 업데이트에 의해 사라지지 않아야 함
        assert jobs[job_id]["cancel_requested"] is True
        jobs.pop(job_id, None)


def test_atomic_save_no_corruption(tmp_path):
    """save_job의 원자적 쓰기: 동시 저장 후 JSON이 항상 유효."""
    with patch("store.jobs.JOBS_DIR", tmp_path):
        job_id = "atomic_save"
        jobs[job_id] = {"status": "encoding", "data": "x" * 1000}

        def writer(value):
            for _ in range(100):
                update_job(job_id, data=value * 500)

        threads = [
            threading.Thread(target=writer, args=("A",)),
            threading.Thread(target=writer, args=("B",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 디스크 파일이 항상 유효한 JSON
        content = (tmp_path / f"{job_id}.json").read_text(encoding="utf-8")
        saved = json.loads(content)  # 파싱 실패 시 테스트 실패
        assert saved["data"] in ("A" * 500, "B" * 500)

        jobs.pop(job_id, None)
