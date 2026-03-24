"""API 엔드포인트 테스트 — FastAPI TestClient."""
import io
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def clean_jobs():
    """각 테스트 후 jobs dict 정리."""
    from store.jobs import jobs
    yield
    jobs.clear()


@pytest.fixture
def client():
    from main import app
    return TestClient(app)


# ── Upload ───────────────────────────────────────────────────────────────────

def test_upload_no_file(client):
    r = client.post("/upload")
    assert r.status_code == 422  # FastAPI validation error

def test_upload_not_video(client):
    r = client.post("/upload", files={"file": ("test.txt", b"hello", "text/plain")})
    assert r.status_code == 400

def test_upload_bad_extension(client):
    r = client.post("/upload", files={"file": ("test.exe", b"\x00" * 100, "video/mp4")})
    assert r.status_code == 400
    assert "지원하지 않는" in r.json()["detail"]

def test_upload_success(client, tmp_path):
    """정상 업로드 — pipeline은 mock 처리."""
    with patch("main.pipeline_executor") as mock_exec, \
         patch("main.UPLOADS", tmp_path), \
         patch("main.save_job"):
        mock_exec.submit = MagicMock()
        r = client.post("/upload", files={"file": ("test.mp4", b"\x00" * 100, "video/mp4")})
        assert r.status_code == 200
        data = r.json()
        assert "job_id" in data
        mock_exec.submit.assert_called_once()


# ── Status ───────────────────────────────────────────────────────────────────

def test_status_not_found(client):
    r = client.get("/status/nonexistent")
    assert r.json()["status"] == "not_found"

def test_status_found(client):
    from store.jobs import jobs
    jobs["test123"] = {"status": "done", "message": "ok", "progress": 100}
    r = client.get("/status/test123")
    assert r.json()["status"] == "done"


# ── Subtitles CRUD ───────────────────────────────────────────────────────────

def test_get_subtitles_not_found(client):
    r = client.get("/subtitles/nonexistent")
    assert r.status_code == 404

def test_save_subtitles_validation(client, tmp_path):
    """빈 블록 저장 시 400."""
    srt_path = tmp_path / "test_en.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    with patch("main.UPLOADS", tmp_path):
        r = client.post("/subtitles/test", json={"blocks": []})
        assert r.status_code == 400

def test_save_subtitles_success(client, tmp_path):
    srt_path = tmp_path / "test_en.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    blocks = [{"idx": "1", "timestamp": "00:00:00,000 --> 00:00:01,000", "text": "Updated"}]
    with patch("main.UPLOADS", tmp_path):
        r = client.post("/subtitles/test", json={"blocks": blocks})
        assert r.status_code == 200
        assert r.json()["ok"] is True

def test_save_subtitles_too_many_blocks(client, tmp_path):
    srt_path = tmp_path / "test_en.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    blocks = [
        {"idx": str(i), "timestamp": f"00:00:{i:02d},000 --> 00:00:{i:02d},500", "text": "A"}
        for i in range(5001)
    ]
    with patch("main.UPLOADS", tmp_path):
        r = client.post("/subtitles/test", json={"blocks": blocks})
        assert r.status_code == 400
        assert "너무 많습니다" in r.json()["error"]

def test_save_subtitles_text_too_long(client, tmp_path):
    srt_path = tmp_path / "test_en.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    blocks = [{"idx": "1", "timestamp": "00:00:00,000 --> 00:00:01,000", "text": "A" * 1001}]
    with patch("main.UPLOADS", tmp_path):
        r = client.post("/subtitles/test", json={"blocks": blocks})
        assert r.status_code == 400
        assert "너무 깁니다" in r.json()["error"]


# ── Retranslate ──────────────────────────────────────────────────────────────

def test_retranslate_empty_idx(client):
    r = client.post("/retranslate/test", json={"idx": "", "requirement": ""})
    assert r.status_code == 400

def test_retranslate_long_requirement(client):
    r = client.post("/retranslate/test", json={"idx": "1", "requirement": "x" * 501})
    assert r.status_code == 400
    assert "너무 깁니다" in r.json()["error"]

def test_retranslate_srt_not_found(client):
    r = client.post("/retranslate/nonexistent", json={"idx": "1", "requirement": ""})
    assert r.status_code == 404


# ── Encode ───────────────────────────────────────────────────────────────────

def test_encode_not_ready(client):
    from store.jobs import jobs
    jobs["test"] = {"status": "translating"}
    r = client.post("/encode/test")
    assert r.status_code == 400

def test_encode_success(client):
    from store.jobs import jobs
    jobs["test"] = {"status": "ready_to_encode", "input_path": "/tmp/x.mp4"}
    with patch("main.encode_executor") as mock_exec, \
         patch("main.save_job"):
        mock_exec.submit = MagicMock()
        r = client.post("/encode/test")
        assert r.status_code == 200
        assert jobs["test"]["status"] == "encoding"
        mock_exec.submit.assert_called_once()


# ── Download ─────────────────────────────────────────────────────────────────

def test_download_not_done(client):
    from store.jobs import jobs
    jobs["test"] = {"status": "encoding"}
    r = client.get("/download/test")
    assert r.status_code == 400

def test_download_file_missing(client):
    from store.jobs import jobs
    jobs["test"] = {"status": "done", "output": "/nonexistent/file.mp4"}
    r = client.get("/download/test")
    assert r.status_code == 404
