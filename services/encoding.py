import subprocess, time, tempfile
from pathlib import Path
from config import FFMPEG_SUBTITLE_STYLE, FFMPEG_CRF, FFMPEG_PRESET, FFMPEG_TIMEOUT
from utils.log import get_logger
from utils.errors import CancelledError, EncodeError

log = get_logger("encoding")


def encode_video(input_path: str, srt_path: Path, output_path: Path,
                 timeout: int | None = None, cancel_check=None):
    """ffmpeg으로 자막 번인. cancel_check 콜백이 True 반환 시 프로세스 종료."""
    if timeout is None:
        timeout = FFMPEG_TIMEOUT
    srt_str = str(srt_path).replace("\\", "/").replace(":", "\\:")

    cmd = [
        "ffmpeg", "-i", input_path,
        "-vf", f"subtitles={srt_str}:force_style='{FFMPEG_SUBTITLE_STYLE}'",
        "-c:v", "libx264", "-crf", FFMPEG_CRF, "-preset", FFMPEG_PRESET,
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_path), "-y",
    ]

    log.info("ffmpeg 시작: input=%s srt=%s output=%s", input_path, srt_path, output_path)

    stderr_file = tempfile.TemporaryFile()
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=stderr_file)
    except Exception as e:
        stderr_file.close()
        log.error("ffmpeg 실행 실패: %s", e, exc_info=True)
        raise EncodeError(detail=str(e)) from e

    start = time.time()
    try:
        while proc.poll() is None:
            # 취소 확인
            if cancel_check and cancel_check():
                log.info("ffmpeg 취소 요청 — 프로세스 종료")
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                raise CancelledError()

            # 타임아웃 확인
            if time.time() - start > timeout:
                log.error("ffmpeg 타임아웃 (%ds)", timeout)
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                raise EncodeError(
                    detail=f"ffmpeg timeout after {timeout}s",
                    user_message="인코딩 시간이 초과되었습니다. 영상이 너무 길 수 있습니다.",
                )

            time.sleep(0.5)
    except (CancelledError, EncodeError):
        stderr_file.close()
        raise
    except Exception as e:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        stderr_file.close()
        log.error("ffmpeg 실행 중 오류: %s", e, exc_info=True)
        raise EncodeError(detail=str(e)) from e

    # 프로세스 완료 — stderr 읽기
    stderr_file.seek(0)
    stderr = stderr_file.read().decode(errors="replace")
    stderr_file.close()

    if proc.returncode != 0 or not output_path.exists():
        stderr_last = stderr.strip().split('\n')[-1][:200] if stderr.strip() else "unknown error"
        log.error("ffmpeg 실패 (rc=%d): %s", proc.returncode, stderr_last)
        log.debug("ffmpeg stderr 전문:\n%s", stderr)
        raise EncodeError(detail=stderr_last)

    log.info("ffmpeg 완료: %s", output_path)
