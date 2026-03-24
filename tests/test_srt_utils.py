"""utils/srt.py 순수 함수 테스트."""
import tempfile
from pathlib import Path
from utils.srt import (
    fmt_elapsed, seconds_to_srt_time, segments_to_srt,
    parse_srt, srt_to_vtt, wrap_subtitle, validate_blocks,
    load_blocks, save_blocks,
)


# ── fmt_elapsed ──────────────────────────────────────────────────────────────

def test_fmt_elapsed_seconds():
    assert fmt_elapsed(0) == "0초"
    assert fmt_elapsed(45) == "45초"
    assert fmt_elapsed(59.9) == "59초"

def test_fmt_elapsed_minutes():
    assert fmt_elapsed(60) == "1분 0초"
    assert fmt_elapsed(125) == "2분 5초"


# ── seconds_to_srt_time ─────────────────────────────────────────────────────

def test_seconds_to_srt_time():
    assert seconds_to_srt_time(0) == "00:00:00,000"
    assert seconds_to_srt_time(3661.5) == "01:01:01,500"
    assert seconds_to_srt_time(0.123) == "00:00:00,123"


# ── segments_to_srt ──────────────────────────────────────────────────────────

def test_segments_to_srt():
    segs = [
        {"start": 0.0, "end": 1.5, "text": " 안녕하세요 "},
        {"start": 2.0, "end": 3.0, "text": " 감사합니다 "},
    ]
    result = segments_to_srt(segs)
    assert "1\n00:00:00,000 --> 00:00:01,500\n안녕하세요" in result
    assert "2\n00:00:02,000 --> 00:00:03,000\n감사합니다" in result


# ── parse_srt ────────────────────────────────────────────────────────────────

SAMPLE_SRT = """1
00:00:00,000 --> 00:00:01,500
Hello world

2
00:00:02,000 --> 00:00:03,000
Second line
with wrap
"""

def test_parse_srt_basic():
    blocks = parse_srt(SAMPLE_SRT)
    assert len(blocks) == 2
    assert blocks[0]["idx"] == "1"
    assert blocks[0]["timestamp"] == "00:00:00,000 --> 00:00:01,500"
    assert blocks[0]["text"] == "Hello world"

def test_parse_srt_multiline_text():
    blocks = parse_srt(SAMPLE_SRT)
    assert blocks[1]["text"] == "Second line\nwith wrap"

def test_parse_srt_empty():
    assert parse_srt("") == []
    assert parse_srt("\n\n\n") == []

def test_parse_srt_invalid_block():
    bad = "not a valid srt\njust some text"
    assert parse_srt(bad) == []

def test_parse_srt_numeric_text():
    """자막 텍스트에 숫자만 있는 줄이 있어도 오파싱되지 않아야 함."""
    srt = """1
00:00:00,000 --> 00:00:01,000
123

2
00:00:01,000 --> 00:00:02,000
Hello
"""
    blocks = parse_srt(srt)
    assert len(blocks) == 2
    assert blocks[0]["text"] == "123"


# ── srt_to_vtt ───────────────────────────────────────────────────────────────

def test_srt_to_vtt():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".srt", delete=False, encoding="utf-8") as f:
        f.write(SAMPLE_SRT)
        f.flush()
        result = srt_to_vtt(Path(f.name))
    assert result.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:01.500" in result  # comma → dot
    assert "," not in result.split("WEBVTT")[1]  # timestamp commas converted


# ── wrap_subtitle ────────────────────────────────────────────────────────────

def test_wrap_short():
    assert wrap_subtitle("Short text") == "Short text"

def test_wrap_long():
    text = "This is a really long subtitle that should be wrapped at some point"
    result = wrap_subtitle(text, max_chars=42)
    lines = result.split("\n")
    assert len(lines) == 2

def test_wrap_no_spaces():
    text = "A" * 50
    assert wrap_subtitle(text, max_chars=42) == text  # no space to split

def test_wrap_already_multiline():
    text = "Line one\nLine two"
    assert wrap_subtitle(text) == "Line one\nLine two"

def test_wrap_exactly_at_limit():
    text = "A" * 42
    assert wrap_subtitle(text) == text


# ── validate_blocks ──────────────────────────────────────────────────────────

def test_validate_valid():
    blocks = [
        {"idx": "1", "timestamp": "00:00:00,000 --> 00:00:01,000", "text": "Hello"},
        {"idx": "2", "timestamp": "00:00:01,000 --> 00:00:02,000", "text": "World"},
    ]
    assert validate_blocks(blocks) is None

def test_validate_empty():
    assert validate_blocks([]) is not None

def test_validate_not_list():
    assert validate_blocks("string") is not None

def test_validate_duplicate_idx():
    blocks = [
        {"idx": "1", "timestamp": "00:00:00,000 --> 00:00:01,000", "text": "A"},
        {"idx": "1", "timestamp": "00:00:01,000 --> 00:00:02,000", "text": "B"},
    ]
    err = validate_blocks(blocks)
    assert err is not None
    assert "중복" in err

def test_validate_bad_timestamp():
    blocks = [{"idx": "1", "timestamp": "not a timestamp", "text": "A"}]
    err = validate_blocks(blocks)
    assert err is not None
    assert "타임스탬프" in err

def test_validate_reversed_time():
    blocks = [{"idx": "1", "timestamp": "00:00:02,000 --> 00:00:01,000", "text": "A"}]
    err = validate_blocks(blocks)
    assert err is not None
    assert "앞섭니다" in err

def test_validate_empty_text():
    blocks = [{"idx": "1", "timestamp": "00:00:00,000 --> 00:00:01,000", "text": ""}]
    err = validate_blocks(blocks)
    assert err is not None
    assert "비어" in err

def test_validate_missing_idx():
    blocks = [{"timestamp": "00:00:00,000 --> 00:00:01,000", "text": "A"}]
    err = validate_blocks(blocks)
    assert err is not None

def test_validate_max_errors():
    """에러 5건 초과 시 '외 N건' 표시."""
    blocks = [{"idx": str(i), "timestamp": "bad", "text": "A"} for i in range(10)]
    err = validate_blocks(blocks)
    assert "외" in err


# ── load_blocks / save_blocks ────────────────────────────────────────────────

def test_save_and_load_blocks():
    blocks = [
        {"idx": "1", "timestamp": "00:00:00,000 --> 00:00:01,000", "text": "Hello"},
        {"idx": "2", "timestamp": "00:00:01,000 --> 00:00:02,000", "text": "World"},
    ]
    with tempfile.NamedTemporaryFile(suffix=".srt", delete=False) as f:
        path = Path(f.name)
    save_blocks(path, blocks)
    loaded = load_blocks(path)
    assert len(loaded) == 2
    assert loaded[0]["text"] == "Hello"
    assert loaded[1]["idx"] == "2"
    path.unlink()

def test_load_blocks_missing():
    assert load_blocks(Path("/nonexistent/path.srt")) is None
