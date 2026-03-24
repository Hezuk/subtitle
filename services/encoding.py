import subprocess
from pathlib import Path
from utils.log import get_logger
from utils.errors import EncodeError

log = get_logger("encoding")

SUBTITLE_STYLE = "FontName=Arial,FontSize=20,PrimaryColour=&HFFFFFF,OutlineColour=&H000000,Outline=2,Bold=1,Alignment=2"


def encode_video(input_path: str, srt_path: Path, output_path: Path, timeout: int = 7200):
    """ffmpeg으로 자막 번인. 실패 시 EncodeError."""
    srt_str = str(srt_path).replace("\\", "/").replace(":", "\\:")

    log.info("ffmpeg 시작: input=%s srt=%s output=%s", input_path, srt_path, output_path)
    try:
        r = subprocess.run(
            [
                "ffmpeg", "-i", input_path,
                "-vf", f"subtitles={srt_str}:force_style='{SUBTITLE_STYLE}'",
                "-c:v", "libx264", "-crf", "18", "-preset", "fast",
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
        # 마지막 줄이 보통 핵심 에러 메시지
        stderr_last = stderr.strip().split('\n')[-1][:200] if stderr.strip() else "unknown error"
        log.error("ffmpeg 실패 (rc=%d): %s", r.returncode, stderr_last)
        log.debug("ffmpeg stderr 전문:\n%s", stderr)
        raise EncodeError(detail=stderr_last)

    log.info("ffmpeg 완료: %s", output_path)
