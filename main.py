import audioop
import json
import os
import queue
import re
import time
import asyncio
from difflib import SequenceMatcher
from pathlib import Path
from typing import Awaitable, Callable, Optional

import aiohttp
import numpy as np
import torch
import discord
from discord.ext import commands
from transformers import AutoProcessor, CohereAsrForConditionalGeneration

try:
    import torchaudio.functional as torchaudio_F
except Exception:
    torchaudio_F = None

from evelyn_voice import EvelynVoiceClient


# =========================================================
# 기본 설정
# =========================================================
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

LLM_SERVER_URL = os.getenv("LLM_SERVER_URL", "http://127.0.0.1:9820/v1/chat/completions")
MODEL_NAME = os.getenv("LLM_MODEL_NAME", "Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q8_0.gguf")

OMNIVOICE_SERVER_URL = os.getenv("OMNIVOICE_SERVER_URL", "http://127.0.0.1:8880")
OMNIVOICE_MODEL = os.getenv("OMNIVOICE_MODEL", "omnivoice")
OMNIVOICE_VOICE = os.getenv("OMNIVOICE_VOICE", "clone:evelyn")
OMNIVOICE_LANGUAGE = os.getenv("OMNIVOICE_LANGUAGE", "ko")
OMNIVOICE_STREAM = os.getenv("OMNIVOICE_STREAM", "true").lower() == "true"
OMNIVOICE_TIMEOUT_SEC = float(os.getenv("OMNIVOICE_TIMEOUT_SEC", "180"))

SUMMARY_LLM_URL = os.getenv("SUMMARY_LLM_URL", "http://127.0.0.1:9821/v1/chat/completions")
SUMMARY_MODEL_NAME = os.getenv("SUMMARY_MODEL_NAME", "Qwen2.5-1.5B-Instruct-Q8_0.gguf")
MEMORY_ROOT = Path(os.getenv("BOT_MEMORY_DIR", str(Path(__file__).resolve().parent / "bot_memory")))
MEMORY_FACT_LIMIT = int(os.getenv("MEMORY_FACT_LIMIT", "200"))
MEMORY_LOOP_LIMIT = int(os.getenv("MEMORY_LOOP_LIMIT", "100"))
MEMORY_RETRIEVE_LIMIT = int(os.getenv("MEMORY_RETRIEVE_LIMIT", "8"))

STT_MODEL_NAME = os.getenv("STT_MODEL_NAME", "CohereLabs/cohere-transcribe-03-2026")
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "ko")
STT_COMPUTE_TYPE = os.getenv("STT_COMPUTE_TYPE", "float16")

VAD_ENABLED = os.getenv("VAD_ENABLED", "true").lower() == "true"
VAD_RMS_THRESHOLD = float(os.getenv("VAD_RMS_THRESHOLD", "0.008"))
VAD_PEAK_THRESHOLD = float(os.getenv("VAD_PEAK_THRESHOLD", "0.020"))
VAD_MIN_VOICED_RATIO = float(os.getenv("VAD_MIN_VOICED_RATIO", "0.015"))
VAD_CHUNK_MS = float(os.getenv("VAD_CHUNK_MS", "32"))
VAD_START_CONSECUTIVE = int(os.getenv("VAD_START_CONSECUTIVE", "2"))

DENOISE_ENABLED = os.getenv("DENOISE_ENABLED", "true").lower() == "true"
DENOISE_HIGHPASS_HZ = float(os.getenv("DENOISE_HIGHPASS_HZ", "120"))
DENOISE_NOISE_FLOOR_SEC = float(os.getenv("DENOISE_NOISE_FLOOR_SEC", "0.20"))
DENOISE_GATE_MULT = float(os.getenv("DENOISE_GATE_MULT", "1.35"))
WAKE_AUDIO_SEC = float(os.getenv("WAKE_AUDIO_SEC", "1.4"))
WAKE_MAX_TOKENS = int(os.getenv("WAKE_MAX_TOKENS", "48"))
WAKE_FUZZY_THRESHOLD = float(os.getenv("WAKE_FUZZY_THRESHOLD", "0.72"))
WAKE_SHORT_TEXT_KEEP_LEN = int(os.getenv("WAKE_SHORT_TEXT_KEEP_LEN", "2"))
TTS_EARLY_CHUNK_LEN = int(os.getenv("TTS_EARLY_CHUNK_LEN", "24"))
TTS_EARLY_CUT_MIN = int(os.getenv("TTS_EARLY_CUT_MIN", "12"))

MAX_HISTORY_ITEMS = 1024
MAX_VISIBLE_TEXT = 1800
AUTO_JOIN_VOICE = os.getenv("AUTO_JOIN_VOICE", "true").lower() == "true"

MIN_TEXT_LEN = int(os.getenv("VOICE_MIN_TEXT_LEN", "4"))
MIN_TRANSCRIBED_LEN = int(os.getenv("VOICE_MIN_TRANSCRIBED_LEN", "6"))
MIN_AUDIO_SEC = float(os.getenv("VOICE_MIN_AUDIO_SEC", "0.6"))
REPLY_COOLDOWN_SEC = float(os.getenv("VOICE_REPLY_COOLDOWN_SEC", "2.5"))
POST_TTS_IGNORE_SEC = float(os.getenv("VOICE_POST_TTS_IGNORE_SEC", "1.2"))
SIMILARITY_BLOCK = float(os.getenv("VOICE_SIMILARITY_BLOCK", "0.88"))
WAKE_WORDS = [
    w.strip()
    for w in os.getenv(
        "WAKE_WORDS",
        "이별인,이별링,이벨링,에벌링,이블린,이불린,이불링,이브린,이브링,입을린,입을링,이블닝,이블링,이별린,이벌린,에블린,에브린,에블링,에브링,에벌린,이벨린,이반린,불리읍,이블리"
    ).split(",")
    if w.strip()
]
RATE = 48000
CHANNELS = 2
TARGET_RATE = 16000
DISCORD_PCM_RATE = 48000
DISCORD_PCM_CHANNELS = 2
OMNIVOICE_PCM_RATE = 24000
OMNIVOICE_PCM_CHANNELS = 1
DISCORD_FRAME_BYTES = 3840
# =========================================================
# 봇 설정
# =========================================================
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.voice_states = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

conversation_history = [
    {
        "role": "system",
        "content": (
            "너는 사용자의 친구 이블린이야, 나이는 20살이고 친절하고 상냥해 "
            "항상 자연스러운 한국어로만 답한다. "
            "반드시 최종 답변만 바로 출력한다. "
            "<think>, reasoning, thinking process, memo, bullet, 사용자 분석, 초안은 절대 출력하지 않는다. "
            "질문에는 한 문장 또는 두 문장으로 짧고 자연스럽게 답한다."
        ),
    }
]

guild_locks: dict[int, asyncio.Lock] = {}
tts_lock = asyncio.Lock()

stt_processor: Optional[AutoProcessor] = None
stt_model: Optional[CohereAsrForConditionalGeneration] = None
http_session: Optional[aiohttp.ClientSession] = None

last_voice_reply_at: dict[int, float] = {}
last_voice_text: dict[int, str] = {}
last_bot_audio_end_at: dict[int, float] = {}
bot_speaking_guilds: set[int] = set()
memory_locks: dict[int, asyncio.Lock] = {}


# =========================================================
# 유틸
# =========================================================
def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def visible_text(text: str) -> str:
    text = clean_text(text)
    if len(text) > MAX_VISIBLE_TEXT:
        return text[:MAX_VISIBLE_TEXT] + "..."
    return text


def trim_history() -> None:
    global conversation_history
    if len(conversation_history) > 1 + MAX_HISTORY_ITEMS:
        conversation_history = [conversation_history[0]] + conversation_history[-MAX_HISTORY_ITEMS:]


def append_history(user_text: str, answer: str) -> None:
    conversation_history.append({"role": "user", "content": clean_text(user_text)})
    conversation_history.append({"role": "assistant", "content": clean_text(answer)})
    trim_history()


def guild_memory_dir(guild_id: int) -> Path:
    path = MEMORY_ROOT / f"guild_{guild_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def memory_summary_path(guild_id: int) -> Path:
    return guild_memory_dir(guild_id) / "rolling_summary.txt"


def memory_facts_path(guild_id: int) -> Path:
    return guild_memory_dir(guild_id) / "durable_facts.jsonl"


def memory_loops_path(guild_id: int) -> Path:
    return guild_memory_dir(guild_id) / "open_loops.jsonl"


def read_text_file(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore").strip()


def write_text_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(clean_text(text), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []

    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    if content:
        content += "\n"
    path.write_text(content, encoding="utf-8")


def append_unique_memory_rows(path: Path, rows: list[dict], limit: int) -> None:
    existing = read_jsonl(path)
    seen = {clean_text(str(row.get("text", ""))) for row in existing}

    for row in rows:
        text = clean_text(str(row.get("text", "")))
        if len(text) < 2 or text in seen:
            continue
        seen.add(text)
        existing.append({
            "text": text,
            "type": clean_text(str(row.get("type", "memory"))) or "memory",
            "saved_at": int(time.time()),
        })

    if len(existing) > limit:
        existing = existing[-limit:]

    write_jsonl(path, existing)


def memory_tokens(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z0-9_]+|[가-힣]{2,}", clean_text(text).lower()))


def select_relevant_memory_rows(query: str, rows: list[dict], limit: int) -> list[dict]:
    q = memory_tokens(query)
    if not rows:
        return []

    scored: list[tuple[int, int, dict]] = []
    for index, row in enumerate(rows):
        text = clean_text(str(row.get("text", "")))
        if not text:
            continue
        score = len(q & memory_tokens(text))
        recency = int(row.get("saved_at", index))
        scored.append((score, recency, row))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selected = [row for score, _, row in scored if score > 0][:limit]
    if selected:
        return selected
    return [row for _, _, row in scored[:limit]]


def build_memory_context(guild_id: int, user_text: str) -> str:
    summary = read_text_file(memory_summary_path(guild_id))
    facts = select_relevant_memory_rows(user_text, read_jsonl(memory_facts_path(guild_id)), MEMORY_RETRIEVE_LIMIT)
    loops = select_relevant_memory_rows(user_text, read_jsonl(memory_loops_path(guild_id)), 4)

    parts: list[str] = []
    if summary:
        parts.append(f"최근 누적 요약:\n{summary}")
    if facts:
        parts.append(
            "장기 기억 후보:\n" + "\n".join(f"- {clean_text(str(row.get('text', '')))}" for row in facts)
        )
    if loops:
        parts.append(
            "열린 작업/보류 메모:\n" + "\n".join(f"- {clean_text(str(row.get('text', '')))}" for row in loops)
        )

    if not parts:
        return ""

    return (
        "다음은 이전 대화에서 정리한 참고 메모다. 사실처럼 단정하지 말고, 현재 질문과 맞는 경우에만 자연스럽게 반영해라.\n\n"
        + "\n\n".join(parts)
    )


def extract_json_object(text: str) -> dict:
    text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    return {}


async def ask_summary_llm(messages: list[dict]) -> dict:
    session = await get_http_session()
    payload = {
        "model": SUMMARY_MODEL_NAME,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 500,
        "stream": False,
    }
    timeout = aiohttp.ClientTimeout(total=90)

    async with session.post(SUMMARY_LLM_URL, json=payload, timeout=timeout) as resp:
        if resp.status != 200:
            error_text = await resp.text()
            raise RuntimeError(f"요약 LLM 서버 오류: {resp.status} / {error_text[:300]}")

        data = await resp.json()
        choices = data.get("choices", [])
        if not choices:
            return {}

        msg = choices[0].get("message", {})
        text = clean_text(msg.get("content", "") or msg.get("reasoning_content", ""))
        return extract_json_object(text)


async def update_long_term_memory(guild_id: int, user_text: str, answer: str) -> None:
    lock = memory_locks.setdefault(guild_id, asyncio.Lock())
    async with lock:
        current_summary = read_text_file(memory_summary_path(guild_id))
        recent_facts = read_jsonl(memory_facts_path(guild_id))[-12:]
        recent_loops = read_jsonl(memory_loops_path(guild_id))[-8:]

        messages = [
            {
                "role": "system",
                "content": (
                    "너는 대화 장기기억 관리자다. 반드시 JSON 객체 하나만 출력한다. "
                    "형식은 {\"summary_update\": string, \"durable_facts\": [{\"type\": string, \"text\": string}], \"open_loops\": [{\"type\": string, \"text\": string}]}. "
                    "durable_facts에는 오래 기억할 만한 선호, 설정, 프로젝트 결정, 반복되는 사실만 넣어라. "
                    "잡담, 일회성 문장, 추측, 노이즈는 넣지 마라. open_loops에는 아직 끝나지 않은 작업이나 다음에 이어야 할 일만 넣어라. "
                    "summary_update는 지금까지 맥락을 짧게 압축한 한국어 요약으로 작성해라."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"현재 요약:\n{current_summary or '(없음)'}\n\n"
                    f"최근 durable_facts:\n{json.dumps(recent_facts, ensure_ascii=False)}\n\n"
                    f"최근 open_loops:\n{json.dumps(recent_loops, ensure_ascii=False)}\n\n"
                    f"새 대화:\nuser: {clean_text(user_text)}\nassistant: {clean_text(answer)}"
                ),
            },
        ]

        try:
            result = await ask_summary_llm(messages)
        except Exception as e:
            print(f"[MEMORY] 요약 업데이트 실패: {e}")
            return

        summary_update = clean_text(str(result.get("summary_update", "")))
        if summary_update:
            write_text_file(memory_summary_path(guild_id), summary_update)

        durable_facts = result.get("durable_facts", [])
        if isinstance(durable_facts, list):
            append_unique_memory_rows(memory_facts_path(guild_id), [row for row in durable_facts if isinstance(row, dict)], MEMORY_FACT_LIMIT)

        open_loops = result.get("open_loops", [])
        if isinstance(open_loops, list):
            append_unique_memory_rows(memory_loops_path(guild_id), [row for row in open_loops if isinstance(row, dict)], MEMORY_LOOP_LIMIT)


def schedule_memory_update(guild_id: int, user_text: str, answer: str) -> None:
    asyncio.create_task(update_long_term_memory(guild_id, user_text, answer))


def clean_tts_text(text: str) -> str:
    text = clean_text(text)
    text = re.sub(r"[\"'`~*_#@^|<>\[\]{}()]", "", text)
    return text


def split_tts_sentences(buffer: str, *, force: bool = False) -> tuple[list[str], str]:
    working = buffer or ""
    chunks: list[str] = []

    while True:
        match = re.search(r"(.+?[.!?…。]+)(?:\s+|$)", working, flags=re.DOTALL)
        if not match:
            break

        sentence = clean_tts_text(match.group(1))
        if sentence:
            chunks.append(sentence)
        working = working[match.end():].lstrip()

    if not force:
        compact = clean_text(working)
        if len(compact) >= TTS_EARLY_CHUNK_LEN:
            cut = max(working.rfind(" "), working.rfind(","), working.rfind("，"))
            if cut >= TTS_EARLY_CUT_MIN:
                sentence = clean_tts_text(working[:cut])
                if sentence:
                    chunks.append(sentence)
                working = working[cut + 1 :].lstrip()
        return chunks, working

    tail = clean_tts_text(working)
    if tail:
        chunks.append(tail)
    return chunks, ""


def sanitize_model_output(text: str) -> str:
    text = text or ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<think>.*$", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = clean_text(text)
    return text


def strip_markdown_noise(text: str) -> str:
    text = re.sub(r"[*_`#]+", "", text)
    text = re.sub(r"^[\-\*\d\.\)\s]+", "", text)
    return clean_text(text)


def looks_like_meta_line(text: str) -> bool:
    t = text.strip().lower()
    blocked_prefixes = (
        "thinking process",
        "analyze the request",
        "determine the",
        "draft",
        "option",
        "selecting",
        "refining",
        "final polish",
        "final output generation",
        "role:",
        "language:",
        "format:",
        "length:",
        "tone:",
        "content:",
        "input:",
        "wait",
        "let's",
        "let’s",
        "or:",
    )
    if any(t.startswith(p) for p in blocked_prefixes):
        return True
    if "**" in text:
        return True
    if len(re.findall(r"[A-Za-z]", text)) > len(re.findall(r"[가-힣]", text)) * 2:
        return True
    return False


def extract_answer_from_reasoning(reasoning: str, user_text: str) -> str:
    text = sanitize_model_output(reasoning)
    if not text:
        return ""

    text = text.replace("\r", "\n")
    candidates: list[str] = []

    quoted = re.findall(r"[\"“”'‘’]([^\"“”'‘’]{4,120})[\"“”'‘’]", text)
    for q in quoted:
        q = strip_markdown_noise(q)
        if not q or not re.search(r"[가-힣]", q):
            continue
        if looks_like_meta_line(q):
            continue
        if clean_text(q) == clean_text(user_text):
            continue
        candidates.append(q)

    for raw_line in text.splitlines():
        line = strip_markdown_noise(raw_line)
        if not line:
            continue
        if not re.search(r"[가-힣]", line):
            continue
        if looks_like_meta_line(line):
            continue
        if clean_text(line) == clean_text(user_text):
            continue
        candidates.append(line)

    sentence_candidates = re.findall(r"[가-힣0-9 ,~…?!\.]+[?!\.]", text)
    for s in sentence_candidates:
        s = strip_markdown_noise(s)
        if not s:
            continue
        if looks_like_meta_line(s):
            continue
        if clean_text(s) == clean_text(user_text):
            continue
        candidates.append(s)

    seen = set()
    filtered: list[str] = []
    for c in candidates:
        c = clean_text(c).strip("\"'“”‘’")
        if not c or c in seen:
            continue
        seen.add(c)
        if len(c) < 6 or len(c) > 120:
            continue
        if clean_text(user_text) in c and len(c) <= len(clean_text(user_text)) + 6:
            continue
        filtered.append(c)

    if not filtered:
        return ""

    for c in reversed(filtered):
        if re.search(r"[가-힣]", c):
            return c

    return filtered[-1]


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


def contains_leading_wake_word(text: str) -> bool:
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
    text_n = clean_text(text)

    for wake_word in WAKE_WORDS:
        ww = wake_word.strip()
        if not ww:
            continue

        # 맨 앞 호출 제거: "이블린", "이블린아", "이블린,"
        pattern_front = rf"^\s*{re.escape(ww)}[야아]?\s*[, ]*"
        new_text = re.sub(pattern_front, "", text_n, count=1)
        if new_text != text_n:
            text_n = clean_text(new_text)
            return text_n or "부르셨나요?"

        # 문장 중 첫 1회 제거
        pattern_once = rf"{re.escape(ww)}[야아]?"
        new_text = re.sub(pattern_once, "", text_n, count=1)
        if new_text != text_n:
            text_n = clean_text(new_text)
            return text_n or "부르셨나요?"

    return text_n or "부르셨나요?"

def apply_stt_post_corrections(text: str, *, wake_detected: bool = False) -> str:
    text = clean_text(text)
    if not text:
        return text

    corrections = [
        (r"\b이\s*블\s*린\b", "이블린"),
        (r"\b이\s*브\s*린\b", "이블린"),
        (r"\b에\s*블\s*린\b", "에블린"),
        (r"\b에\s*브\s*린\b", "에브린"),
        (r"\b이\s*벨\s*린\b", "이벨린"),
        (r"\b이\s*벌\s*린\b", "이벌린"),
    ]

    corrected = text
    for pattern, replacement in corrections:
        corrected = re.sub(pattern, replacement, corrected, flags=re.IGNORECASE)

    corrected = clean_text(corrected)

    if wake_detected:
        for wake_word in WAKE_WORDS:
            ww = wake_word.strip()
            if not ww:
                continue
            pattern_front = rf"^\s*{re.escape(ww)}[아야]?\s*[, ]*"
            if re.match(pattern_front, corrected):
                rest = clean_text(re.sub(pattern_front, "", corrected, count=1))
                return clean_text(f"이블린 {rest}" if rest else "이블린")

    return corrected

def is_similar(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return SequenceMatcher(None, a, b).ratio() >= SIMILARITY_BLOCK

def should_ignore_short_transcription(
    text: str,
    pcm_bytes: bytes,
    *,
    wake_detected: bool = False,
) -> bool:
    text_n = normalize_voice_text(text)
    if not text_n:
        return True

    if text_n in normalized_wake_words():
        return False

    if wake_detected and len(text_n) >= WAKE_SHORT_TEXT_KEEP_LEN:
        return False

    audio_sec = len(pcm_bytes) / (RATE * CHANNELS * 2)
    if audio_sec < MIN_AUDIO_SEC and len(text_n) < MIN_TRANSCRIBED_LEN:
        return True

    return False


def should_reply_to_voice(guild_id: int, text: str, *, wake_detected: bool = False) -> tuple[bool, str]:
    now = time.monotonic()
    text_n = normalize_voice_text(text)

    if guild_id in bot_speaking_guilds:
        return False, "bot_is_speaking"

    if now - last_bot_audio_end_at.get(guild_id, 0.0) < POST_TTS_IGNORE_SEC:
        return False, "post_tts_ignore"

    if not text_n:
        return False, "empty"

    if not wake_detected and not contains_wake_word(text_n):
        return False, "no_wake_word"

    if len(text_n) < MIN_TEXT_LEN and not wake_detected:
        return False, "too_short"

    if now - last_voice_reply_at.get(guild_id, 0.0) < REPLY_COOLDOWN_SEC:
        return False, "cooldown"

    if is_similar(text_n, last_voice_text.get(guild_id, "")):
        return False, "duplicate"

    last_voice_text[guild_id] = text_n
    last_voice_reply_at[guild_id] = now
    return True, "ok"


def downmix_and_resample_int16_stereo_to_mono16k(pcm_bytes: bytes) -> np.ndarray:
    audio = np.frombuffer(pcm_bytes, dtype=np.int16)
    if audio.size == 0:
        return np.zeros(0, dtype=np.float32)

    if CHANNELS == 2:
        audio = audio.reshape(-1, 2).mean(axis=1)

    audio = audio.astype(np.float32) / 32768.0

    if RATE != TARGET_RATE:
        ratio = TARGET_RATE / RATE
        new_len = max(1, int(len(audio) * ratio))
        x_old = np.linspace(0, 1, len(audio), endpoint=False)
        x_new = np.linspace(0, 1, new_len, endpoint=False)
        audio = np.interp(x_new, x_old, audio).astype(np.float32)

    return audio


def apply_light_denoise(audio16k: np.ndarray) -> np.ndarray:
    if not DENOISE_ENABLED or audio16k.size == 0:
        return audio16k

    audio = np.asarray(audio16k, dtype=np.float32).copy()

    if torchaudio_F is not None:
        try:
            tensor = torch.from_numpy(audio)
            tensor = torchaudio_F.highpass_biquad(tensor, TARGET_RATE, DENOISE_HIGHPASS_HZ)
            audio = tensor.cpu().numpy().astype(np.float32)
        except Exception:
            pass

    noise_len = min(len(audio), max(1, int(TARGET_RATE * DENOISE_NOISE_FLOOR_SEC)))
    noise_sample = np.abs(audio[:noise_len]) if noise_len > 0 else np.abs(audio)
    base_floor = float(np.percentile(noise_sample, 65)) if noise_sample.size else 0.0
    global_floor = float(np.percentile(np.abs(audio), 20)) if audio.size else 0.0
    threshold = max(base_floor, global_floor * 0.85, 0.0015) * DENOISE_GATE_MULT

    abs_audio = np.abs(audio)
    gain = np.ones_like(audio, dtype=np.float32)
    below = abs_audio < threshold
    if np.any(below):
        gain[below] = np.clip(abs_audio[below] / max(threshold, 1e-6), 0.12, 1.0)
        audio[below] *= gain[below]

    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 0:
        audio = np.clip(audio * min(1.6, 0.98 / peak), -1.0, 1.0)

    return audio.astype(np.float32)


def prepare_stt_audio(pcm_bytes: bytes) -> np.ndarray:
    audio16k = downmix_and_resample_int16_stereo_to_mono16k(pcm_bytes)
    if audio16k.size == 0:
        return audio16k
    return apply_light_denoise(audio16k)


def slice_audio_window(audio16k: np.ndarray, max_sec: float) -> np.ndarray:
    if audio16k.size == 0 or max_sec <= 0:
        return audio16k
    sample_len = max(1, int(TARGET_RATE * max_sec))
    return audio16k[:sample_len].copy()


def _is_voiced_vad_chunk(chunk: np.ndarray) -> bool:
    if chunk.size == 0:
        return False

    abs_chunk = np.abs(chunk)
    rms = float(np.sqrt(np.mean(np.square(chunk))))
    voiced_ratio = float(np.mean(abs_chunk > VAD_PEAK_THRESHOLD))
    return rms >= VAD_RMS_THRESHOLD and voiced_ratio >= VAD_MIN_VOICED_RATIO


def is_probably_silent(audio16k: np.ndarray) -> bool:
    if audio16k.size == 0:
        return True

    chunk_samples = max(1, int(TARGET_RATE * (VAD_CHUNK_MS / 1000.0)))
    required_streak = max(1, VAD_START_CONSECUTIVE)
    voiced_streak = 0

    for start in range(0, len(audio16k), chunk_samples):
        chunk = audio16k[start:start + chunk_samples]
        if _is_voiced_vad_chunk(chunk):
            voiced_streak += 1
            if voiced_streak >= required_streak:
                return False
        else:
            voiced_streak = 0

    return True

def log_visible_gpus() -> None:
    print("CUDA available:", torch.cuda.is_available())
    print("CUDA device count:", torch.cuda.device_count())
    if torch.cuda.is_available():
        for i in range(torch.cuda.device_count()):
            print(f"GPU {i}: {torch.cuda.get_device_name(i)}")


async def get_http_session() -> aiohttp.ClientSession:
    global http_session
    if http_session is None or http_session.closed:
        timeout = aiohttp.ClientTimeout(total=None, connect=10, sock_connect=10)
        http_session = aiohttp.ClientSession(timeout=timeout)
    return http_session


class OmniVoicePCMStream(discord.AudioSource):
    def __init__(self):
        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._buffer = bytearray()
        self._done = False
        self._closed = False
        self._rate_state = None
        self._input_remainder = b""
        self.error: Exception | None = None

    def feed_pcm24_mono(self, chunk: bytes) -> None:
        if self._closed or not chunk:
            return

        pcm = self._input_remainder + chunk
        if len(pcm) % 2 == 1:
            self._input_remainder = pcm[-1:]
            pcm = pcm[:-1]
        else:
            self._input_remainder = b""

        if not pcm:
            return

        upsampled, self._rate_state = audioop.ratecv(
            pcm,
            2,
            OMNIVOICE_PCM_CHANNELS,
            OMNIVOICE_PCM_RATE,
            DISCORD_PCM_RATE,
            self._rate_state,
        )
        stereo = audioop.tostereo(upsampled, 2, 1, 1)
        if stereo:
            self._queue.put(stereo)

    def finish(self) -> None:
        self._done = True
        self._queue.put(None)

    def fail(self, err: Exception) -> None:
        self.error = err
        self.finish()

    def read(self) -> bytes:
        while len(self._buffer) < DISCORD_FRAME_BYTES:
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                if self._done:
                    break
                continue

            if item is None:
                self._done = True
                break

            self._buffer.extend(item)

        if len(self._buffer) >= DISCORD_FRAME_BYTES:
            chunk = bytes(self._buffer[:DISCORD_FRAME_BYTES])
            del self._buffer[:DISCORD_FRAME_BYTES]
            return chunk

        if self._done and self._buffer:
            chunk = bytes(self._buffer)
            self._buffer.clear()
            return chunk + (b"\x00" * (DISCORD_FRAME_BYTES - len(chunk)))

        return b""

    def cleanup(self) -> None:
        self._closed = True
        self._done = True
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass


async def warmup_tts_server() -> None:
    session = await get_http_session()
    timeout = aiohttp.ClientTimeout(total=10)
    async with session.get(f"{OMNIVOICE_SERVER_URL}/health", timeout=timeout) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"OmniVoice health check 실패: {resp.status} / {text[:200]}")
        print("OmniVoice 서버 준비 확인 완료")


def log_voice_latency(metrics: dict | None, key: str, label: str) -> None:
    if not metrics or metrics.get(key):
        return

    started_at = metrics.get("started_at")
    if started_at is None:
        return

    elapsed_ms = (time.monotonic() - float(started_at)) * 1000.0
    metrics[key] = True
    print(f"[VOICE LATENCY] {label}: {elapsed_ms:.0f}ms")


async def create_omnivoice_source(
    text: str,
    *,
    on_first_byte: Callable[[], None] | None = None,
) -> OmniVoicePCMStream:
    text = clean_tts_text(text)
    if not text:
        raise ValueError("TTS 텍스트가 비어 있습니다.")

    source = OmniVoicePCMStream()

    async def producer() -> None:
        session = await get_http_session()
        timeout = aiohttp.ClientTimeout(total=OMNIVOICE_TIMEOUT_SEC)

        async def stream_with_voice(voice_name: str) -> tuple[bool, str]:
            payload = {
                "model": OMNIVOICE_MODEL,
                "input": text,
                "voice": voice_name,
                "response_format": "pcm",
                "stream": OMNIVOICE_STREAM,
            }
            if OMNIVOICE_LANGUAGE:
                payload["language"] = OMNIVOICE_LANGUAGE

            first_byte_logged = False

            async with session.post(
                f"{OMNIVOICE_SERVER_URL}/v1/audio/speech",
                json=payload,
                timeout=timeout,
            ) as resp:
                if resp.status != 200:
                    return False, await resp.text()

                async for chunk in resp.content.iter_chunked(8192):
                    if chunk:
                        if on_first_byte is not None and not first_byte_logged:
                            on_first_byte()
                            first_byte_logged = True
                        source.feed_pcm24_mono(chunk)
                return True, ""

        try:
            ok, error_text = await stream_with_voice(OMNIVOICE_VOICE)
            if not ok:
                if OMNIVOICE_VOICE.startswith("clone:"):
                    print(f"[TTS FALLBACK] clone voice 실패 -> auto 사용 | voice={OMNIVOICE_VOICE} err={error_text[:200]}")
                    ok, error_text = await stream_with_voice("auto")
                if not ok:
                    raise RuntimeError(f"OmniVoice 서버 오류: {error_text[:300]}")
        except Exception as e:
            source.fail(e)
            return

        source.finish()

    asyncio.create_task(producer())
    return source


# =========================================================
# STT
# =========================================================
def get_stt_model() -> tuple[AutoProcessor, CohereAsrForConditionalGeneration]:
    global stt_processor, stt_model

    if stt_processor is not None and stt_model is not None:
        return stt_processor, stt_model

    device = "cuda" if torch.cuda.is_available() else "cpu"
    token = os.getenv("HF_TOKEN")
    torch_dtype = torch.float16 if device == "cuda" and STT_COMPUTE_TYPE == "float16" else torch.float32

    print(f"STT 로드 시작: model={STT_MODEL_NAME}, device={device}, dtype={torch_dtype}")

    stt_processor = AutoProcessor.from_pretrained(
        STT_MODEL_NAME,
        token=token,
    )

    stt_model = CohereAsrForConditionalGeneration.from_pretrained(
        STT_MODEL_NAME,
        token=token,
        torch_dtype=torch_dtype,
    ).to(device)

    print("STT 로드 완료")
    return stt_processor, stt_model


def transcribe_audio16k_sync(audio16k: np.ndarray, max_new_tokens: int = 256) -> str:
    if audio16k.size == 0:
        return ""

    processor, model = get_stt_model()

    inputs = processor(
        audio16k,
        sampling_rate=TARGET_RATE,
        return_tensors="pt",
        language=STT_LANGUAGE,
    )

    moved = {}
    for k, v in inputs.items():
        if torch.is_tensor(v):
            if torch.is_floating_point(v):
                moved[k] = v.to(model.device, dtype=model.dtype)
            else:
                moved[k] = v.to(model.device)
        else:
            moved[k] = v

    with torch.inference_mode():
        outputs = model.generate(**moved, max_new_tokens=max_new_tokens)

    text = processor.decode(outputs[0], skip_special_tokens=True)
    return clean_text(text)


def transcribe_voice_sync(pcm_bytes: bytes) -> str:
    return transcribe_audio16k_sync(prepare_stt_audio(pcm_bytes), max_new_tokens=256)


def detect_wake_word_sync(audio16k: np.ndarray) -> tuple[bool, str]:
    wake_audio = slice_audio_window(audio16k, WAKE_AUDIO_SEC)
    wake_text = transcribe_audio16k_sync(wake_audio, max_new_tokens=WAKE_MAX_TOKENS)
    return contains_leading_wake_word(wake_text), wake_text


# =========================================================
# 디스코드 음성
# =========================================================
async def ensure_listening_voice_client(guild: discord.Guild, target_channel: discord.VoiceChannel) -> Optional[EvelynVoiceClient]:
    vc = guild.voice_client

    if vc is not None and not isinstance(vc, EvelynVoiceClient):
        await vc.disconnect(force=True)
        vc = None

    if vc is None:
        vc = await target_channel.connect(cls=EvelynVoiceClient)
    elif vc.channel != target_channel:
        await vc.move_to(target_channel)

    if isinstance(vc, EvelynVoiceClient):
        vc.on_user_audio = process_member_audio
        if not vc.is_listening():
            vc.listen()
        return vc

    return None


async def ensure_voice_client(message: discord.Message) -> Optional[EvelynVoiceClient]:
    if not message.guild:
        return None

    voice_state = getattr(message.author, "voice", None)
    if not voice_state or not voice_state.channel:
        return None

    vc = await ensure_listening_voice_client(message.guild, voice_state.channel)
    return vc


async def wait_until_not_playing(vc: discord.VoiceClient) -> None:
    while vc.is_playing() or vc.is_paused():
        await asyncio.sleep(0.05)


async def play_audio_source(
    vc: discord.VoiceClient,
    source: discord.AudioSource,
    *,
    on_play_start: Callable[[], None] | None = None,
) -> None:
    await wait_until_not_playing(vc)

    done = asyncio.Event()
    playback_error: list[Exception | None] = [None]

    def after_play(err):
        if err:
            playback_error[0] = err
        bot.loop.call_soon_threadsafe(done.set)

    if on_play_start is not None:
        on_play_start()
    vc.play(source, after=after_play)
    await done.wait()

    if playback_error[0] is not None:
        raise playback_error[0]

    if isinstance(source, OmniVoicePCMStream) and source.error is not None:
        raise source.error


async def speak_answer(vc: discord.VoiceClient, answer: str) -> None:
    guild_id = getattr(getattr(vc, "guild", None), "id", None)

    async with tts_lock:
        source = await create_omnivoice_source(answer)
        try:
            if guild_id is not None:
                bot_speaking_guilds.add(guild_id)
            await play_audio_source(vc, source)
        finally:
            if guild_id is not None:
                bot_speaking_guilds.discard(guild_id)
                last_bot_audio_end_at[guild_id] = time.monotonic()


async def stream_tts_sentences(
    vc: discord.VoiceClient,
    sentence_queue: "asyncio.Queue[str | None]",
    *,
    metrics: dict | None = None,
) -> None:
    guild_id = getattr(getattr(vc, "guild", None), "id", None)
    did_speak = False

    async with tts_lock:
        try:
            while True:
                sentence = await sentence_queue.get()
                if sentence is None:
                    break

                sentence = clean_tts_text(sentence)
                if not sentence:
                    continue

                if guild_id is not None and not did_speak:
                    bot_speaking_guilds.add(guild_id)

                did_speak = True
                source = await create_omnivoice_source(
                    sentence,
                    on_first_byte=lambda: log_voice_latency(metrics, "tts_first_byte_logged", "TTS 첫 바이트 도착 시간"),
                )
                await play_audio_source(
                    vc,
                    source,
                    on_play_start=lambda: log_voice_latency(metrics, "playback_start_logged", "첫 재생 시작 시간"),
                )
        finally:
            if guild_id is not None:
                bot_speaking_guilds.discard(guild_id)
                if did_speak:
                    last_bot_audio_end_at[guild_id] = time.monotonic()


# =========================================================
# LLM
# =========================================================
def fallback_answer_for(user_text: str) -> str:
    user_text = clean_text(user_text)
    if not user_text:
        return "응, 듣고 있어."
    return "응, 잠깐만."


async def ask_llm_once(user_text: str, guild_id: int | None = None) -> str:
    final_user_text = (
        f"{user_text}\n\n"
        "주의: 생각 과정 말하지 말고, 최종 답변만 한국어로 한두 문장으로 짧게 말해."
    )

    messages = list(conversation_history)

    if guild_id is not None:
        memory_context = build_memory_context(guild_id, user_text)
        if memory_context:
            base_system = messages[0]["content"] if messages and messages[0].get("role") == "system" else ""
            merged_system = clean_text(base_system + "\n\n" + memory_context)

            if messages and messages[0].get("role") == "system":
                messages[0] = {"role": "system", "content": merged_system}
            else:
                messages.insert(0, {"role": "system", "content": merged_system})

    payload = {
        "model": MODEL_NAME,
        "messages": messages + [{"role": "user", "content": final_user_text}],
        "temperature": 0.1,
        "max_tokens": 320,
        "stream": False,
    }

    timeout = aiohttp.ClientTimeout(total=120)
    session = await get_http_session()

    async with session.post(LLM_SERVER_URL, json=payload, timeout=timeout) as resp:
        if resp.status != 200:
            error_text = await resp.text()
            raise RuntimeError(f"LLM 서버 오류: {resp.status} / {error_text[:300]}")

        data = await resp.json()
        choices = data.get("choices", [])
        if not choices:
            return fallback_answer_for(user_text)

        choice = choices[0]
        msg = choice.get("message", {})
        answer = sanitize_model_output(msg.get("content", ""))
        reasoning = msg.get("reasoning_content", "")
        finish_reason = choice.get("finish_reason", "")

        if answer:
            return answer

        extracted = extract_answer_from_reasoning(reasoning, user_text)
        if extracted:
            return extracted

        print(f"LLM 응답 본문이 비어 있어서 fallback 사용, finish_reason={finish_reason}")
        return fallback_answer_for(user_text)


def extract_stream_delta_text(data: dict) -> str:
    choices = data.get("choices", [])
    if not choices:
        return ""

    choice = choices[0]
    delta = choice.get("delta") or {}
    content = delta.get("content")

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)

    message = choice.get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content

    text = choice.get("text")
    return text if isinstance(text, str) else ""


async def ask_llm_streaming(
    user_text: str,
    guild_id: int | None = None,
    on_sentence: Callable[[str], Awaitable[None]] | None = None,
    on_first_chunk: Callable[[], None] | None = None,
) -> str:
    final_user_text = (
        f"{user_text}\n\n"
        "주의: 생각 과정 말하지 말고, 최종 답변만 한국어로 한두 문장으로 짧게 말해."
    )

    messages = list(conversation_history)

    if guild_id is not None:
        memory_context = build_memory_context(guild_id, user_text)
        if memory_context:
            base_system = messages[0]["content"] if messages and messages[0].get("role") == "system" else ""
            merged_system = clean_text(base_system + "\n\n" + memory_context)

            if messages and messages[0].get("role") == "system":
                messages[0] = {"role": "system", "content": merged_system}
            else:
                messages.insert(0, {"role": "system", "content": merged_system})

    payload = {
        "model": MODEL_NAME,
        "messages": messages + [{"role": "user", "content": final_user_text}],
        "temperature": 0.1,
        "max_tokens": 320,
        "stream": True,
    }

    timeout = aiohttp.ClientTimeout(total=120)
    session = await get_http_session()
    raw_parts: list[str] = []
    sentence_buffer = ""
    emitted_any = False

    async with session.post(LLM_SERVER_URL, json=payload, timeout=timeout) as resp:
        if resp.status != 200:
            error_text = await resp.text()
            raise RuntimeError(f"LLM 서버 오류: {resp.status} / {error_text[:300]}")

        content_type = resp.headers.get("Content-Type", "")
        if "application/json" in content_type.lower():
            data = await resp.json()
            choices = data.get("choices", [])
            answer = ""
            if choices:
                msg = choices[0].get("message", {})
                answer = sanitize_model_output(msg.get("content", ""))
                if not answer:
                    answer = extract_answer_from_reasoning(msg.get("reasoning_content", ""), user_text)
            if not answer:
                answer = fallback_answer_for(user_text)
            if on_first_chunk is not None:
                on_first_chunk()
            if on_sentence is not None:
                await on_sentence(answer)
            return answer

        async for raw_line in resp.content:
            line = raw_line.decode("utf-8", errors="ignore").strip()
            if not line or line.startswith(":"):
                continue
            if line.startswith("data:"):
                line = line[5:].strip()
            if not line:
                continue
            if line == "[DONE]":
                break

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            delta_text = extract_stream_delta_text(data)
            if not delta_text:
                continue

            if on_first_chunk is not None:
                on_first_chunk()
                on_first_chunk = None

            raw_parts.append(delta_text)
            sentence_buffer += delta_text

            if on_sentence is not None:
                ready_chunks, sentence_buffer = split_tts_sentences(sentence_buffer)
                for chunk in ready_chunks:
                    emitted_any = True
                    await on_sentence(chunk)

    answer = sanitize_model_output("".join(raw_parts))
    if not answer:
        answer = fallback_answer_for(user_text)

    if on_sentence is not None:
        ready_chunks, sentence_buffer = split_tts_sentences(sentence_buffer, force=True)
        if not ready_chunks and answer and not emitted_any:
            ready_chunks = [answer]
        for chunk in ready_chunks:
            emitted_any = True
            await on_sentence(chunk)

    return answer


async def ask_llm_and_speak_streaming(
    vc: discord.VoiceClient,
    user_text: str,
    guild_id: int | None = None,
    on_final_answer: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    metrics = {
        "started_at": time.monotonic(),
        "llm_first_chunk_logged": False,
        "tts_first_byte_logged": False,
        "playback_start_logged": False,
    }
    sentence_queue: asyncio.Queue[str | None] = asyncio.Queue()
    playback_task = asyncio.create_task(stream_tts_sentences(vc, sentence_queue, metrics=metrics))
    llm_error: Exception | None = None
    answer = ""

    async def enqueue_sentence(sentence: str) -> None:
        sentence = clean_tts_text(sentence)
        if sentence:
            await sentence_queue.put(sentence)

    try:
        answer = await ask_llm_streaming(
            user_text,
            guild_id=guild_id,
            on_sentence=enqueue_sentence,
            on_first_chunk=lambda: log_voice_latency(metrics, "llm_first_chunk_logged", "LLM 첫 chunk 시간"),
        )
        answer = clean_text(answer)
        if answer and on_final_answer is not None:
            await on_final_answer(answer)
    except Exception as e:
        llm_error = e
        answer = ""
    finally:
        await sentence_queue.put(None)

    try:
        await playback_task
    except Exception:
        if llm_error is None:
            raise

    if llm_error is not None:
        raise llm_error

    return answer


# =========================================================
# 음성 입력 처리
# =========================================================
async def process_member_audio(member: discord.Member | None, pcm_bytes: bytes) -> None:
    if member is None:
        return

    if member.bot:
        return

    guild = getattr(member, "guild", None)
    if guild is None:
        return

    guild_id = guild.id
    audio16k = prepare_stt_audio(pcm_bytes)
    if audio16k.size == 0:
        return

    if VAD_ENABLED and is_probably_silent(audio16k):
        return

    try:
        wake_detected, wake_probe = await asyncio.to_thread(detect_wake_word_sync, audio16k)
    except Exception as e:
        print(f"❌ [WAKE-STT] {e}")
        return

    if not wake_detected:
        if wake_probe:
            print(f"[WAKE IGNORE] {member.display_name}: {wake_probe!r}")
        return

    try:
        text = await asyncio.to_thread(transcribe_audio16k_sync, audio16k, 256)
    except Exception as e:
        print(f"❌ [STT] {e}")
        return

    if not text:
        return

    corrected_text = apply_stt_post_corrections(text, wake_detected=wake_detected)
    if corrected_text != text:
        print(f"[STT CORRECT] raw={text!r} -> corrected={corrected_text!r}")
    text = corrected_text

    if should_ignore_short_transcription(text, pcm_bytes, wake_detected=wake_detected):
        print(f"[STT IGNORE] short_noise: {text!r}")
        return

    print(f"🎤 [{member.display_name}] wake={wake_probe!r} text={text}")

    ok, reason = should_reply_to_voice(guild_id, text, wake_detected=True)
    if not ok:
        print(f"[STT IGNORE] {reason}: {text!r}")
        return

    user_text = strip_voice_wake_word(text)
    lock = guild_locks.setdefault(guild_id, asyncio.Lock())

    if lock.locked():
        print(f"[VOICE WAIT] guild={guild_id} speaker={member.display_name} text={user_text!r}")

    async with lock:
        vc = guild.voice_client
        if vc is None:
            return

        async def on_final_answer(answer_text: str) -> None:
            print(f"💬 [Evelyn] {answer_text}")

        try:
            answer = await ask_llm_and_speak_streaming(
                vc,
                user_text,
                guild_id=guild_id,
                on_final_answer=on_final_answer,
            )
        except Exception as e:
            print(f"❌ [LLM/TTS] {e}")
            return

        answer = clean_text(answer)
        if not answer:
            return

        append_history(user_text, answer)
        schedule_memory_update(guild_id, user_text, answer)


# =========================================================
# 이벤트
# =========================================================
@bot.event
async def on_ready():
    print(f"로그인 완료: {bot.user}")
    try:
        await asyncio.to_thread(get_stt_model)
    except Exception as e:
        print(f"STT 로드 실패: {e}")

    try:
        await warmup_tts_server()
    except Exception as e:
        print("OmniVoice 서버 준비 확인 실패:", repr(e))


@bot.event
async def on_message(message: discord.Message):
    global conversation_history

    if message.author.bot:
        return

    if not message.guild:
        await bot.process_commands(message)
        return

    is_wake_word = contains_wake_word(message.content)
    is_reply = False

    if message.reference:
        try:
            replied_msg = await message.channel.fetch_message(message.reference.message_id)
            if replied_msg.author == bot.user:
                is_reply = True
        except Exception as e:
            print("답장 확인 오류:", repr(e))

    if not (is_wake_word or is_reply):
        await bot.process_commands(message)
        return

    user_text = strip_voice_wake_word(message.content) if is_wake_word else message.content.strip()
    if not user_text:
        user_text = "부르셨나요?"

    conversation_history[:] = [conversation_history[0]] + [
        m for m in conversation_history[1:] if m.get("role") != "system"
    ]

    lock = guild_locks.setdefault(message.guild.id, asyncio.Lock())

    if lock.locked():
        await message.channel.send("⏳ 지금 다른 응답을 처리 중이야. 잠깐만.")
        await bot.process_commands(message)
        return

    async with lock:
        try:
            async with message.channel.typing():
                vc = None
                if AUTO_JOIN_VOICE:
                    vc = await ensure_voice_client(message)

                answer = await ask_llm_once(user_text, guild_id=message.guild.id)

                await message.channel.send(visible_text(answer))

            append_history(user_text, answer)
            schedule_memory_update(message.guild.id, user_text, answer)

            if vc is not None:
                await speak_answer(vc, answer)

        except Exception as e:
            print("전체 오류:", repr(e))
            await message.channel.send(f"❌ 오류 발생: {e}")

    await bot.process_commands(message)


# =========================================================
# 명령어
# =========================================================
@bot.command(name="들어와", aliases=["join"])
async def join_voice(ctx):
    voice_state = getattr(ctx.author, "voice", None)
    if not voice_state or not voice_state.channel:
        await ctx.send("먼저 음성 채널에 들어가줘.")
        return

    try:
        vc = await ensure_listening_voice_client(ctx.guild, voice_state.channel)
        if vc is None:
            await ctx.send("❌ 음성 연결에 실패했어.")
            return
        await ctx.send(f"🔊 {voice_state.channel.name}에 들어왔어. 이제 듣고 말할게.")
    except Exception as e:
        print("음성 연결 오류:", repr(e))
        await ctx.send(f"❌ 음성 연결 실패: {e}")


@bot.command(name="다시들어와", aliases=["rejoin"])
async def rejoin_voice(ctx):
    channel = ctx.author.voice.channel if ctx.author.voice else None
    if channel is None:
        await ctx.send("먼저 음성 채널에 들어가줘.")
        return

    vc = ctx.guild.voice_client
    if vc is not None:
        try:
            if hasattr(vc, "stop_listening"):
                vc.stop_listening()
        except Exception:
            pass
        await vc.disconnect(force=True)

    try:
        new_vc = await ensure_listening_voice_client(ctx.guild, channel)
        if new_vc is None:
            await ctx.send("❌ 재연결 실패")
            return
        await ctx.send("🔄 다시 붙었어. 이제 계속 들을게.")
    except Exception as e:
        print("재연결 오류:", repr(e))
        await ctx.send(f"❌ 재연결 실패: {e}")


@bot.command(name="나가", aliases=["leave"])
async def leave_voice(ctx):
    vc = ctx.guild.voice_client
    if vc is None:
        await ctx.send("이미 나와 있어.")
        return

    try:
        if hasattr(vc, "stop_listening"):
            vc.stop_listening()
    except Exception:
        pass

    await vc.disconnect()
    await ctx.send("👋 나갔어.")


# =========================================================
# 실행
# =========================================================
if not DISCORD_BOT_TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN 환경변수가 설정되지 않았습니다.")

bot.run(DISCORD_BOT_TOKEN)
