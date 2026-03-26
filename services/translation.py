import re
import requests
from config import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_TIMEOUT, GEMINI_RETRANSLATE_TIMEOUT
from utils.srt import wrap_subtitle
from utils.log import get_logger
from utils.errors import ConfigError, TranslationError, ReviewError

log = get_logger("translation")

_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"


def _get_api_key() -> str:
    if not GEMINI_API_KEY:
        raise ConfigError(
            detail="GEMINI_API_KEY 환경변수 없음",
            user_message="Gemini API 키가 설정되지 않았습니다. .env 파일을 확인해주세요.",
        )
    return GEMINI_API_KEY


def _call_gemini(prompt: str, timeout: int | None = None) -> str:
    if timeout is None:
        timeout = GEMINI_TIMEOUT
    resp = requests.post(
        f"{_API_URL}?key={_get_api_key()}",
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=timeout,
    )
    if not resp.ok:
        log.error("Gemini API 오류: status=%d body=%s", resp.status_code, resp.text[:500])
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]


def _parse_block_response(response: str, blocks: list[dict]) -> dict[str, str]:
    """Gemini 응답에서 [idx] 패턴으로 블록별 텍스트 추출."""
    result: dict[str, str] = {}
    for b in blocks:
        m = re.search(rf"\[{b['idx']}\]\s*(.*?)(?=\n\[|\Z)", response, re.DOTALL)
        text = m.group(1).strip() if m else b["text"]
        text = re.sub(r'\s*---\s*', '', text).strip()
        result[b["idx"]] = text
    return result


def translate_with_gemini(blocks: list[dict]) -> dict[str, str]:
    log.info("번역 시작: %d개 블록", len(blocks))
    texts = "\n---\n".join(f"[{b['idx']}] {b['text']}" for b in blocks)
    prompt = (
        "Translate the following subtitle segments to English.\n"
        "Rules:\n"
        "- Each segment is marked with [number]\n"
        "- Use natural, sophisticated English — avoid literal word-for-word translation\n"
        "- Each subtitle must be a complete sentence or complete clause — no fragments or mid-sentence breaks\n"
        "- Maintain consistent tone, terminology, and style throughout all segments\n"
        "- Ensure each segment flows coherently with the surrounding context\n"
        "- Return ONLY the translated segments with the same [number] markers\n\n"
        f"{texts}"
    )
    try:
        response = _call_gemini(prompt)
    except Exception as e:
        if isinstance(e, (ConfigError, TranslationError)):
            raise
        log.error("번역 API 호출 실패: %s", e, exc_info=True)
        raise TranslationError(detail=str(e)) from e

    raw = _parse_block_response(response, blocks)
    log.info("번역 완료: %d개 블록", len(raw))
    return {idx: wrap_subtitle(text) for idx, text in raw.items()}


def review_with_gemini(blocks: list[dict]) -> dict[str, str]:
    """번역된 자막을 검토: 미번역 잔존 여부, 비문, 용어 일관성 확인 후 교정."""
    log.info("검토 시작: %d개 블록", len(blocks))
    texts = "\n---\n".join(f"[{b['idx']}] {b['text']}" for b in blocks)
    prompt = (
        "You are a subtitle QA editor. Review and correct the following English subtitle segments.\n"
        "Fix any of these issues you find:\n"
        "1. Untranslated text: any non-English words must be translated to English\n"
        "2. Grammar: fix fragments, broken sentences, or unnatural phrasing so each segment reads as natural English\n"
        "3. Consistency: ensure the same terms, names, and style are used throughout all segments\n"
        "Rules:\n"
        "- Use natural, sophisticated English — avoid literal word-for-word translation\n"
        "- Each subtitle must be a complete sentence or complete clause — no fragments or mid-sentence breaks\n"
        "- Maintain consistent tone, terminology, and style throughout all segments\n"
        "- Ensure each segment flows coherently with the surrounding context\n"
        "- If a segment is too flawed to fix (wrong content, missing translation, or incomprehensible), "
        "output [RETRANSLATE] as its text so it can be retranslated from the source\n"
        "- Return ONLY the corrected segments with the same [number] markers, one per line\n"
        "- Do not add explanations, comments, or extra text\n\n"
        f"{texts}"
    )
    try:
        response = _call_gemini(prompt)
    except Exception as e:
        if isinstance(e, (ConfigError, ReviewError)):
            raise
        log.error("검토 API 호출 실패: %s", e, exc_info=True)
        raise ReviewError(detail=str(e)) from e

    raw = _parse_block_response(response, blocks)
    result: dict[str, str] = {}
    retranslate_count = 0
    for idx, text in raw.items():
        if re.search(r'\[RETRANSLATE\]|^RETRANSLATE$', text, re.IGNORECASE):
            result[idx] = "__RETRANSLATE__"
            retranslate_count += 1
        else:
            result[idx] = wrap_subtitle(text)
    log.info("검토 완료: %d개 블록, 재번역 필요 %d개", len(result), retranslate_count)
    return result


def retranslate_with_gemini(ko_text: str, current_en: str, requirement: str) -> str:
    log.info("개별 재번역 요청")
    prompt = (
        "Translate the following Korean subtitle segment to English.\n"
        f"Korean: {ko_text}\n"
        f"Current translation: {current_en}\n"
    )
    if requirement:
        prompt += f"Additional requirement: {requirement}\n"
    prompt += (
        "Rules:\n"
        "- Use natural, sophisticated English — avoid literal word-for-word translation\n"
        "- Must be a complete sentence or complete clause — no fragments\n"
        "- Return ONLY the translated text, nothing else\n"
    )
    try:
        result = _call_gemini(prompt, timeout=GEMINI_RETRANSLATE_TIMEOUT).strip()
    except Exception as e:
        if isinstance(e, (ConfigError, TranslationError)):
            raise
        log.error("개별 재번역 실패: %s", e, exc_info=True)
        raise TranslationError(detail=str(e)) from e
    log.info("개별 재번역 완료")
    return wrap_subtitle(result)
