import re
from difflib import SequenceMatcher

from .config import (
    ALLOWED_OMNIVOICE_TAGS,
    MAX_VISIBLE_TEXT,
    SIMILARITY_BLOCK,
    WAKE_WORDS,
)


def clean_text(text: str) -> str:
    """여러 공백을 하나로 줄이고 앞뒤 공백을 정리한다."""
    return re.sub(r"\s+", " ", (text or "").strip())


def normalize_omnivoice_tags(text: str) -> str:
    """허용된 OmniVoice 태그만 남기고 표기를 정규화한다."""
    text = text or ""

    def repl(match: re.Match) -> str:
        tag = f"[{clean_text(match.group(1)).lower()}]"
        return tag if tag in ALLOWED_OMNIVOICE_TAGS else ""

    return re.sub(r"\[\s*([^\[\]]+?)\s*\]", repl, text)


def strip_omnivoice_tags(text: str) -> str:
    text = normalize_omnivoice_tags(text)
    text = re.sub(r"\[[^\[\]]+\]", " ", text)
    return clean_text(text)


def clean_tts_text(text: str) -> str:
    """TTS로 보내기 전 태그와 특수문자를 정리해 읽기 쉬운 문장으로 만든다."""
    text = normalize_omnivoice_tags(clean_text(text))
    leading_tag_match = re.match(r"^\s*(\[[^\[\]]+\])", text)
    leading_tag = leading_tag_match.group(1) if leading_tag_match else ""

    text = re.sub(r"\[[^\[\]]+\]", " ", text)
    text = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]+", " ", text)
    text = re.sub(r"(?:\s|^)[:;=8xX]-?[)DdpP(/\\|]+\s*$", "", text)
    text = re.sub(r"(?:\s|^)(?:ㅎㅎ+|ㅋㅋ+|ㅠㅠ+|ㅜㅜ+|\^\^+|헤헤+|하하+|흐흐+)\s*$", "", text)
    text = re.sub(r"[\"'`~*_#@^|<>{}()]", "", text)
    text = clean_text(text)

    if not text:
        return ""
    if leading_tag:
        return clean_text(f"{leading_tag} {text}")
    return text


def visible_text(text: str) -> str:
    text = strip_omnivoice_tags(text)
    if len(text) > MAX_VISIBLE_TEXT:
        return text[:MAX_VISIBLE_TEXT] + "..."
    return text


def normalize_voice_text(s: str) -> str:
    s = clean_text(s)
    s = re.sub(r"[^\w가-힣 ]+", "", s)
    return s


def normalized_wake_words() -> list[str]:
    return [normalize_voice_text(w) for w in WAKE_WORDS if normalize_voice_text(w)]


def contains_wake_word(text: str) -> bool:
    text_n = normalize_voice_text(text)
    if not text_n:
        return False

    for wake in normalized_wake_words():
        if not wake:
            continue
        pattern = rf"(?:^|\s){re.escape(wake)}(?:\s|$)"
        if re.search(pattern, text_n):
            return True
    return False


def strip_leading_voice_fillers(text: str) -> str:
    text = clean_text(text)
    return re.sub(r"^(?:아+|어+|음+|흠+|저기|야|아니)[,\s]+", "", text, count=1)


def contains_leading_wake_word(text: str) -> bool:
    """호환성을 위해 남겨둔 함수. 이제 wake word는 문장 어느 위치에서든 exact match만 허용한다."""
    return contains_wake_word(text)


def extract_leading_wake_alias(text: str) -> str | None:
    text_n = normalize_voice_text(strip_leading_voice_fillers(text))
    if not text_n:
        return None

    tokens = [t for t in text_n.split() if t]
    if not tokens:
        return None

    for wake in normalized_wake_words():
        wake_tokens = [t for t in wake.split() if t]
        if wake_tokens and tokens[: len(wake_tokens)] == wake_tokens:
            return wake
    return None


def fuzzy_leading_wake_alias(text: str) -> str | None:
    text_n = normalize_voice_text(strip_leading_voice_fillers(text))
    if not text_n:
        return None

    tokens = [t for t in text_n.split() if t]
    if not tokens:
        return None

    probe = " ".join(tokens[:2]) if len(tokens) >= 2 else tokens[0]
    best_alias = None
    best_score = 0.0
    for wake in normalized_wake_words():
        score = SequenceMatcher(None, probe, wake).ratio()
        if score > best_score:
            best_score = score
            best_alias = wake
    return best_alias if best_alias is not None and best_score >= SIMILARITY_BLOCK else None


def looks_like_gibberish_probe(text: str) -> bool:
    text_n = normalize_voice_text(text)
    if not text_n:
        return True
    compact = text_n.replace(" ", "")
    if len(compact) <= 2:
        return True
    if looks_like_repetitive_noise_text(text_n):
        return True
    if len(set(compact)) <= 2 and len(compact) >= 4:
        return True
    return False


def strip_voice_wake_word(text: str) -> str:
    text_n = strip_leading_voice_fillers(text)

    for wake_word in WAKE_WORDS:
        ww = wake_word.strip()
        if not ww:
            continue

        pattern_once = rf"(?:^|\s){re.escape(ww)}(?:\s|$)"
        new_text = re.sub(pattern_once, " ", text_n, count=1)
        if new_text != text_n:
            return clean_text(new_text)

    return clean_text(text_n)


def apply_stt_post_corrections(text: str, *, wake_detected: bool = False) -> str:
    """wake word는 exact match만 허용하므로 STT 결과를 wake 기준으로 퍼지 교정하지 않는다."""
    text = clean_text(text)
    if not text:
        return text

    return text


def is_similar(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return SequenceMatcher(None, a, b).ratio() >= SIMILARITY_BLOCK


def looks_like_repetitive_noise_text(text: str) -> bool:
    """반복 토큰이 과도한 전사 결과를 잡음성 텍스트로 본다."""
    tokens = [t for t in normalize_voice_text(text).split() if t]
    if not tokens:
        return False

    longest_same_run = 1
    current_run = 1
    for prev, cur in zip(tokens, tokens[1:]):
        if prev == cur:
            current_run += 1
            longest_same_run = max(longest_same_run, current_run)
        else:
            current_run = 1

    wake_words = set(normalized_wake_words())
    if len(tokens) >= 3 and len(set(tokens)) == 1 and (tokens[0] in wake_words or contains_leading_wake_word(tokens[0])):
        return True

    if len(tokens) < 8:
        return False

    unique_ratio = len(set(tokens)) / max(1, len(tokens))
    return unique_ratio < 0.35 or longest_same_run >= 4


def looks_like_brief_filler_text(text: str) -> bool:
    """아, 어, 음 같은 짧은 필러 발화만 들어온 경우를 빠르게 걸러낸다."""
    text_n = normalize_voice_text(text)
    if not text_n:
        return True

    compact = text_n.replace(" ", "")
    if len(compact) > 6:
        return False

    return compact in {
        "아", "아아", "아아아",
        "어", "어어", "어어어",
        "응", "응응",
        "음", "으음", "음음", "음음음",
        "흠", "흠흠",
    }
