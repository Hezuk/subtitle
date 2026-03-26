import whisper
from config import WHISPER_MODEL, WHISPER_LANGUAGE, WHISPER_FP16
from utils.log import get_logger
from utils.errors import TranscriptionError

log = get_logger("transcription")
_model = None


def transcribe(input_path: str, language: str | None = None, on_model_ready=None) -> dict:
    """Whisper로 음성 인식. 모델은 첫 호출 시 로드 후 재사용."""
    global _model
    if _model is None:
        log.info("Whisper 모델 로딩 시작 (%s)", WHISPER_MODEL)
        try:
            _model = whisper.load_model(WHISPER_MODEL)
        except Exception as e:
            log.error("Whisper 모델 로딩 실패: %s", e, exc_info=True)
            raise TranscriptionError(
                detail=str(e),
                user_message="음성 인식 모델 로딩에 실패했습니다. 디스크 공간과 메모리를 확인해주세요.",
            ) from e
        log.info("Whisper 모델 로딩 완료")
    if on_model_ready:
        on_model_ready()

    lang = language or WHISPER_LANGUAGE
    log.info("음성 인식 시작: %s (language=%s)", input_path, lang)
    try:
        result = _model.transcribe(input_path, fp16=WHISPER_FP16, language=lang)
    except Exception as e:
        log.error("음성 인식 실패: %s", e, exc_info=True)
        raise TranscriptionError(detail=str(e)) from e

    seg_count = len(result.get("segments", []))
    detected = result.get("language", "unknown")
    log.info("음성 인식 완료: segments=%d, language=%s", seg_count, detected)
    return result
