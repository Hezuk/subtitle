"""services/transcription.py 단위 테스트."""
from unittest.mock import MagicMock, patch

import pytest

from services import transcription


def test_normalize_whisper_model_defaults_to_env_default():
    assert transcription.normalize_whisper_model(None) == transcription.DEFAULT_WHISPER_MODEL


def test_normalize_whisper_model_rejects_invalid_value():
    with pytest.raises(ValueError):
        transcription.normalize_whisper_model("unknown")


def test_transcribe_uses_selected_model_and_caches_it():
    fake_model = MagicMock()
    fake_model.transcribe.return_value = {"segments": [], "language": "ko"}
    ready_models = []

    with patch.dict(transcription._models, {}, clear=True), \
         patch("services.transcription.whisper.load_model", return_value=fake_model) as mock_load:
        transcription.transcribe("/tmp/a.mp4", model_name="turbo", on_model_ready=ready_models.append)
        transcription.transcribe("/tmp/b.mp4", model_name="turbo")

    mock_load.assert_called_once_with("turbo")
    assert ready_models == ["turbo"]
    fake_model.transcribe.assert_any_call("/tmp/a.mp4", fp16=transcription.WHISPER_FP16, language=transcription.WHISPER_LANGUAGE)
    fake_model.transcribe.assert_any_call("/tmp/b.mp4", fp16=transcription.WHISPER_FP16, language=transcription.WHISPER_LANGUAGE)
