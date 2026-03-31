"""API 엔드포인트 테스트 — FastAPI TestClient."""
import io
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient
from utils.errors import TranslationError


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

def test_upload_invalid_translation_model(client):
    r = client.post(
        "/upload",
        data={"translation_model": "unknown"},
        files={"file": ("test.mp4", b"\x00" * 100, "video/mp4")},
    )
    assert r.status_code == 400
    assert "지원하지 않는 번역 모델" in r.json()["detail"]

def test_upload_invalid_whisper_model(client):
    r = client.post(
        "/upload",
        data={"whisper_model": "unknown"},
        files={"file": ("test.mp4", b"\x00" * 100, "video/mp4")},
    )
    assert r.status_code == 400
    assert "지원하지 않는 Whisper 모델" in r.json()["detail"]

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
        assert data["translation_model"] == "claude"
        assert data["whisper_model"] == "large-v3-turbo"
        mock_exec.submit.assert_called_once()

def test_upload_selected_translation_model_saved(client, tmp_path):
    from store.jobs import jobs

    with patch("main.pipeline_executor") as mock_exec, \
         patch("main.UPLOADS", tmp_path), \
         patch("main.save_job"):
        mock_exec.submit = MagicMock()
        r = client.post(
            "/upload",
            data={"translation_model": "gemini"},
            files={"file": ("test.mp4", b"\x00" * 100, "video/mp4")},
        )
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        assert jobs[job_id]["translation_model"] == "gemini"

def test_upload_selected_whisper_model_saved(client, tmp_path):
    from store.jobs import jobs

    with patch("main.pipeline_executor") as mock_exec, \
         patch("main.UPLOADS", tmp_path), \
         patch("main.save_job"):
        mock_exec.submit = MagicMock()
        r = client.post(
            "/upload",
            data={"whisper_model": "turbo"},
            files={"file": ("test.mp4", b"\x00" * 100, "video/mp4")},
        )
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        assert jobs[job_id]["whisper_model"] == "turbo"

def test_upload_with_existing_ko_subtitle_skips_transcription(client, tmp_path):
    from store.jobs import jobs

    subtitle_content = (
        "1\n"
        "00:00:00,000 --> 00:00:01,000\n"
        "안녕하세요\n\n"
        "2\n"
        "00:00:01,000 --> 00:00:02,000\n"
        "반갑습니다\n"
    )

    with patch("main.pipeline_executor") as mock_exec, \
         patch("main.UPLOADS", tmp_path), \
         patch("main.save_job"):
        mock_exec.submit = MagicMock()
        r = client.post(
            "/upload",
            data={"translation_model": "gemini"},
            files={
                "file": ("test.mp4", b"\x00" * 100, "video/mp4"),
                "subtitle_file": ("subtitle.srt", subtitle_content.encode("utf-8"), "application/x-subrip"),
            },
        )

    assert r.status_code == 200
    data = r.json()
    job_id = data["job_id"]
    assert data["source_has_ko_subtitle"] is True
    assert jobs[job_id]["progress"] == 30
    assert jobs[job_id]["source_has_ko_subtitle"] is True
    assert (tmp_path / f"{job_id}.srt").exists()
    mock_exec.submit.assert_called_once_with(__import__("main").run_translate_pipeline, job_id)

def test_upload_rejects_non_srt_subtitle_file(client, tmp_path):
    with patch("main.pipeline_executor") as mock_exec, \
         patch("main.UPLOADS", tmp_path), \
         patch("main.save_job"):
        mock_exec.submit = MagicMock()
        r = client.post(
            "/upload",
            files={
                "file": ("test.mp4", b"\x00" * 100, "video/mp4"),
                "subtitle_file": ("subtitle.txt", b"hello", "text/plain"),
            },
        )

    assert r.status_code == 400
    assert ".srt" in r.json()["detail"]

def test_upload_rejects_invalid_ko_subtitle_contents(client, tmp_path):
    with patch("main.pipeline_executor") as mock_exec, \
         patch("main.UPLOADS", tmp_path), \
         patch("main.save_job"):
        mock_exec.submit = MagicMock()
        r = client.post(
            "/upload",
            files={
                "file": ("test.mp4", b"\x00" * 100, "video/mp4"),
                "subtitle_file": ("subtitle.srt", b"not an srt", "application/x-subrip"),
            },
        )

    assert r.status_code == 400
    assert "한국어 자막 파일이 올바르지 않습니다" in r.json()["detail"]


# ── Status ───────────────────────────────────────────────────────────────────

def test_status_not_found(client):
    r = client.get("/status/nonexistent")
    assert r.json()["status"] == "not_found"

def test_status_found(client):
    from store.jobs import jobs
    jobs["test123"] = {"status": "done", "message": "ok", "progress": 100}
    r = client.get("/status/test123")
    assert r.json()["status"] == "done"


def test_retry_translate_can_switch_model(client, tmp_path):
    from store.jobs import jobs

    jobs["job1"] = {"status": "ready_to_translate", "translation_model": "gemini"}
    (tmp_path / "job1.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n안녕\n", encoding="utf-8")

    with patch("main.UPLOADS", tmp_path), \
         patch("main.pipeline_executor") as mock_exec, \
         patch("main.save_job"):
        mock_exec.submit = MagicMock()
        r = client.post("/retry/job1", json={"translation_model": "claude"})

    assert r.status_code == 200
    assert r.json()["translation_model"] == "claude"
    assert jobs["job1"]["translation_model"] == "claude"
    assert jobs["job1"]["status"] == "queued"
    mock_exec.submit.assert_called_once()


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

def test_retranslate_uses_job_translation_model(client, tmp_path):
    from store.jobs import jobs

    (tmp_path / "job1.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n안녕\n", encoding="utf-8")
    (tmp_path / "job1_en.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    jobs["job1"] = {"translation_model": "gemini"}

    with patch("main.UPLOADS", tmp_path), \
         patch("main.retranslate_block", return_value="Hi there") as mock_retranslate:
        r = client.post("/retranslate/job1", json={"idx": "1", "requirement": ""})

    assert r.status_code == 200
    assert r.json()["text"] == "Hi there"
    mock_retranslate.assert_called_once_with("안녕", "Hello", "", "gemini")


def test_retranslate_returns_structured_error(client, tmp_path):
    from store.jobs import jobs

    (tmp_path / "job1.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n안녕\n", encoding="utf-8")
    jobs["job1"] = {"translation_model": "gemini"}

    with patch("main.UPLOADS", tmp_path), \
         patch("main.retranslate_block", side_effect=TranslationError(
             detail="HTTP 503: Service Unavailable",
             user_message="Gemini API가 일시적으로 응답하지 않습니다. (503)",
             hint="잠시 후 다시 시도하세요.",
             error_code="service_unavailable",
             retryable=True,
         )):
        r = client.post("/retranslate/job1", json={"idx": "1", "requirement": ""})

    assert r.status_code == 502
    data = r.json()
    assert "503" in data["error"]
    assert data["retryable"] is True
    assert data["error_code"] == "service_unavailable"


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
