import re
import time

import requests

from config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_MAX_TOKENS,
    ANTHROPIC_MODEL,
    ANTHROPIC_RETRANSLATE_TIMEOUT,
    ANTHROPIC_TIMEOUT,
    DEFAULT_TRANSLATION_MODEL,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GEMINI_RETRY_ATTEMPTS,
    GEMINI_RETRY_BASE_DELAY,
    GEMINI_RETRY_MAX_DELAY,
    GEMINI_RETRANSLATE_TIMEOUT,
    GEMINI_REVIEW_BATCH,
    GEMINI_TIMEOUT,
    GEMINI_TRANSLATE_BATCH,
)
from utils.errors import ConfigError, ReviewError, TranslationError
from utils.log import get_logger
from utils.srt import wrap_subtitle

log = get_logger("translation")

SUPPORTED_TRANSLATION_MODELS = ("claude", "gemini")
MODEL_LABELS = {
    "claude": "Claude",
    "gemini": "Gemini",
}

_ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"


def normalize_translation_model(model: str | None) -> str:
    value = (model or DEFAULT_TRANSLATION_MODEL or "claude").strip().lower()
    if not model and value not in SUPPORTED_TRANSLATION_MODELS:
        log.warning("잘못된 DEFAULT_TRANSLATION_MODEL=%r, 'claude'로 대체합니다", DEFAULT_TRANSLATION_MODEL)
        return "claude"
    if value not in SUPPORTED_TRANSLATION_MODELS:
        raise ValueError(f"지원하지 않는 번역 모델입니다: {model}")
    return value


def _get_api_key(translation_model: str) -> str:
    if translation_model == "claude":
        if not ANTHROPIC_API_KEY:
            raise ConfigError(
                detail="ANTHROPIC_API_KEY 환경변수 없음",
                user_message="Claude API 키가 설정되지 않았습니다. .env 파일을 확인해주세요.",
            )
        return ANTHROPIC_API_KEY

    if not GEMINI_API_KEY:
        raise ConfigError(
            detail="GEMINI_API_KEY 환경변수 없음",
            user_message="Gemini API 키가 설정되지 않았습니다. .env 파일을 확인해주세요.",
        )
    return GEMINI_API_KEY


def _call_claude(prompt: str, timeout: int | None = None) -> str:
    if timeout is None:
        timeout = ANTHROPIC_TIMEOUT
    resp = requests.post(
        _ANTHROPIC_API_URL,
        headers={
            "x-api-key": _get_api_key("claude"),
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "output-128k-2025-02-19",
            "content-type": "application/json",
        },
        json={
            "model": ANTHROPIC_MODEL,
            "max_tokens": ANTHROPIC_MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=timeout,
    )
    if not resp.ok:
        log.error("Anthropic API 오류: status=%d body=%s", resp.status_code, resp.text[:500])
    resp.raise_for_status()
    data = resp.json()
    if data.get("stop_reason") == "max_tokens":
        log.warning("Claude 응답이 max_tokens에 도달하여 잘렸습니다")
    return data["content"][0]["text"]


def _call_gemini(prompt: str, timeout: int | None = None) -> str:
    if timeout is None:
        timeout = GEMINI_TIMEOUT
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    for attempt in range(1, GEMINI_RETRY_ATTEMPTS + 1):
        try:
            resp = requests.post(
                f"{api_url}?key={_get_api_key('gemini')}",
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=timeout,
            )
            if not resp.ok:
                log.error("Gemini API 오류: status=%d body=%s", resp.status_code, resp.text[:500])
            resp.raise_for_status()
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        except requests.RequestException as exc:
            should_retry, status = _should_retry_gemini_error(exc, attempt)
            if not should_retry:
                raise

            delay = _gemini_delay_seconds(attempt)
            detail = f"status={status}" if status is not None else type(exc).__name__
            log.warning(
                "Gemini API 재시도 예정 (%d/%d, %s, %.1fs 후, model=%s)",
                attempt,
                GEMINI_RETRY_ATTEMPTS,
                detail,
                delay,
                GEMINI_MODEL,
            )
            time.sleep(delay)

    raise RuntimeError("Gemini API 호출 재시도 루프가 비정상 종료되었습니다")


def _call_llm(prompt: str, translation_model: str, timeout: int | None = None) -> str:
    model = normalize_translation_model(translation_model)
    if model == "claude":
        return _call_claude(prompt, timeout=timeout)
    return _call_gemini(prompt, timeout=timeout)


def _summarize_provider_detail(exc: Exception) -> str:
    if isinstance(exc, requests.HTTPError):
        response = exc.response
        status = response.status_code if response is not None else "unknown"
        body = ""
        if response is not None:
            try:
                body = re.sub(r"\s+", " ", response.text).strip()
            except Exception:
                body = ""
        return f"HTTP {status}: {body[:200]}".rstrip(": ")

    if isinstance(exc, requests.Timeout):
        return "provider timeout"

    if isinstance(exc, requests.ConnectionError):
        return "provider connection error"

    return re.sub(r"\s+", " ", str(exc)).strip()[:200]


def _provider_error(exc: Exception, translation_model: str, err_cls):
    model_label = MODEL_LABELS[translation_model]
    detail = _summarize_provider_detail(exc)

    if isinstance(exc, requests.Timeout):
        return err_cls(
            detail=detail,
            user_message=f"{model_label} 응답 시간이 초과되었습니다.",
            hint="잠시 후 다시 시도하세요. 반복되면 더 짧은 영상으로 나누거나 Claude로 바꿔보세요.",
            error_code="provider_timeout",
            retryable=True,
        )

    if isinstance(exc, requests.ConnectionError):
        return err_cls(
            detail=detail,
            user_message=f"{model_label} API 연결에 실패했습니다.",
            hint="인터넷 연결을 확인한 뒤 잠시 후 다시 시도하세요.",
            error_code="provider_connection",
            retryable=True,
        )

    if isinstance(exc, requests.HTTPError):
        response = exc.response
        status = response.status_code if response is not None else None

        if status == 503:
            return err_cls(
                detail=detail,
                user_message=f"{model_label} API가 일시적으로 응답하지 않습니다. (503)",
                hint="1~2분 후 다시 시도하세요. 계속 반복되면 Claude로 바꿔 번역을 다시 시도해보세요.",
                error_code="service_unavailable",
                retryable=True,
            )
        if status == 429:
            return err_cls(
                detail=detail,
                user_message=f"{model_label} API 요청 한도에 도달했습니다. (429)",
                hint="잠시 기다렸다가 다시 시도하세요. 반복되면 Claude로 바꾸는 것도 방법입니다.",
                error_code="rate_limited",
                retryable=True,
            )
        if status in {500, 502, 504}:
            return err_cls(
                detail=detail,
                user_message=f"{model_label} 서버 오류가 발생했습니다. ({status})",
                hint="잠시 후 다시 시도하세요. 같은 오류가 반복되면 다른 모델로 재시도해보세요.",
                error_code="provider_server_error",
                retryable=True,
            )
        if status in {401, 403}:
            return err_cls(
                detail=detail,
                user_message=f"{model_label} API 인증에 실패했습니다. ({status})",
                hint="API 키와 권한 설정을 확인한 뒤 다시 시도하세요.",
                error_code="provider_auth_error",
                retryable=False,
            )
        if status == 400:
            return err_cls(
                detail=detail,
                user_message=f"{model_label} 요청이 거부되었습니다. (400)",
                hint="모델 설정을 확인한 뒤 다시 시도하세요.",
                error_code="provider_bad_request",
                retryable=False,
            )

        return err_cls(
            detail=detail,
            user_message=f"{model_label} API 호출에 실패했습니다. ({status or 'unknown'})",
            hint="잠시 후 다시 시도해주세요.",
            error_code="provider_http_error",
            retryable=True,
        )

    return err_cls(
        detail=detail,
        user_message=f"{model_label} 호출 중 알 수 없는 오류가 발생했습니다.",
        hint="잠시 후 다시 시도해주세요.",
        error_code="provider_unknown_error",
        retryable=True,
    )


def _parse_block_response(response: str, blocks: list[dict]) -> dict[str, str]:
    """LLM 응답에서 [idx] 패턴으로 블록별 텍스트 추출."""
    result: dict[str, str] = {}
    missed: list[str] = []
    for b in blocks:
        m = re.search(rf"\[{b['idx']}\][ \t]*(.*?)(?=\n\[|\Z)", response, re.DOTALL)
        if m:
            text = m.group(1).strip()
            text = re.sub(r"\s*---\s*", "", text).strip()
            result[b["idx"]] = text
        else:
            missed.append(b["idx"])
            result[b["idx"]] = b["text"]
    if missed:
        log.warning(
            "응답에서 찾지 못한 블록 %d개: %s ... (응답 마지막 200자: %r)",
            len(missed),
            missed[:10],
            response[-200:],
        )
    return result


_TRANSLATE_BATCH = 200   # Claude 번역 배치 크기
_REVIEW_BATCH = 500      # Claude 검토 배치 크기
_CONTEXT_TAIL = 50       # 이전 배치에서 context로 넘길 블록 수
_GEMINI_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _gemini_delay_seconds(attempt: int) -> float:
    delay = GEMINI_RETRY_BASE_DELAY * (2 ** (attempt - 1))
    return min(delay, GEMINI_RETRY_MAX_DELAY)


def _should_retry_gemini_error(exc: requests.RequestException, attempt: int) -> tuple[bool, int | None]:
    if attempt >= GEMINI_RETRY_ATTEMPTS:
        return False, None

    if isinstance(exc, requests.HTTPError):
        response = exc.response
        status = response.status_code if response is not None else None
        return status in _GEMINI_RETRYABLE_STATUS_CODES, status

    return True, None


def _get_translate_batch_size(translation_model: str) -> int:
    return GEMINI_TRANSLATE_BATCH if translation_model == "gemini" else _TRANSLATE_BATCH


def _get_review_batch_size(translation_model: str) -> int:
    return GEMINI_REVIEW_BATCH if translation_model == "gemini" else _REVIEW_BATCH


def _format_context(prev_result: dict[str, str], prev_blocks: list[dict], n: int) -> str:
    """직전 배치의 마지막 n개 블록의 번역 결과를 context 문자열로 포맷."""
    tail = prev_blocks[-n:]
    lines = [f"[{b['idx']}] {prev_result.get(b['idx'], b['text'])}" for b in tail]
    return "\n".join(lines)


def _translate_batch(blocks: list[dict], translation_model: str, context: str = "") -> dict[str, str]:
    """단일 배치 번역. context는 이전 배치 마지막 번역 결과."""
    texts = "\n---\n".join(f"[{b['idx']}] {b['text']}" for b in blocks)
    context_section = (
        "Previously translated segments (for context and consistency only — do NOT output these):\n"
        f"{context}\n\n"
    ) if context else ""
    prompt = (
        "You are a professional Korean-to-English subtitle translator.\n"
        "Translate each Korean subtitle segment into natural English subtitles that preserve the original meaning and nuance as much as possible.\n\n"
        + context_section +
        "Output rules:\n"
        "- Keep the same [number] marker for every segment\n"
        "- Return ONLY the segments listed under 'Segments' below — do NOT re-output context segments\n"
        "- Do not omit, merge, split, reorder, explain, or comment\n\n"
        "Length constraints (CRITICAL):\n"
        "- Each segment must be at most 2 lines\n"
        "- Each line must be no longer than 42 characters\n"
        "- If a translation is too long, rephrase or shorten it only as much as needed to fit — do not just truncate\n"
        "- Do not compress the line more than necessary. Preserve meaningful detail and nuance whenever they fit within subtitle limits\n"
        "- When a segment uses 2 lines, break lines at natural meaning units so the subtitle is easy to read on screen\n"
        "- Prefer line breaks at phrase or clause boundaries; avoid splitting tightly connected words or short function words from what follows\n\n"
        "Context and flow:\n"
        "- Read all segments before translating to understand the full context\n"
        "- If a Korean segment is a sentence fragment that continues from the previous segment,\n"
        "  translate it so it reads naturally on its own — rephrase or complete the thought briefly\n"
        "  rather than translating the fragment literally\n"
        "- Keep names, terms, and phrasing consistent with the previously translated segments above\n\n"
        "Subtitle style rules:\n"
        "- Write natural spoken English suitable for on-screen subtitles\n"
        "- Prioritize fidelity to the Korean, clarity, and readability; prefer preserving nuance over aggressive shortening\n"
        "- Preserve the original meaning, tone, intent, and speaker attitude\n"
        "- Use idiomatic English when it sounds more natural\n"
        "- Do not over-translate or add information not present in the Korean\n"
        "- Avoid stiff, literary, or overly formal phrasing unless the source is clearly formal\n"
        "- If the Korean is casual, make the English casual; if polite, keep it politely neutral\n"
        "- Fragments are allowed when they sound natural as subtitles; do not force every line into a full sentence\n"
        "- Resolve implied subjects or objects only when necessary for natural English\n\n"
        "Important cautions:\n"
        "- When filler words (음, 어, 그, etc.) or interjections (surprise sounds, hesitation, sighs, laughter) appear within a sentence, omit them from the translation\n"
        "- If a segment contains ONLY fillers or interjections with no meaningful content, keep the [number] marker and output an empty line\n"
        "- Do not leave Korean words untranslated unless they are proper nouns\n\n"
        "Segments:\n\n"
        f"{texts}"
    )
    try:
        response = _call_llm(prompt, translation_model)
    except Exception as e:
        if isinstance(e, (ConfigError, TranslationError)):
            raise
        log.error("%s 번역 API 호출 실패: %s", MODEL_LABELS[translation_model], e, exc_info=True)
        raise _provider_error(e, translation_model, TranslationError) from e

    raw = _parse_block_response(response, blocks)
    return {idx: wrap_subtitle(text) for idx, text in raw.items()}


def translate_blocks(blocks: list[dict], translation_model: str) -> dict[str, str]:
    translation_model = normalize_translation_model(translation_model)
    batch_size = _get_translate_batch_size(translation_model)
    total = len(blocks)
    log.info("%s 번역 시작: %d개 블록 (배치 크기: %d)", MODEL_LABELS[translation_model], total, batch_size)
    result: dict[str, str] = {}
    prev_blocks: list[dict] = []
    for i in range(0, total, batch_size):
        batch = blocks[i:i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (total + batch_size - 1) // batch_size
        log.info(
            "%s 번역 배치 %d/%d (블록 %d~%d)",
            MODEL_LABELS[translation_model],
            batch_num,
            total_batches,
            i + 1,
            min(i + batch_size, total),
        )
        context = _format_context(result, prev_blocks, _CONTEXT_TAIL) if prev_blocks else ""
        batch_result = _translate_batch(batch, translation_model, context=context)
        result.update(batch_result)
        prev_blocks = batch
    log.info("%s 번역 완료: %d개 블록", MODEL_LABELS[translation_model], len(result))
    return result


def _review_batch(blocks: list[dict], translation_model: str, context: str = "") -> dict[str, str]:
    """단일 배치 검토. context는 이전 배치 마지막 검토 결과."""
    texts = "\n---\n".join(f"[{b['idx']}] {b['text']}" for b in blocks)
    context_section = (
        "Previously reviewed segments (for context and consistency only — do NOT output these):\n"
        f"{context}\n\n"
    ) if context else ""
    prompt = (
        "You are an English subtitle editor reviewing machine-translated subtitles.\n"
        "Revise each segment so it reads like polished, natural on-screen English subtitles.\n\n"
        + context_section +
        "Check and fix:\n"
        "1. Any untranslated or partially untranslated Korean\n"
        "2. Awkward, literal, ungrammatical, or unnatural English\n"
        "3. Inconsistent names, terms, tone, or repeated phrasing — match the previously reviewed segments above\n"
        "4. Overly wordy lines that can be made shorter without losing meaning or nuance\n"
        "5. Lines exceeding 42 characters — rephrase to fit, do not just truncate\n"
        "6. Segments with more than 2 lines — condense into at most 2 lines\n"
        "7. Sentence fragments that feel incomplete on their own — rephrase so each segment reads naturally in isolation\n\n"
        "Rules:\n"
        "- Keep the same [number] marker for every segment\n"
        "- Return ONLY the segments listed under 'Segments' below — do NOT re-output context segments\n"
        "- Do not omit, merge, split, reorder, explain, or comment\n"
        "- Each segment: at most 2 lines, each line no longer than 42 characters\n"
        "- Use natural spoken English suitable for subtitles while preserving meaningful detail and nuance when they fit\n"
        "- If a segment uses 2 lines, keep the line break at a natural meaning unit so it reads smoothly on screen\n"
        "- Preserve meaning, tone, intent, and speaker attitude\n"
        "- Fragments are allowed when natural for subtitles; do not force every line into a full sentence\n"
        "- Fix problems yourself whenever possible — rewrite and rephrase freely, but do not simplify away meaningful detail unless needed to satisfy subtitle limits\n"
        "- Length violations, awkward phrasing, or minor inaccuracies are YOUR job to fix, not [RETRANSLATE]\n"
        "- Output [RETRANSLATE] ONLY when the entire meaning is fundamentally wrong or the Korean was clearly mistranslated in a way you cannot recover without seeing the original\n"
        "- When in doubt, fix it yourself rather than marking [RETRANSLATE]\n\n"
        "Segments:\n\n"
        f"{texts}"
    )
    try:
        response = _call_llm(prompt, translation_model)
    except Exception as e:
        if isinstance(e, (ConfigError, ReviewError)):
            raise
        log.error("%s 검토 API 호출 실패: %s", MODEL_LABELS[translation_model], e, exc_info=True)
        raise _provider_error(e, translation_model, ReviewError) from e

    raw = _parse_block_response(response, blocks)
    result: dict[str, str] = {}
    for idx, text in raw.items():
        if re.search(r'\[RETRANSLATE\]|^RETRANSLATE$', text, re.IGNORECASE):
            result[idx] = "__RETRANSLATE__"
        else:
            result[idx] = wrap_subtitle(text)
    return result


def review_blocks(blocks: list[dict], translation_model: str) -> dict[str, str]:
    """번역된 자막을 검토: 미번역 잔존 여부, 비문, 용어 일관성 확인 후 교정."""
    translation_model = normalize_translation_model(translation_model)
    batch_size = _get_review_batch_size(translation_model)
    total = len(blocks)
    log.info("%s 검토 시작: %d개 블록 (배치 크기: %d)", MODEL_LABELS[translation_model], total, batch_size)
    result: dict[str, str] = {}
    prev_blocks: list[dict] = []
    for i in range(0, total, batch_size):
        batch = blocks[i:i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (total + batch_size - 1) // batch_size
        log.info(
            "%s 검토 배치 %d/%d (블록 %d~%d)",
            MODEL_LABELS[translation_model],
            batch_num,
            total_batches,
            i + 1,
            min(i + batch_size, total),
        )
        context = _format_context(result, prev_blocks, _CONTEXT_TAIL) if prev_blocks else ""
        batch_result = _review_batch(batch, translation_model, context=context)
        result.update(batch_result)
        prev_blocks = batch
    retranslate_count = sum(1 for t in result.values() if t == "__RETRANSLATE__")
    log.info(
        "%s 검토 완료: %d개 블록, 재번역 필요 %d개",
        MODEL_LABELS[translation_model],
        len(result),
        retranslate_count,
    )
    return result


def retranslate_block(ko_text: str, current_en: str, requirement: str, translation_model: str) -> str:
    translation_model = normalize_translation_model(translation_model)
    log.info("%s 개별 재번역 요청", MODEL_LABELS[translation_model])
    prompt = (
        "You are a professional Korean-to-English subtitle translator.\n"
        "Rewrite the following Korean subtitle segment as natural English subtitle text while preserving the original meaning and nuance as much as possible.\n"
        f"Korean: {ko_text}\n"
        f"Current translation: {current_en}\n"
    )
    if requirement:
        prompt += f"Additional requirement: {requirement}\n"
    prompt += (
        "\nRules:\n"
        "- Return ONLY the translated text, nothing else\n"
        "- At most 2 lines, each line no longer than 42 characters\n"
        "- If too long, rephrase or shorten only as much as needed to fit — do not just truncate\n"
        "- If you use 2 lines, break them at a natural meaning unit so the subtitle is easy to read on screen\n"
        "- Write natural spoken English suitable for subtitles\n"
        "- Prioritize fidelity to the Korean, clarity, and readability; prefer preserving nuance over aggressive shortening\n"
        "- Preserve the original meaning, tone, intent, and speaker attitude\n"
        "- Use idiomatic English when it sounds more natural\n"
        "- Do not over-translate or add information not present in the Korean\n"
        "- Avoid stiff, literary, or overly formal phrasing unless the source is clearly formal\n"
        "- Fragments are allowed when they sound natural as subtitles; do not force a full sentence\n"
        "- Keep names and key terms accurate and consistent\n"
        "- Do not leave Korean words untranslated unless they are proper nouns\n"
    )
    try:
        timeout = ANTHROPIC_RETRANSLATE_TIMEOUT if translation_model == "claude" else GEMINI_RETRANSLATE_TIMEOUT
        result = _call_llm(prompt, translation_model, timeout=timeout).strip()
    except Exception as e:
        if isinstance(e, (ConfigError, TranslationError)):
            raise
        log.error("%s 개별 재번역 실패: %s", MODEL_LABELS[translation_model], e, exc_info=True)
        raise _provider_error(e, translation_model, TranslationError) from e
    log.info("%s 개별 재번역 완료", MODEL_LABELS[translation_model])
    return wrap_subtitle(result)


def _refine_batch(blocks: list[dict], translation_model: str, context: str = "") -> dict[str, str]:
    """단일 배치 한국어 자막 다듬기."""
    texts = "\n---\n".join(f"[{b['idx']}] {b['text']}" for b in blocks)
    context_section = (
        "Previously refined segments (for context and consistency only — do NOT output these):\n"
        f"{context}\n\n"
    ) if context else ""
    prompt = (
        "You are a Korean subtitle editor preparing ASR-derived Korean subtitles for downstream English translation.\n"
        "Refine each segment so it is natural as Korean subtitles and maximally clear for accurate English translation.\n\n"
        + context_section +
        "Primary goal:\n"
        "- The refined Korean will be translated into English next\n"
        "- Remove ASR noise and ambiguity that would cause mistranslation\n"
        "- Keep every segment faithful to what the speaker actually said\n\n"
        "Fix these ASR issues:\n"
        "1. Transcription errors — homophones, similar-sounding words, and garbled phrases\n"
        "2. Filler words and stutters — remove empty hesitations such as '음', '어', '그', '아', and repeated words\n"
        "3. Sentence fragments — reconstruct them into short, coherent clauses using nearby context\n"
        "4. Spacing, punctuation, and broken word boundaries\n\n"
        "Make the Korean translation-ready:\n"
        "- Prefer clear, standard spoken Korean over messy ASR phrasing\n"
        "- If a subject, object, or referent is clearly recoverable from context, restore it briefly to reduce ambiguity for English translation\n"
        "- Keep names, titles, numbers, units, key terms, and honorific/politeness level accurate and consistent\n"
        "- Preserve speaker intent, tone, and attitude; remove only meaningless fillers, not meaningful content\n"
        "- Make each segment understandable on its own when possible, but stay concise and subtitle-friendly\n"
        "- Avoid overly literary rewriting; keep the spoken register unless the source is clearly formal\n\n"
        "Rules:\n"
        "- Keep the same [number] marker for every segment\n"
        "- Return ONLY the segments listed under 'Segments' below — do NOT re-output context segments\n"
        "- Do not omit, merge, split, reorder, explain, or comment\n"
        "- Do not translate into English\n"
        "- Preserve original meaning — do not add information not supported by the source or nearby context\n"
        "- When uncertain, make the smallest safe correction and keep the original meaning\n"
        "- Output natural, translation-ready Korean suitable for subtitles\n\n"
        "Segments:\n\n"
        f"{texts}"
    )
    try:
        response = _call_llm(prompt, translation_model)
    except Exception as e:
        if isinstance(e, (ConfigError, TranslationError)):
            raise
        log.error("%s 한국어 다듬기 API 호출 실패: %s", MODEL_LABELS[translation_model], e, exc_info=True)
        raise _provider_error(e, translation_model, TranslationError) from e
    raw = _parse_block_response(response, blocks)
    return {idx: wrap_subtitle(text) for idx, text in raw.items()}


def refine_blocks(blocks: list[dict], translation_model: str) -> dict[str, str]:
    """한국어 자막 다듬기 (ASR 오류 수정, 필러 제거)."""
    translation_model = normalize_translation_model(translation_model)
    batch_size = _get_translate_batch_size(translation_model)
    total = len(blocks)
    log.info("한국어 다듬기 시작: %d개 블록 (배치: %d, %s)", total, batch_size, MODEL_LABELS[translation_model])
    result: dict[str, str] = {}
    prev_blocks: list[dict] = []
    for i in range(0, total, batch_size):
        batch = blocks[i:i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (total + batch_size - 1) // batch_size
        log.info(
            "한국어 다듬기 배치 %d/%d (블록 %d~%d)",
            batch_num, total_batches, i + 1, min(i + batch_size, total),
        )
        context = _format_context(result, prev_blocks, _CONTEXT_TAIL) if prev_blocks else ""
        batch_result = _refine_batch(batch, translation_model, context=context)
        result.update(batch_result)
        prev_blocks = batch
    log.info("한국어 다듬기 완료: %d개 블록", len(result))
    return result


def _review_ko_batch(blocks: list[dict], translation_model: str, context: str = "") -> dict[str, str]:
    """단일 배치 한국어 자막 AI 검토."""
    texts = "\n---\n".join(f"[{b['idx']}] {b['text']}" for b in blocks)
    context_section = (
        "Previously reviewed Korean segments (for context and consistency only — do NOT output these):\n"
        f"{context}\n\n"
    ) if context else ""
    prompt = (
        "You are a Korean subtitle reviewer performing final QA on subtitles that will be translated into English next.\n"
        "Review each Korean subtitle segment and revise it only when needed so it is precise, natural, and safe for accurate English translation.\n\n"
        + context_section +
        "Check and fix:\n"
        "1. Remaining ASR mistakes, wrong word choices, or garbled phrases\n"
        "2. Ambiguous or incomplete references that can be safely clarified from nearby context\n"
        "3. Inconsistent names, titles, numbers, units, key terms, or honorific/politeness level\n"
        "4. Awkward spacing, punctuation, broken phrasing, or subtitle-unfriendly wording\n"
        "5. Fragments that are still too unclear for reliable English translation\n\n"
        "Rules:\n"
        "- Keep the same [number] marker for every segment\n"
        "- Return ONLY the segments listed under 'Segments' below — do NOT re-output context segments\n"
        "- Do not omit, merge, split, reorder, explain, or comment\n"
        "- Do not translate into English\n"
        "- Preserve the original meaning, tone, intent, and speaker attitude\n"
        "- Prefer the smallest safe edit that makes the Korean clear and translation-ready\n"
        "- If a segment is already good, keep it very close to the current wording\n"
        "- Do not add information unless it is clearly supported by the source or nearby context\n"
        "- Output natural Korean suitable for subtitles\n\n"
        "Segments:\n\n"
        f"{texts}"
    )
    try:
        response = _call_llm(prompt, translation_model)
    except Exception as e:
        if isinstance(e, (ConfigError, ReviewError)):
            raise
        log.error("%s 한국어 검토 API 호출 실패: %s", MODEL_LABELS[translation_model], e, exc_info=True)
        raise _provider_error(e, translation_model, ReviewError) from e
    raw = _parse_block_response(response, blocks)
    return {idx: wrap_subtitle(text) for idx, text in raw.items()}


def review_ko_blocks(blocks: list[dict], translation_model: str) -> dict[str, str]:
    """다듬어진 한국어 자막을 최종 검토해 번역하기 좋은 형태로 교정."""
    translation_model = normalize_translation_model(translation_model)
    batch_size = _get_review_batch_size(translation_model)
    total = len(blocks)
    log.info("%s 한국어 검토 시작: %d개 블록 (배치 크기: %d)", MODEL_LABELS[translation_model], total, batch_size)
    result: dict[str, str] = {}
    prev_blocks: list[dict] = []
    for i in range(0, total, batch_size):
        batch = blocks[i:i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (total + batch_size - 1) // batch_size
        log.info(
            "%s 한국어 검토 배치 %d/%d (블록 %d~%d)",
            MODEL_LABELS[translation_model],
            batch_num,
            total_batches,
            i + 1,
            min(i + batch_size, total),
        )
        context = _format_context(result, prev_blocks, _CONTEXT_TAIL) if prev_blocks else ""
        batch_result = _review_ko_batch(batch, translation_model, context=context)
        result.update(batch_result)
        prev_blocks = batch
    log.info("%s 한국어 검토 완료: %d개 블록", MODEL_LABELS[translation_model], len(result))
    return result
