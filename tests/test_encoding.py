"""services/encoding.py 테스트 — ffmpeg subprocess를 mock 처리."""
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from services.encoding import encode_video
from utils.errors import CancelledError, EncodeError


@pytest.fixture
def tmp_paths(tmp_path):
    inp = tmp_path / "input.mp4"
    inp.write_text("fake")
    srt = tmp_path / "sub.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n")
    out = tmp_path / "out.mp4"
    return inp, srt, out


# ── 정상 인코딩 ──────────────────────────────────────────────────────────────

def test_encode_success(tmp_paths):
    inp, srt, out = tmp_paths

    proc = MagicMock()
    proc.poll.side_effect = [None, 0]  # 한 번 대기 후 완료
    proc.returncode = 0

    with patch("services.encoding.subprocess.Popen", return_value=proc):
        out.write_text("result")  # output_path.exists() 통과
        encode_video(str(inp), srt, out)

    proc.poll.assert_called()


# ── ffmpeg 실행 실패 ─────────────────────────────────────────────────────────

def test_encode_popen_failure(tmp_paths):
    inp, srt, out = tmp_paths

    with patch("services.encoding.subprocess.Popen", side_effect=FileNotFoundError("ffmpeg not found")):
        with pytest.raises(EncodeError):
            encode_video(str(inp), srt, out)


# ── ffmpeg 비정상 종료 ───────────────────────────────────────────────────────

def test_encode_nonzero_returncode(tmp_paths):
    inp, srt, out = tmp_paths

    proc = MagicMock()
    proc.poll.side_effect = [None, 1]
    proc.returncode = 1

    stderr_file = MagicMock()
    stderr_file.read.return_value = b"error: codec not found"
    stderr_file.seek = MagicMock()

    with (
        patch("services.encoding.subprocess.Popen", return_value=proc),
        patch("services.encoding.tempfile.TemporaryFile", return_value=stderr_file),
    ):
        with pytest.raises(EncodeError):
            encode_video(str(inp), srt, out)


# ── 취소 ─────────────────────────────────────────────────────────────────────

def test_encode_cancel(tmp_paths):
    inp, srt, out = tmp_paths

    proc = MagicMock()
    proc.poll.return_value = None  # 계속 실행 중
    proc.wait.return_value = None

    with patch("services.encoding.subprocess.Popen", return_value=proc):
        with pytest.raises(CancelledError):
            encode_video(str(inp), srt, out, cancel_check=lambda: True)

    proc.terminate.assert_called_once()


# ── 타임아웃 ─────────────────────────────────────────────────────────────────

def test_encode_timeout(tmp_paths):
    inp, srt, out = tmp_paths

    proc = MagicMock()
    proc.poll.return_value = None
    proc.wait.return_value = None

    with patch("services.encoding.subprocess.Popen", return_value=proc):
        with pytest.raises(EncodeError, match="timeout"):
            encode_video(str(inp), srt, out, timeout=0)

    proc.terminate.assert_called_once()
