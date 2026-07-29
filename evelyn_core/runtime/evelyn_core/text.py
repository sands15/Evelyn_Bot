import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from .config import (
    ALLOWED_OMNIVOICE_TAGS,
    EXACT_WAKE_WORD,
    MAX_VISIBLE_TEXT,
    OMNIVOICE_AUTO_EMOTION_TAGS,
    SIMILARITY_BLOCK,
    WAKE_WORDS,
)


def is_user_echo_answer(user_text: str, answer_text: str | None) -> bool:
    user_clean = clean_text(user_text)
    answer_clean = clean_text(answer_text or "")
    if not user_clean or not answer_clean:
        return False
    return user_clean == answer_clean


def clean_text(text: str) -> str:
    """여러 공백을 하나로 줄이고 앞뒤 공백을 정리한다."""
    return re.sub(r"\s+", " ", (text or "").strip())


def strip_response_action_tags(text: str) -> str:
    """내부 제어용 응답 태그([찾기]/[질문]/[대기]/[답변])는 사용자 표시/STT/TTS 경로에서 제거한다."""
    return re.sub(r"^\s*\[(?:찾기|질문|대기|답변|응답)\]\s*", "", text or "", flags=re.IGNORECASE)


def strip_model_channel_tags(text: str) -> str:
    """Gemma-style channel markers are transport metadata, not user-visible text."""
    text = text or ""
    text = re.sub(
        r"<\|channel\>\s*(?:thought|analysis|reasoning)\b.*?<channel\|>\s*<\|channel\>\s*(?:final|model|answer|content)\b\s*<channel\|>",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"<\|channel\>\s*(?:thought|analysis|reasoning)\b.*?<channel\|>\s*<\|channel\>\s*(?:final|model|answer|content)\b\s*",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"<\|channel\>\s*(?:thought|analysis|reasoning)\s*<channel\|>.*?<\|channel\>\s*(?:final|model|answer|content)\s*<channel\|>",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"<\|channel\>\s*(?:thought|analysis|reasoning)\s*<channel\|>.*?<\|channel\>\s*(?:final|model|answer|content)\s*",
        " ",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<\|channel\>\s*(?:thought|analysis|reasoning)\s*<channel\|>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<\|channel\>\s*(?:final|model|answer|content)\s*<channel\|>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<\|channel\>\s*(?:final|model|answer|content)\s*", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<channel\|>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"</?think>", " ", text, flags=re.IGNORECASE)
    return clean_text(text)


_MODEL_STREAM_ACTION_TAGS = {
    "찾기",
    "질문",
    "대기",
    "답변",
    "응답",
    "search",
    "ask",
    "wait",
    "answer",
}
_MODEL_STREAM_CHANNEL_ROLES = (
    "thought",
    "analysis",
    "reasoning",
    "final",
    "model",
    "answer",
    "content",
)


@dataclass
class ModelStreamPrefixFilter:
    """Hold unstable leading tokens until transport/action prefixes can be discarded safely."""

    pending: str = ""
    resolved: bool = False
    max_prefix_chars: int = 256

    def push(self, delta: str) -> str:
        if not delta:
            return ""
        if self.resolved:
            return delta
        self.pending += delta
        return self._drain(final=False)

    def finish(self) -> str:
        if self.resolved:
            return ""
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> str:
        while self.pending:
            self.pending = self.pending.lstrip()
            if not self.pending:
                return ""

            lowered = self.pending.lower()

            if lowered.startswith("<think>"):
                close_at = lowered.find("</think>", len("<think>"))
                if close_at < 0:
                    if final or len(self.pending) >= self.max_prefix_chars:
                        self.pending = ""
                    return ""
                self.pending = self.pending[close_at + len("</think>") :]
                continue

            if "<think>".startswith(lowered) or "</think>".startswith(lowered):
                if final:
                    self.pending = ""
                return ""

            if lowered.startswith("</think>"):
                self.pending = self.pending[len("</think>") :]
                continue

            if lowered.startswith("<channel|>"):
                self.pending = self.pending[len("<channel|>") :]
                continue
            if "<channel|>".startswith(lowered):
                if final:
                    self.pending = ""
                return ""

            channel_open = "<|channel>"
            if lowered.startswith(channel_open):
                remainder = self.pending[len(channel_open) :].lstrip()
                remainder_lower = remainder.lower()
                if not remainder:
                    if final:
                        self.pending = ""
                    return ""

                matched_role = None
                for role in _MODEL_STREAM_CHANNEL_ROLES:
                    if not remainder_lower.startswith(role):
                        continue
                    boundary = remainder[len(role) : len(role) + 1]
                    if not boundary or boundary.isspace() or boundary == "<":
                        matched_role = role
                        break
                if matched_role is not None:
                    self.pending = remainder[len(matched_role) :].lstrip()
                    continue
                if any(role.startswith(remainder_lower) for role in _MODEL_STREAM_CHANNEL_ROLES):
                    if final:
                        self.pending = ""
                    return ""

                # Unknown channel names are transport metadata as well. Drop the
                # marker itself, then let ordinary visible text resolve normally.
                self.pending = remainder
                continue

            if channel_open.startswith(lowered):
                if final:
                    self.pending = ""
                return ""

            if self.pending.startswith("["):
                close_at = self.pending.find("]")
                if close_at < 0:
                    candidate = clean_text(self.pending[1:]).lower()
                    if any(tag.startswith(candidate) for tag in _MODEL_STREAM_ACTION_TAGS):
                        if final or len(self.pending) >= self.max_prefix_chars:
                            self.pending = ""
                        return ""
                else:
                    tag = clean_text(self.pending[1:close_at]).lower()
                    if tag in _MODEL_STREAM_ACTION_TAGS:
                        self.pending = self.pending[close_at + 1 :].lstrip()
                        continue

            self.resolved = True
            output = self.pending
            self.pending = ""
            return output

        return ""


def normalize_omnivoice_tags(text: str) -> str:
    """허용된 OmniVoice 태그만 남기고 표기를 정규화한다."""
    text = strip_model_channel_tags(strip_response_action_tags(text or ""))

    def repl(match: re.Match) -> str:
        tag = f"[{clean_text(match.group(1)).lower()}]"
        return tag if tag in ALLOWED_OMNIVOICE_TAGS else ""

    return re.sub(r"\[\s*([^\[\]]+?)\s*\]", repl, text)


def strip_omnivoice_tags(text: str) -> str:
    text = normalize_omnivoice_tags(text)
    text = re.sub(r"\[[^\[\]]+\]", " ", text)
    return clean_text(text)


def has_omnivoice_tag(text: str) -> bool:
    return bool(re.search(r"\[[^\[\]]+\]", normalize_omnivoice_tags(text or "")))


def strip_tts_leading_oh(text: str) -> str:
    """TTS가 문장 앞에서 '오!' 감탄을 별도로 읽지 않도록 제거한다."""
    text = text or ""
    text = re.sub(r"^\s*(?:\[(?:question|surprise)-oh\]\s*)+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*(?:오|oh)\s*[!！,，.。…]+\s*", "", text, flags=re.IGNORECASE)
    return clean_text(text)


def infer_omnivoice_emotion_tag(text: str) -> str:
    plain = strip_omnivoice_tags(text)
    compact = plain.replace(" ", "")
    if not plain:
        return ""
    if re.search(r"(ㅋㅋ+|ㅎㅎ+|하하+|헤헤+)", plain, flags=re.IGNORECASE):
        return "[laughter]"
    if plain.rstrip().endswith(("?", "？")):
        return "[question-oh]"
    if re.search(r"(헐|진짜\?|뭐야|(?<![가-힣])어\?|와[!！]?|대박)", plain):
        return "[surprise-oh]"
    if re.search(r"(하아|에휴|아휴|흠|으음)", compact):
        return "[sigh]"
    return ""


def clean_tts_text(text: str) -> str:
    """TTS로 보내기 전 태그와 특수문자를 정리해 읽기 쉬운 문장으로 만든다."""
    text = strip_tts_leading_oh(normalize_omnivoice_tags(clean_text(strip_response_action_tags(text))))
    text = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]+", " ", text)
    text = re.sub(r"(?:\s|^)[:;=8xX]-?[)DdpP(/\\|]+\s*$", "", text)
    text = re.sub(r"(?:\s|^)(?:ㅎㅎ+|ㅋㅋ+|ㅠㅠ+|ㅜㅜ+|\^\^+|헤헤+|하하+|흐흐+)\s*$", "", text)
    text = re.sub(r"[\"'`~*_#@^|<>{}()]", "", text)
    text = clean_text(text)

    if not text:
        return ""
    if OMNIVOICE_AUTO_EMOTION_TAGS and not has_omnivoice_tag(text):
        tag = infer_omnivoice_emotion_tag(text)
        if tag:
            return strip_tts_leading_oh(clean_text(f"{tag} {text}"))
    return text


def should_suppress_tts_for_command(text: str) -> bool:
    """Return True for commands whose result is meant for display, not speech."""
    normalized = clean_text(text).lower().strip()
    return normalized in {"/", "/help", "help"}


def visible_text(text: str) -> str:
    text = strip_omnivoice_tags(strip_model_channel_tags(strip_response_action_tags(text)))
    if len(text) > MAX_VISIBLE_TEXT:
        return text[:MAX_VISIBLE_TEXT] + "..."
    return text


def normalize_voice_text(s: str) -> str:
    s = clean_text(s)
    s = re.sub(r"[^\w가-힣 ]+", "", s)
    return s


def normalized_wake_words() -> list[str]:
    exact = normalize_voice_text(EXACT_WAKE_WORD)
    if exact:
        return [exact]

    normalized = [normalize_voice_text(w) for w in WAKE_WORDS if normalize_voice_text(w)]
    if normalized:
        return [normalized[0]]
    return []


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

    head = tokens[0]
    compact = text_n.replace(" ", "")
    for wake in normalized_wake_words():
        wake_tokens = [t for t in wake.split() if t]
        if not wake_tokens:
            continue
        target = wake_tokens[0]
        wake_compact = wake.replace(" ", "")
        ratio = SequenceMatcher(None, head, target).ratio()
        compact_ratio = SequenceMatcher(None, compact, wake_compact).ratio()
        if ratio >= 0.72 or compact_ratio >= 0.78:
            return wake
    return None


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

    for wake_word in normalized_wake_words():
        if not wake_word:
            continue

        pattern_once = rf"(?:^|\s){re.escape(wake_word)}(?:\s|$)"
        new_text = re.sub(pattern_once, " ", normalize_voice_text(text_n), count=1)
        if new_text != normalize_voice_text(text_n):
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
