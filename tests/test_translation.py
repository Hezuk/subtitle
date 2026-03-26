"""services/translation.py 단위 테스트."""
from unittest.mock import patch
from services.translation import retranslate_with_gemini


def test_retranslate_applies_wrap():
    """재번역 결과에 wrap_subtitle이 적용되는지 확인."""
    # 42자 초과 긴 문장 — wrap_subtitle이 2줄로 분할해야 함
    long_text = "This is a very long subtitle line that definitely exceeds the maximum character limit"
    assert len(long_text) > 42

    with patch("services.translation._call_gemini", return_value=f"  {long_text}  "):
        result = retranslate_with_gemini("한국어 텍스트", "current", "")

    assert "\n" in result
    for line in result.splitlines():
        assert len(line) <= 50  # 분할 후 각 줄이 합리적 길이


def test_retranslate_short_text_unchanged():
    """짧은 텍스트는 wrap 후에도 한 줄 유지."""
    short_text = "Hello world"

    with patch("services.translation._call_gemini", return_value=short_text):
        result = retranslate_with_gemini("안녕", "current", "")

    assert result == "Hello world"
    assert "\n" not in result
