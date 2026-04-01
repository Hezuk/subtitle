"""services/translation.py 단위 테스트."""
from unittest.mock import patch

import pytest
import requests

from services.translation import (
    _refine_batch,
    _review_ko_batch,
    _call_gemini,
    _provider_error,
    normalize_translation_model,
    retranslate_block,
    translate_blocks,
)
from utils.errors import TranslationError


def test_retranslate_applies_wrap():
    """재번역 결과에 wrap_subtitle이 적용되는지 확인."""
    # 42자 초과 긴 문장 — wrap_subtitle이 2줄로 분할해야 함
    long_text = "This is a very long subtitle line that definitely exceeds the maximum character limit"
    assert len(long_text) > 42

    with patch("services.translation._call_llm", return_value=f"  {long_text}  "):
        result = retranslate_block("한국어 텍스트", "current", "", "claude")

    assert "\n" in result
    for line in result.splitlines():
        assert len(line) <= 50  # 분할 후 각 줄이 합리적 길이


def test_retranslate_short_text_unchanged():
    """짧은 텍스트는 wrap 후에도 한 줄 유지."""
    short_text = "Hello world"

    with patch("services.translation._call_llm", return_value=short_text):
        result = retranslate_block("안녕", "current", "", "gemini")

    assert result == "Hello world"
    assert "\n" not in result


def test_normalize_translation_model_defaults_to_claude():
    assert normalize_translation_model(None) == "claude"


def test_normalize_translation_model_rejects_invalid_value():
    with pytest.raises(ValueError):
        normalize_translation_model("unknown")


def test_call_gemini_retries_on_503_then_succeeds():
    error_response = requests.Response()
    error_response.status_code = 503
    error_response._content = b"Service Unavailable"

    ok_response = requests.Response()
    ok_response.status_code = 200
    ok_response._content = b'{"candidates":[{"content":{"parts":[{"text":"Recovered"}]}}]}'

    with patch("services.translation._get_api_key", return_value="test-key"), \
         patch("services.translation.requests.post", side_effect=[error_response, ok_response]) as mock_post, \
         patch("services.translation.time.sleep") as mock_sleep:
        result = _call_gemini("prompt", timeout=5)

    assert result == "Recovered"
    assert mock_post.call_count == 2
    mock_sleep.assert_called_once()


def test_call_gemini_does_not_retry_on_400():
    bad_request = requests.Response()
    bad_request.status_code = 400
    bad_request._content = b"Bad Request"

    with patch("services.translation._get_api_key", return_value="test-key"), \
         patch("services.translation.requests.post", return_value=bad_request) as mock_post, \
         patch("services.translation.time.sleep") as mock_sleep:
        with pytest.raises(requests.HTTPError):
            _call_gemini("prompt", timeout=5)

    assert mock_post.call_count == 1
    mock_sleep.assert_not_called()


def test_translate_blocks_respects_gemini_batch_size():
    blocks = [
        {"idx": "1", "text": "하나"},
        {"idx": "2", "text": "둘"},
        {"idx": "3", "text": "셋"},
    ]

    def fake_translate(batch, translation_model, context=""):
        return {item["idx"]: f"{translation_model}:{item['idx']}" for item in batch}

    with patch("services.translation.GEMINI_TRANSLATE_BATCH", 2), \
         patch("services.translation._translate_batch", side_effect=fake_translate) as mock_translate:
        result = translate_blocks(blocks, "gemini")

    assert result == {"1": "gemini:1", "2": "gemini:2", "3": "gemini:3"}
    assert mock_translate.call_count == 2


def test_refine_batch_prompt_is_translation_oriented():
    blocks = [{"idx": "1", "text": "음 그 사람한테 어 말했어요"}]

    with patch("services.translation._call_llm", return_value="[1] 그 사람한테 말했어요") as mock_call:
        result = _refine_batch(blocks, "claude")

    prompt = mock_call.call_args.args[0]
    assert "translated into English next" in prompt
    assert "reduce ambiguity for English translation" in prompt
    assert "translation-ready Korean suitable for subtitles" in prompt
    assert result == {"1": "그 사람한테 말했어요"}


def test_review_ko_batch_prompt_is_final_qa_oriented():
    blocks = [{"idx": "1", "text": "그 사람한테 말했어요"}]

    with patch("services.translation._call_llm", return_value="[1] 그분께 말씀드렸어요") as mock_call:
        result = _review_ko_batch(blocks, "gemini")

    prompt = mock_call.call_args.args[0]
    assert "translated into English next" in prompt
    assert "revise it only when needed" in prompt
    assert "smallest safe edit" in prompt
    assert result == {"1": "그분께 말씀드렸어요"}


def test_provider_error_for_gemini_503_is_user_friendly():
    response = requests.Response()
    response.status_code = 503
    response._content = b"Service Unavailable"
    exc = requests.HTTPError(response=response)

    err = _provider_error(exc, "gemini", TranslationError)

    assert "503" in err.user_message
    assert "Claude" in err.hint
    assert err.retryable is True
    assert err.error_code == "service_unavailable"
