import re
from pathlib import Path
from config import MAX_SUBTITLE_CHARS

_TS_RE = re.compile(r'^(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})$')


def fmt_elapsed(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}초"
    return f"{s // 60}분 {s % 60}초"


def seconds_to_srt_time(s: float) -> str:
    ms = int(round((s % 1) * 1000))
    s = int(s)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02}:{m:02}:{sec:02},{ms:03}"


def segments_to_srt(segments) -> str:
    lines = []
    for i, seg in enumerate(segments, 1):
        start = seconds_to_srt_time(seg["start"])
        end = seconds_to_srt_time(seg["end"])
        text = seg["text"].strip()
        lines.append(f"{i}\n{start} --> {end}\n{text}\n")
    return "\n".join(lines)


def parse_srt(content: str) -> list[dict]:
    ts_pattern = re.compile(r'\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}')
    blocks = []
    for raw in re.split(r'\n{2,}', content.strip()):
        lines = raw.strip().splitlines()
        if len(lines) < 3:
            continue
        idx = lines[0].strip()
        ts = lines[1].strip()
        if not idx.isdigit() or not ts_pattern.match(ts):
            continue
        text = '\n'.join(lines[2:]).strip()
        blocks.append({"idx": idx, "timestamp": ts, "text": text})
    return blocks


def srt_to_vtt(path: Path) -> str:
    content = path.read_text(encoding="utf-8")
    return "WEBVTT\n\n" + re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", content)


def wrap_subtitle(text: str, max_chars: int | None = None) -> str:
    """한 줄이 max_chars를 초과하면 중간 공백에서 두 줄로 분할."""
    if max_chars is None:
        max_chars = MAX_SUBTITLE_CHARS
    lines = text.splitlines()
    wrapped = []
    for line in lines:
        line = line.strip()
        if len(line) <= max_chars:
            wrapped.append(line)
        else:
            mid = len(line) // 2
            left = line.rfind(' ', 0, mid + 1)
            right = line.find(' ', mid)
            if left == -1 and right == -1:
                wrapped.append(line)
                continue
            elif left == -1:
                split_at = right
            elif right == -1:
                split_at = left
            else:
                split_at = left if (mid - left) <= (right - mid) else right
            wrapped.append(line[:split_at].strip())
            wrapped.append(line[split_at:].strip())
    return "\n".join(wrapped)


def _ts_to_ms(h, m, s, ms):
    return int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(ms)


def validate_blocks(blocks: list) -> str | None:
    """블록 리스트 검증. 문제가 있으면 에러 메시지 반환, 없으면 None."""
    if not isinstance(blocks, list) or len(blocks) == 0:
        return "블록이 비어 있습니다."
    seen_idx = set()
    errors = []
    for i, b in enumerate(blocks):
        if not isinstance(b, dict):
            errors.append(f"블록 {i+1}: 유효하지 않은 형식")
            continue
        idx = b.get("idx", "")
        ts = b.get("timestamp", "")
        text = b.get("text", "")
        if not str(idx).strip():
            errors.append(f"블록 {i+1}: 번호가 없습니다")
            continue
        idx_str = str(idx).strip()
        if idx_str in seen_idx:
            errors.append(f"블록 {idx_str}: 번호가 중복됩니다")
        seen_idx.add(idx_str)
        m = _TS_RE.match(ts.strip())
        if not m:
            errors.append(f"블록 {idx_str}: 타임스탬프 형식이 잘못되었습니다")
            continue
        start_ms = _ts_to_ms(*m.groups()[:4])
        end_ms = _ts_to_ms(*m.groups()[4:])
        if end_ms <= start_ms:
            errors.append(f"블록 {idx_str}: 종료 시간이 시작 시간보다 앞섭니다")
        if not text.strip():
            errors.append(f"블록 {idx_str}: 텍스트가 비어 있습니다")
    if errors:
        return "; ".join(errors[:5]) + (f" 외 {len(errors)-5}건" if len(errors) > 5 else "")
    return None


def load_blocks(path: Path):
    if not path.exists():
        return None
    return parse_srt(path.read_text(encoding="utf-8"))


def save_blocks(path: Path, blocks: list):
    lines = [f"{b['idx']}\n{b['timestamp']}\n{b['text']}\n" for b in blocks]
    path.write_text("\n".join(lines), encoding="utf-8")
