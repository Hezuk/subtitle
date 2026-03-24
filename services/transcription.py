import whisper
from utils.log import get_logger
from utils.errors import TranscriptionError

log = get_logger("transcription")
_model = None


def transcribe(input_path: str, language: str = "ko") -> dict:
    """Whisper로 음성 인식. 모델은 첫 호출 시 로드 후 재사용."""
    global _model
    if _model is None:
        log.info("Whisper 모델 로딩 시작 (large-v3-turbo)")
        try:
            _model = whisper.load_model("large-v3-turbo")
        except Exception as e:
            log.error("Whisper 모델 로딩 실패: %s", e, exc_info=True)
            raise TranscriptionError(
                detail=str(e),
                user_message="음성 인식 모델 로딩에 실패했습니다. 디스크 공간과 메모리를 확인해주세요.",
            ) from e
        log.info("Whisper 모델 로딩 완료")

    log.info("음성 인식 시작: %s", input_path)
    try:
        result = _model.transcribe(input_path, fp16=False, language=language)
    except Exception as e:
        log.error("음성 인식 실패: %s", e, exc_info=True)
        raise TranscriptionError(detail=str(e)) from e

    seg_count = len(result.get("segments", []))
    detected = result.get("language", "unknown")
    log.info("음성 인식 완료: segments=%d, language=%s", seg_count, detected)
    return result
