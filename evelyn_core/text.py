import re
from difflib import SequenceMatcher

from .config import (
    ALLOWED_OMNIVOICE_TAGS,
    MAX_VISIBLE_TEXT,
    SIMILARITY_BLOCK,
    WAKE_FUZZY_THRESHOLD,
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
    return any(w in text_n for w in normalized_wake_words())


def strip_leading_voice_fillers(text: str) -> str:
    text = clean_text(text)
    return re.sub(r"^(?:아+|어+|음+|흠+|저기|야|아니)[,\s]+", "", text, count=1)


def contains_leading_wake_word(text: str) -> bool:
    """문장 맨 앞부분이 wake word로 시작했는지 fuzzy matching까지 포함해 판별한다."""
    text_n = normalize_voice_text(text)
    if not text_n:
        return False

    prefixes: list[str] = []
    tokens = text_n.split()
    if tokens:
        prefixes.append(tokens[0])
        prefixes.append("".join(tokens[:2]))
    prefixes.append(text_n[: max(8, min(len(text_n), 14))])

    wake_words = normalized_wake_words()
    for prefix in prefixes:
        prefix = clean_text(prefix)
        if not prefix:
            continue
        if any(w in prefix or prefix in w for w in wake_words):
            return True
        for wake in wake_words:
            if SequenceMatcher(None, prefix[: len(wake) + 2], wake).ratio() >= WAKE_FUZZY_THRESHOLD:
                return True

    return False


def strip_voice_wake_word(text: str) -> str:
    text_n = strip_leading_voice_fillers(text)

    for wake_word in WAKE_WORDS:
        ww = wake_word.strip()
        if not ww:
            continue

        pattern_front = rf"^\s*{re.escape(ww)}[야아]?\s*[, ]*"
        new_text = re.sub(pattern_front, "", text_n, count=1)
        if new_text != text_n:
            return clean_text(new_text)

        pattern_once = rf"{re.escape(ww)}[야아]?"
        new_text = re.sub(pattern_once, "", text_n, count=1)
        if new_text != text_n:
            return clean_text(new_text)

    return clean_text(text_n)


def apply_stt_post_corrections(text: str, *, wake_detected: bool = False) -> str:
    """STT 결과에서 자주 틀리는 wake word 표기를 canonical 형태로 교정한다."""
    text = clean_text(text)
    if not text:
        return text

    leading_fillers = ""
    filler_match = re.match(r"^((?:아+|어+|음+|흠+|저기|야|아니)[,\s]+)", text)
    if filler_match:
        leading_fillers = filler_match.group(1)
        text = text[len(leading_fillers):].lstrip()

    wake_variants = [
        "이블린", "이불린", "이브린", "이벨린", "이벌린", "에블린", "에브린",
        "이블리", "이별인", "이별린", "이벨링", "에벌린", "입을린",
    ]

    for variant in wake_variants:
        pattern_front = rf"^{re.escape(variant)}(?:[아야])?(?=(?:[,.!?\s]|$))[,.!?\s]*"
        if re.match(pattern_front, text):
            rest = clean_text(re.sub(pattern_front, "", text, count=1))
            normalized = clean_text(f"이블린 {rest}" if rest else "이블린")
            return clean_text(f"{leading_fillers}{normalized}" if leading_fillers else normalized)

    return clean_text(f"{leading_fillers}{text}" if leading_fillers else text)


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
