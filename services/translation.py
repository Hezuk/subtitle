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
        "You are a professional Korean-to-English subtitle translator.\n"
        "Translate each Korean subtitle segment into natural, concise English subtitles.\n\n"
        "Output rules:\n"
        "- Keep the same [number] marker for every segment\n"
        "- Translate every segment independently, but keep wording consistent across the full list\n"
        "- Return ONLY translated segments with the same [number] markers\n"
        "- Do not omit, merge, split, reorder, explain, or comment\n\n"
        "Subtitle style rules:\n"
        "- Write natural spoken English suitable for on-screen subtitles\n"
        "- Prioritize clarity, brevity, and readability over literal wording\n"
        "- Preserve the original meaning, tone, intent, and speaker attitude\n"
        "- Use idiomatic English when it sounds more natural\n"
        "- Do not over-translate or add information not present in the Korean\n"
        "- Avoid stiff, literary, or overly formal phrasing unless the source is clearly formal\n"
        "- If the Korean is casual, make the English casual; if polite, keep it politely neutral\n"
        "- Fragments are allowed when they sound natural as subtitles; do not force every line into a full sentence\n"
        "- Resolve implied subjects or objects only when necessary for natural English\n"
        "- Keep names, key terms, and repeated expressions consistent across segments\n\n"
        "Important cautions:\n"
        "- Remove filler only if it is meaningless; preserve it if it affects tone or emotion\n"
        "- Translate interjections naturally, including surprise, hesitation, sighs, and laughter\n"
        "- Do not leave Korean words untranslated unless they are proper nouns\n\n"
        "Segments:\n\n"
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
        "You are an English subtitle editor reviewing machine-translated subtitles.\n"
        "Revise each segment so it reads like polished, natural on-screen English subtitles.\n\n"
        "Check and fix:\n"
        "1. Any untranslated or partially untranslated Korean\n"
        "2. Awkward, literal, ungrammatical, or unnatural English\n"
        "3. Inconsistent names, terms, tone, or repeated phrasing\n"
        "4. Overly wordy lines that can be made shorter without losing meaning\n\n"
        "Rules:\n"
        "- Keep the same [number] marker for every segment\n"
        "- Return ONLY the corrected segments with the same [number] markers\n"
        "- Do not omit, merge, split, reorder, explain, or comment\n"
        "- Use concise, natural spoken English suitable for subtitles\n"
        "- Preserve meaning, tone, intent, and speaker attitude\n"
        "- Fragments are allowed when natural for subtitles; do not force every line into a full sentence\n"
        "- Keep names, key terms, and repeated expressions consistent across segments\n"
        "- If a segment is too wrong, too incomplete, or too unclear to repair safely, output [RETRANSLATE]\n\n"
        "Segments:\n\n"
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
        "You are a professional Korean-to-English subtitle translator.\n"
        "Rewrite the following Korean subtitle segment as natural, concise English subtitle text.\n"
        f"Korean: {ko_text}\n"
        f"Current translation: {current_en}\n"
    )
    if requirement:
        prompt += f"Additional requirement: {requirement}\n"
    prompt += (
        "\nRules:\n"
        "- Return ONLY the translated text, nothing else\n"
        "- Write natural spoken English suitable for subtitles\n"
        "- Prioritize clarity, brevity, and readability over literal wording\n"
        "- Preserve the original meaning, tone, intent, and speaker attitude\n"
        "- Use idiomatic English when it sounds more natural\n"
        "- Do not over-translate or add information not present in the Korean\n"
        "- Avoid stiff, literary, or overly formal phrasing unless the source is clearly formal\n"
        "- Fragments are allowed when they sound natural as subtitles; do not force a full sentence\n"
        "- Keep names and key terms accurate and consistent\n"
        "- Do not leave Korean words untranslated unless they are proper nouns\n"
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
