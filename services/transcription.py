import threading

import whisper

from config import WHISPER_MODEL, WHISPER_LANGUAGE, WHISPER_FP16
from utils.log import get_logger
from utils.errors import TranscriptionError

log = get_logger("transcription")
AVAILABLE_WHISPER_MODELS = tuple(whisper.available_models())
_FALLBACK_WHISPER_MODEL = "large-v3-turbo" if "large-v3-turbo" in AVAILABLE_WHISPER_MODELS else AVAILABLE_WHISPER_MODELS[-1]


def normalize_whisper_model(model: str | None) -> str:
    value = (model or WHISPER_MODEL or _FALLBACK_WHISPER_MODEL).strip()
    if not value:
        value = _FALLBACK_WHISPER_MODEL
    if value not in AVAILABLE_WHISPER_MODELS:
        raise ValueError(value)
    return value


try:
    DEFAULT_WHISPER_MODEL = normalize_whisper_model(WHISPER_MODEL)
except ValueError:
    DEFAULT_WHISPER_MODEL = _FALLBACK_WHISPER_MODEL
    log.warning("잘못된 WHISPER_MODEL=%r, 기본값 %s 로 대체합니다.", WHISPER_MODEL, DEFAULT_WHISPER_MODEL)


_models: dict[str, object] = {}
_model_lock = threading.Lock()


def _get_model(model_name: str):
    model = _models.get(model_name)
    if model is not None:
        return model
    with _model_lock:
        model = _models.get(model_name)
        if model is not None:
            return model
        log.info("Whisper 모델 로딩 시작 (%s)", model_name)
        try:
            model = whisper.load_model(model_name)
        except Exception as e:
            log.error("Whisper 모델 로딩 실패 (%s): %s", model_name, e, exc_info=True)
            raise TranscriptionError(
                detail=str(e),
                user_message="음성 인식 모델 로딩에 실패했습니다. 디스크 공간과 메모리를 확인해주세요.",
            ) from e
        _models[model_name] = model
        log.info("Whisper 모델 로딩 완료 (%s)", model_name)
        return model


def transcribe(input_path: str, model_name: str | None = None, language: str | None = None, on_model_ready=None) -> dict:
    """Whisper로 음성 인식. 모델별로 첫 호출 시 로드 후 재사용."""
    model_name = normalize_whisper_model(model_name)
    model = _get_model(model_name)
    if on_model_ready:
        on_model_ready(model_name)

    lang = language or WHISPER_LANGUAGE
    log.info("음성 인식 시작: %s (model=%s, language=%s)", input_path, model_name, lang)
    try:
        result = model.transcribe(input_path, fp16=WHISPER_FP16, language=lang)
    except Exception as e:
        log.error("음성 인식 실패: %s", e, exc_info=True)
        raise TranscriptionError(detail=str(e)) from e

    seg_count = len(result.get("segments", []))
    detected = result.get("language", "unknown")
    log.info("음성 인식 완료: model=%s, segments=%d, language=%s", model_name, seg_count, detected)
    return result
