"""utils/errors.py 예외 클래스 테스트."""
from utils.errors import (
    SubtitleError, ConfigError, TranscriptionError,
    TranslationError, ReviewError, EncodeError,
)


def test_hierarchy():
    for cls in (ConfigError, TranscriptionError, TranslationError, ReviewError, EncodeError):
        assert issubclass(cls, SubtitleError)
        assert issubclass(cls, Exception)


def test_default_user_message():
    e = TranscriptionError(detail="model load failed")
    assert e.detail == "model load failed"
    assert "음성 인식" in e.user_message


def test_custom_user_message():
    e = EncodeError(detail="rc=1", user_message="커스텀 메시지")
    assert e.user_message == "커스텀 메시지"
    assert e.detail == "rc=1"


def test_str_uses_detail():
    e = ConfigError(detail="GEMINI_API_KEY missing")
    assert "GEMINI_API_KEY missing" in str(e)


def test_empty_detail():
    e = TranslationError()
    assert e.detail == ""
    assert e.user_message  # default message exists
