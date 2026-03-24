import subprocess
from pathlib import Path
from config import FFMPEG_SUBTITLE_STYLE, FFMPEG_CRF, FFMPEG_PRESET, FFMPEG_TIMEOUT
from utils.log import get_logger
from utils.errors import EncodeError

log = get_logger("encoding")


def encode_video(input_path: str, srt_path: Path, output_path: Path, timeout: int | None = None):
    """ffmpeg으로 자막 번인. 실패 시 EncodeError."""
    if timeout is None:
        timeout = FFMPEG_TIMEOUT
    srt_str = str(srt_path).replace("\\", "/").replace(":", "\\:")

    log.info("ffmpeg 시작: input=%s srt=%s output=%s", input_path, srt_path, output_path)
    try:
        r = subprocess.run(
            [
                "ffmpeg", "-i", input_path,
                "-vf", f"subtitles={srt_str}:force_style='{FFMPEG_SUBTITLE_STYLE}'",
                "-c:v", "libx264", "-crf", FFMPEG_CRF, "-preset", FFMPEG_PRESET,
                "-c:a", "copy",
                "-movflags", "+faststart",
                str(output_path), "-y",
            ],
            capture_output=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        log.error("ffmpeg 타임아웃 (%ds)", timeout)
        raise EncodeError(
            detail=f"ffmpeg timeout after {timeout}s",
            user_message="인코딩 시간이 초과되었습니다. 영상이 너무 길 수 있습니다.",
        ) from e
    except Exception as e:
        log.error("ffmpeg 실행 실패: %s", e, exc_info=True)
        raise EncodeError(detail=str(e)) from e

    if r.returncode != 0 or not output_path.exists():
        stderr = r.stderr.decode(errors="replace")
        stderr_last = stderr.strip().split('\n')[-1][:200] if stderr.strip() else "unknown error"
        log.error("ffmpeg 실패 (rc=%d): %s", r.returncode, stderr_last)
        log.debug("ffmpeg stderr 전문:\n%s", stderr)
        raise EncodeError(detail=stderr_last)

    log.info("ffmpeg 완료: %s", output_path)
