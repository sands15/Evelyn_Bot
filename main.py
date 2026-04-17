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
    from silero_vad import load_silero_vad, get_speech_timestamps
except Exception:
    load_silero_vad = None
    get_speech_timestamps = None

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
MODEL_NAME = os.getenv("LLM_MODEL_NAME", "Qwen3-8B-Q4_K_M.gguf")

OMNIVOICE_SERVER_URL = os.getenv("OMNIVOICE_SERVER_URL", "http://127.0.0.1:8880")
OMNIVOICE_MODEL = os.getenv("OMNIVOICE_MODEL", "omnivoice")
OMNIVOICE_VOICE = os.getenv("OMNIVOICE_VOICE", "clone:evelyn")
OMNIVOICE_LANGUAGE = os.getenv("OMNIVOICE_LANGUAGE", "ko")
OMNIVOICE_STREAM = os.getenv("OMNIVOICE_STREAM", "true").lower() == "true"
OMNIVOICE_TIMEOUT_SEC = float(os.getenv("OMNIVOICE_TIMEOUT_SEC", "180"))

SUMMARY_LLM_URL = os.getenv("SUMMARY_LLM_URL", "http://127.0.0.1:9821/v1/chat/completions")
SUMMARY_MODEL_NAME = os.getenv("SUMMARY_MODEL_NAME", "Qwen3.6-35B-A3B-UD-Q3_K_XL.gguf")
MEMORY_ROOT = Path(os.getenv("BOT_MEMORY_DIR", str(Path(__file__).resolve().parent / "bot_memory")))
MEMORY_FACT_LIMIT = int(os.getenv("MEMORY_FACT_LIMIT", "200"))
MEMORY_LOOP_LIMIT = int(os.getenv("MEMORY_LOOP_LIMIT", "100"))
MEMORY_RAW_LIMIT = int(os.getenv("MEMORY_RAW_LIMIT", "400"))
MEMORY_RAW_CONTEXT_LIMIT = int(os.getenv("MEMORY_RAW_CONTEXT_LIMIT", "6"))
MEMORY_RETRIEVE_LIMIT = int(os.getenv("MEMORY_RETRIEVE_LIMIT", "8"))
MEMORY_WORKING_SUMMARY_MAX_CHARS = int(os.getenv("MEMORY_WORKING_SUMMARY_MAX_CHARS", "700"))
MEMORY_COGNITIVE_RAW_LIMIT = int(os.getenv("MEMORY_COGNITIVE_RAW_LIMIT", "4"))
MEMORY_LONGTERM_RAW_LIMIT = int(os.getenv("MEMORY_LONGTERM_RAW_LIMIT", "6"))
MEMORY_VAULT_RAW_RETRIEVE_LIMIT = int(os.getenv("MEMORY_VAULT_RAW_RETRIEVE_LIMIT", "4"))
MEMORY_VAULT_DAYS = int(os.getenv("MEMORY_VAULT_DAYS", "7"))
COGNITIVE_MAX_TOKENS = int(os.getenv("COGNITIVE_MAX_TOKENS", "120"))
COGNITIVE_TIMEOUT_SEC = float(os.getenv("COGNITIVE_TIMEOUT_SEC", "8"))
ASK_CONFIDENCE_THRESHOLD_TEXT = float(os.getenv("ASK_CONFIDENCE_THRESHOLD_TEXT", "0.75"))
ASK_CONFIDENCE_THRESHOLD_VOICE = float(os.getenv("ASK_CONFIDENCE_THRESHOLD_VOICE", "0.85"))

STT_MODEL_NAME = os.getenv("STT_MODEL_NAME", "CohereLabs/cohere-transcribe-03-2026")
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "ko")
STT_COMPUTE_TYPE = os.getenv("STT_COMPUTE_TYPE", "float16")
STT_FORCE_LANGUAGE = os.getenv("STT_FORCE_LANGUAGE", "true").lower() == "true"
STT_FORCE_PUNCTUATION = os.getenv("STT_FORCE_PUNCTUATION", "true").lower() == "true"

VAD_ENABLED = os.getenv("VAD_ENABLED", "true").lower() == "true"
VAD_PROVIDER = os.getenv("VAD_PROVIDER", "silero").lower()
VAD_RMS_THRESHOLD = float(os.getenv("VAD_RMS_THRESHOLD", "0.008"))
VAD_PEAK_THRESHOLD = float(os.getenv("VAD_PEAK_THRESHOLD", "0.020"))
VAD_MIN_VOICED_RATIO = float(os.getenv("VAD_MIN_VOICED_RATIO", "0.015"))
VAD_CHUNK_MS = float(os.getenv("VAD_CHUNK_MS", "32"))
VAD_START_CONSECUTIVE = int(os.getenv("VAD_START_CONSECUTIVE", "2"))
VOICE_ENV_FLATNESS_MAX = float(os.getenv("VOICE_ENV_FLATNESS_MAX", "0.72"))
VOICE_HUMAN_BAND_RATIO_MIN = float(os.getenv("VOICE_HUMAN_BAND_RATIO_MIN", "0.38"))
VOICE_ENV_RMS_MAX = float(os.getenv("VOICE_ENV_RMS_MAX", "0.020"))
SILERO_VAD_THRESHOLD = float(os.getenv("SILERO_VAD_THRESHOLD", "0.30"))
SILERO_MIN_SPEECH_MS = int(os.getenv("SILERO_MIN_SPEECH_MS", "32"))
SILERO_MIN_SILENCE_MS = int(os.getenv("SILERO_MIN_SILENCE_MS", "0"))
SILERO_SPEECH_PAD_MS = int(os.getenv("SILERO_SPEECH_PAD_MS", "80"))
SILERO_VAD_ONNX = os.getenv("SILERO_VAD_ONNX", "true").lower() == "true"

DENOISE_ENABLED = os.getenv("DENOISE_ENABLED", "true").lower() == "true"
DENOISE_HIGHPASS_HZ = float(os.getenv("DENOISE_HIGHPASS_HZ", "120"))
DENOISE_NOISE_FLOOR_SEC = float(os.getenv("DENOISE_NOISE_FLOOR_SEC", "0.20"))
DENOISE_GATE_MULT = float(os.getenv("DENOISE_GATE_MULT", "1.35"))
WAKE_AUDIO_SEC = float(os.getenv("WAKE_AUDIO_SEC", "1.4"))
WAKE_MAX_TOKENS = int(os.getenv("WAKE_MAX_TOKENS", "48"))
WAKE_FUZZY_THRESHOLD = float(os.getenv("WAKE_FUZZY_THRESHOLD", "0.72"))
WAKE_SHORT_TEXT_KEEP_LEN = int(os.getenv("WAKE_SHORT_TEXT_KEEP_LEN", "2"))
TTS_EARLY_CHUNK_LEN = int(os.getenv("TTS_EARLY_CHUNK_LEN", "14"))
TTS_EARLY_CUT_MIN = int(os.getenv("TTS_EARLY_CUT_MIN", "6"))
VOICE_STT_MAX_NEW_TOKENS = int(os.getenv("VOICE_STT_MAX_NEW_TOKENS", "256"))
VOICE_LLM_MAX_TOKENS = int(os.getenv("VOICE_LLM_MAX_TOKENS", "320"))

MAX_HISTORY_ITEMS = 1024
VOICE_HISTORY_LIMIT = int(os.getenv("VOICE_HISTORY_LIMIT", str(MAX_HISTORY_ITEMS)))
MAX_VISIBLE_TEXT = 1800
AUTO_JOIN_VOICE = os.getenv("AUTO_JOIN_VOICE", "true").lower() == "true"

MIN_TEXT_LEN = int(os.getenv("VOICE_MIN_TEXT_LEN", "4"))
MIN_TRANSCRIBED_LEN = int(os.getenv("VOICE_MIN_TRANSCRIBED_LEN", "6"))
MIN_AUDIO_SEC = float(os.getenv("VOICE_MIN_AUDIO_SEC", "0.6"))
REPLY_COOLDOWN_SEC = float(os.getenv("VOICE_REPLY_COOLDOWN_SEC", "2.5"))
POST_TTS_IGNORE_SEC = float(os.getenv("VOICE_POST_TTS_IGNORE_SEC", "1.2"))
SIMILARITY_BLOCK = float(os.getenv("VOICE_SIMILARITY_BLOCK", "0.88"))
VOICE_CONNECT_TIMEOUT = float(os.getenv("VOICE_CONNECT_TIMEOUT", "45"))
VOICE_CONNECT_RETRIES = max(1, int(os.getenv("VOICE_CONNECT_RETRIES", "2")))
VOICE_CONNECT_RETRY_DELAY_SEC = float(os.getenv("VOICE_CONNECT_RETRY_DELAY_SEC", "1.5"))
VOICE_TIMING_LOG_THRESHOLD_MS = float(os.getenv("VOICE_TIMING_LOG_THRESHOLD_MS", "3000"))
WAKE_WORDS = [
    w.strip()
    for w in os.getenv(
        "WAKE_WORDS",
        "이별인,이별링,이벨링,에벌링,이블린,이불린,이불링,이브린,이브링,입을린,입을링,이블닝,이블링,이별린,이벌린,에블린,에브린,에블링,에브링,에벌린,이벨린,이반린,불리읍,이블리,이별된,이벨리나,이별레인"
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
            "질문에는 한 문장 또는 두 문장으로 짧고 자연스럽게 답한다. "
            "OmniVoice 감정 태그를 쓸 수 있다. 허용 태그는 [laughter], [sigh], [confirmation-en], [question-en], [question-ah], [question-oh], [question-ei], [question-yi], [surprise-ah], [surprise-oh], [surprise-wa], [surprise-yo], [dissatisfaction-hnn] 뿐이다. "
            "감정이 자연스럽게 들릴 때만 태그를 짧게 붙이고, 남용하지 마라. 태그 외 다른 대괄호 표현은 절대 쓰지 마라."
        ),
    }
]

guild_locks: dict[int, asyncio.Lock] = {}
tts_lock = asyncio.Lock()

stt_processor: Optional[AutoProcessor] = None
stt_model: Optional[CohereAsrForConditionalGeneration] = None
http_session: Optional[aiohttp.ClientSession] = None
silero_vad_model = None
silero_vad_warned = False

last_voice_reply_at: dict[int, float] = {}
last_voice_text: dict[int, str] = {}
last_bot_audio_end_at: dict[int, float] = {}
bot_speaking_guilds: set[int] = set()
memory_locks: dict[int, asyncio.Lock] = {}
cognitive_locks: dict[int, asyncio.Lock] = {}

ALLOWED_OMNIVOICE_TAGS = {
    "[laughter]",
    "[sigh]",
    "[confirmation-en]",
    "[question-en]",
    "[question-ah]",
    "[question-oh]",
    "[question-ei]",
    "[question-yi]",
    "[surprise-ah]",
    "[surprise-oh]",
    "[surprise-wa]",
    "[surprise-yo]",
    "[dissatisfaction-hnn]",
}
OMNIVOICE_TAG_GUIDANCE = (
    "필요할 때만 OmniVoice 감정 태그를 매우 짧게 써도 된다. "
    "허용 태그는 [laughter], [sigh], [confirmation-en], [question-en], [question-ah], [question-oh], [question-ei], [question-yi], [surprise-ah], [surprise-oh], [surprise-wa], [surprise-yo], [dissatisfaction-hnn] 뿐이다. "
    "태그는 문장 앞이나 짧은 감탄 앞에 자연스럽게 붙이고, 보통 답변 전체에서 0개 또는 1개만 쓰고 남용하지 마라. "
    "한 문장에 여러 태그를 연달아 붙이지 마라. 태그는 말투를 보조할 때만 써라."
)


# =========================================================
# 유틸
# =========================================================
def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def normalize_omnivoice_tags(text: str) -> str:
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
    text = normalize_omnivoice_tags(clean_text(text))
    leading_tag_match = re.match(r"^\s*(\[[^\[\]]+\])", text)
    leading_tag = leading_tag_match.group(1) if leading_tag_match else ""

    text = re.sub(r"\[[^\[\]]+\]", " ", text)
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


def trim_history() -> None:
    global conversation_history
    if len(conversation_history) > 1 + MAX_HISTORY_ITEMS:
        conversation_history = [conversation_history[0]] + conversation_history[-MAX_HISTORY_ITEMS:]


def get_voice_history_messages() -> list[dict]:
    messages = list(conversation_history)
    if not messages:
        return []

    system_message = messages[0] if messages[0].get("role") == "system" else None
    tail = [m for m in messages[1:] if m.get("role") != "system"][-VOICE_HISTORY_LIMIT:]
    return ([system_message] if system_message else []) + tail


def append_history(user_text: str, answer: str) -> None:
    conversation_history.append({"role": "user", "content": clean_text(user_text)})
    conversation_history.append({"role": "assistant", "content": clean_text(answer)})
    trim_history()


def guild_memory_dir(guild_id: int) -> Path:
    path = MEMORY_ROOT / f"guild_{guild_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def memory_vault_dir(guild_id: int) -> Path:
    path = guild_memory_dir(guild_id) / "vault"
    path.mkdir(parents=True, exist_ok=True)
    return path


def memory_summary_path(guild_id: int) -> Path:
    return guild_memory_dir(guild_id) / "rolling_summary.txt"


def memory_raw_path(guild_id: int) -> Path:
    return guild_memory_dir(guild_id) / "raw_transcript.jsonl"


def vault_raw_dir(guild_id: int) -> Path:
    path = memory_vault_dir(guild_id) / "raw"
    path.mkdir(parents=True, exist_ok=True)
    return path


def vault_daily_raw_path(guild_id: int, day_key: str | None = None) -> Path:
    day_key = day_key or time.strftime("%Y-%m-%d")
    return vault_raw_dir(guild_id) / f"{day_key}.jsonl"


def memory_facts_path(guild_id: int) -> Path:
    return guild_memory_dir(guild_id) / "durable_facts.jsonl"


def vault_facts_path(guild_id: int) -> Path:
    return memory_vault_dir(guild_id) / "facts.jsonl"


def memory_questions_path(guild_id: int) -> Path:
    return guild_memory_dir(guild_id) / "open_questions.jsonl"


def vault_questions_path(guild_id: int) -> Path:
    return memory_vault_dir(guild_id) / "questions.jsonl"


def cognitive_state_path(guild_id: int) -> Path:
    return guild_memory_dir(guild_id) / "cognitive_state.json"


def memory_loops_path(guild_id: int) -> Path:
    return guild_memory_dir(guild_id) / "open_loops.jsonl"


def read_text_file(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore").strip()


def write_text_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(clean_text(text), encoding="utf-8")


def read_json_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_json_file(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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


def append_jsonl_rows(path: Path, rows: list[dict], limit: int) -> None:
    existing = read_jsonl(path)
    existing.extend(row for row in rows if isinstance(row, dict))
    if len(existing) > limit:
        existing = existing[-limit:]
    write_jsonl(path, existing)


def compact_working_summary(text: str) -> str:
    text = clean_text(text)
    if len(text) <= MEMORY_WORKING_SUMMARY_MAX_CHARS:
        return text
    return clean_text(text[-MEMORY_WORKING_SUMMARY_MAX_CHARS:])


def merge_memory_rows(*row_groups: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for rows in row_groups:
        for row in rows:
            if not isinstance(row, dict):
                continue
            text = clean_text(str(row.get("text", "")))
            row_type = clean_text(str(row.get("type", row.get("role", "memory")))) or "memory"
            if len(text) < 1:
                continue
            key = (row_type, text)
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)
    return merged


def read_vault_raw_rows(guild_id: int, *, days: int | None = None) -> list[dict]:
    days = days or MEMORY_VAULT_DAYS
    paths = sorted(vault_raw_dir(guild_id).glob("*.jsonl"))
    selected = paths[-max(1, days):]
    rows: list[dict] = []
    for path in selected:
        rows.extend(read_jsonl(path))
    return rows


def read_fact_rows(guild_id: int) -> list[dict]:
    return merge_memory_rows(read_jsonl(memory_facts_path(guild_id)), read_jsonl(vault_facts_path(guild_id)))


def append_raw_transcript_rows(guild_id: int, rows: list[dict]) -> None:
    normalized: list[dict] = []
    now = int(time.time())

    for row in rows:
        text = clean_text(str(row.get("text", "")))
        if len(text) < 1:
            continue
        normalized.append(
            {
                "role": clean_text(str(row.get("role", "user"))) or "user",
                "speaker": clean_text(str(row.get("speaker", ""))),
                "source": clean_text(str(row.get("source", "unknown"))) or "unknown",
                "text": text,
                "saved_at": int(row.get("saved_at", now)),
            }
        )

    if normalized:
        append_jsonl_rows(memory_raw_path(guild_id), normalized, MEMORY_RAW_LIMIT)
        append_jsonl_rows(vault_daily_raw_path(guild_id), normalized, max(MEMORY_RAW_LIMIT * 20, 5000))


def append_unique_memory_rows(path: Path, rows: list[dict], limit: int, *, mirror_path: Path | None = None) -> None:
    existing = read_jsonl(path)
    mirror_existing = read_jsonl(mirror_path) if mirror_path is not None else []
    seen = {clean_text(str(row.get("text", ""))) for row in merge_memory_rows(existing, mirror_existing)}
    appended_rows: list[dict] = []

    for row in rows:
        text = clean_text(str(row.get("text", "")))
        if len(text) < 2 or text in seen:
            continue
        saved_row = {
            "text": text,
            "type": clean_text(str(row.get("type", "memory"))) or "memory",
            "saved_at": int(time.time()),
        }
        seen.add(text)
        existing.append(saved_row)
        appended_rows.append(saved_row)

    if len(existing) > limit:
        existing = existing[-limit:]

    write_jsonl(path, existing)
    if mirror_path is not None and appended_rows:
        mirror_rows = mirror_existing + appended_rows
        write_jsonl(mirror_path, mirror_rows)


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


def read_question_rows(guild_id: int) -> list[dict]:
    merged: list[dict] = []
    seen: set[str] = set()
    for path in (memory_questions_path(guild_id), memory_loops_path(guild_id), vault_questions_path(guild_id)):
        for row in read_jsonl(path):
            text = clean_text(str(row.get("text", "")))
            if len(text) < 2 or text in seen:
                continue
            seen.add(text)
            merged.append(row)
    return merged


def normalize_cognitive_action(value: str) -> str:
    action = clean_text(value).lower()
    if action in {"ask", "question", "clarify"}:
        return "ask"
    if action in {"wait", "listen", "hold"}:
        return "wait"
    return "answer"


def normalize_cognitive_state(data: dict) -> dict:
    if not isinstance(data, dict):
        data = {}

    question_for_user = clean_text(
        str(data.get("question_for_user", data.get("suggested_user_question", "")))
    )
    confidence_raw = data.get("confidence", 0.5)
    try:
        confidence = float(confidence_raw)
    except Exception:
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    retrieved_context_ids = data.get("retrieved_context_ids", [])
    if not isinstance(retrieved_context_ids, list):
        retrieved_context_ids = []

    return {
        "action": normalize_cognitive_action(str(data.get("action", "answer"))),
        "confidence": confidence,
        "user_intent": clean_text(str(data.get("user_intent", ""))),
        "state_summary": clean_text(str(data.get("state_summary", ""))),
        "question_for_user": question_for_user,
        "main_prompt_hint": clean_text(str(data.get("main_prompt_hint", ""))),
        "reason_brief": clean_text(str(data.get("reason_brief", ""))),
        "retrieved_context_ids": [clean_text(str(x)) for x in retrieved_context_ids if clean_text(str(x))],
        "updated_at": int(data.get("updated_at", time.time())),
    }


def ask_confidence_threshold_for_source(source: str) -> float:
    return ASK_CONFIDENCE_THRESHOLD_VOICE if source == "voice" else ASK_CONFIDENCE_THRESHOLD_TEXT


def apply_ask_gating(cognitive_state: dict | None = None, *, source: str = "text") -> dict:
    state = normalize_cognitive_state(cognitive_state or {})
    threshold = ask_confidence_threshold_for_source(source)

    if state.get("action") == "ask":
        question_for_user = clean_text(str(state.get("question_for_user", "")))
        confidence = float(state.get("confidence", 0.0) or 0.0)
        if not question_for_user or confidence < threshold:
            gated = dict(state)
            gated["action"] = "wait" if source == "voice" else "answer"
            reason = clean_text(str(gated.get("reason_brief", "")))
            gate_note = f"ask_gated_{source}_{confidence:.2f}_lt_{threshold:.2f}"
            gated["reason_brief"] = clean_text(f"{reason} {gate_note}") if reason else gate_note
            return gated

    return state


def build_main_response_guidance(cognitive_state: dict | None = None, *, source: str = "text") -> str:
    state = apply_ask_gating(cognitive_state, source=source)
    threshold = ask_confidence_threshold_for_source(source)
    parts = [
        "주의: 생각 과정 말하지 말고, 최종 답변만 한국어로 한두 문장으로 짧게 말해.",
        OMNIVOICE_TAG_GUIDANCE,
        "텍스트만 봤을 때도 자연스럽게 읽혀야 하고, 태그를 빼도 문장이 성립해야 한다.",
        "sub handoff의 question_for_user는 사용자가 한 말이 아니다. 내부 메모이므로, 그 문장을 사용자의 질문으로 오해해서 답하지 마라.",
        f"ask 행동은 {source} 입력에서 confidence {threshold:.2f} 이상일 때만 허용한다.",
    ]

    action = state.get("action", "answer")
    if state.get("user_intent"):
        parts.append(f"사용자 의도 추정: {state['user_intent']}")

    if action == "ask":
        parts.append("지금은 바로 단정하기보다 사용자의 원래 발화에 이어서 짧은 확인 질문을 먼저 하는 편이 자연스럽다.")
        parts.append("질문형 태그가 필요하면 [question-en], [question-ah], [question-oh], [question-ei], [question-yi] 중 하나만 골라라.")
        if state.get("question_for_user"):
            parts.append(f"사용자에게 되물을 내부 질문 초안: {state['question_for_user']}")
    elif action == "wait":
        parts.append("지금은 길게 답하지 말고, 더 들을 여지를 두는 짧은 반응이 자연스럽다.")
        parts.append("wait 상황에서는 감정 태그를 거의 쓰지 말고, 정말 필요할 때만 [sigh] 같은 약한 태그 하나만 써라.")
    else:
        parts.append("지금은 답변을 주는 편이 자연스럽다.")
        parts.append("확인이나 수긍에는 [confirmation-en], 가벼운 웃음에는 [laughter], 놀람에는 [surprise-oh]나 [surprise-wa]를 필요할 때만 써라.")

    if state.get("main_prompt_hint"):
        parts.append(f"응답 추가 힌트: {state['main_prompt_hint']}")
    if state.get("confidence", 0.0) > 0:
        parts.append(f"내부 판단 신뢰도: {state['confidence']:.2f}")

    return " ".join(clean_text(part) for part in parts if clean_text(part))


def classify_llm_route(user_text: str, *, source: str = "text") -> str:
    text = clean_text(user_text)
    if source == "voice":
        return "main_direct"

    short_text = len(text) <= 18 or len(text.split()) <= 4
    if short_text:
        return "main_direct"

    context_markers = [
        "아까", "방금", "전에", "이전", "기억", "문맥", "계속", "이어서",
        "요약", "정리", "판단", "비교", "설명", "의견", "생각", "왜", "어떻게",
    ]
    marker_hits = sum(1 for marker in context_markers if marker in text)

    if len(text) >= 60 or marker_hits >= 2:
        return "sub_wait"
    if len(text) >= 24 or marker_hits >= 1:
        return "sub_hint"
    return "main_direct"


async def prepare_llm_messages(
    user_text: str,
    *,
    guild_id: int | None = None,
    source: str = "text",
    debug_text: str | None = None,
) -> tuple[list[dict], dict | None, str]:
    route = classify_llm_route(user_text, source=source)
    messages = list(conversation_history)
    cognitive_state: dict | None = None

    if guild_id is not None and route == "sub_wait":
        cognitive_state = await update_cognitive_state(guild_id, user_text)
    elif guild_id is not None and route == "sub_hint":
        saved_state = read_json_file(cognitive_state_path(guild_id))
        cognitive_state = normalize_cognitive_state(saved_state) if saved_state else None

    if guild_id is not None and route != "main_direct":
        memory_context = build_memory_context(guild_id, user_text, cognitive_state=cognitive_state)
        if memory_context:
            base_system = messages[0]["content"] if messages and messages[0].get("role") == "system" else ""
            merged_system = clean_text(base_system + "\n\n" + memory_context)

            if messages and messages[0].get("role") == "system":
                messages[0] = {"role": "system", "content": merged_system}
            else:
                messages.insert(0, {"role": "system", "content": merged_system})

    if cognitive_state is not None:
        gated_state = apply_ask_gating(cognitive_state, source=source)
        if gated_state.get("action") != cognitive_state.get("action"):
            print(
                f"[ASK GATE] source={source} action={cognitive_state.get('action')} -> {gated_state.get('action')} confidence={float(cognitive_state.get('confidence', 0.0) or 0.0):.2f} threshold={ask_confidence_threshold_for_source(source):.2f}"
            )
            cognitive_state = gated_state

    route_text = debug_text if debug_text is not None else user_text
    print(f"[LLM ROUTE] source={source} route={route} text={visible_text(route_text)!r}")
    return messages, cognitive_state, route


def build_memory_context(guild_id: int, user_text: str, cognitive_state: dict | None = None) -> str:
    summary = compact_working_summary(read_text_file(memory_summary_path(guild_id)))
    raw_rows = read_jsonl(memory_raw_path(guild_id))[-MEMORY_RAW_CONTEXT_LIMIT:]
    vault_raw_rows = select_relevant_memory_rows(user_text, read_vault_raw_rows(guild_id), MEMORY_VAULT_RAW_RETRIEVE_LIMIT)
    facts = select_relevant_memory_rows(user_text, read_fact_rows(guild_id), MEMORY_RETRIEVE_LIMIT)
    questions = select_relevant_memory_rows(user_text, read_question_rows(guild_id), 4)
    state = normalize_cognitive_state(cognitive_state or read_json_file(cognitive_state_path(guild_id)))

    parts: list[str] = []
    if summary:
        parts.append(f"현재 작업 요약:\n{summary}")
    if raw_rows:
        parts.append(
            "최근 원문 로그:\n"
            + "\n".join(
                f"- {clean_text(str(row.get('speaker', row.get('role', 'unknown')))) or 'unknown'}"
                f" ({clean_text(str(row.get('source', 'unknown'))) or 'unknown'}): {clean_text(str(row.get('text', '')))}"
                for row in raw_rows
                if clean_text(str(row.get('text', '')))
            )
        )
    if vault_raw_rows:
        parts.append(
            "문서 보관함에서 꺼낸 관련 대화:\n"
            + "\n".join(
                f"- {clean_text(str(row.get('speaker', row.get('role', 'unknown')))) or 'unknown'}"
                f" ({clean_text(str(row.get('source', 'unknown'))) or 'unknown'}): {clean_text(str(row.get('text', '')))}"
                for row in vault_raw_rows
                if clean_text(str(row.get('text', '')))
            )
        )
    if state.get("state_summary") or state.get("question_for_user") or state.get("main_prompt_hint"):
        action_label = {
            "answer": "답하기",
            "ask": "질문하기",
            "wait": "더 듣기",
        }.get(state.get("action", "answer"), "답하기")
        state_lines = [f"- 권장 행동: {action_label}"]
        if state.get("user_intent"):
            state_lines.append(f"- 사용자 의도: {state['user_intent']}")
        if state.get("state_summary"):
            state_lines.append(f"- 현재 판단: {state['state_summary']}")
        if state.get("question_for_user"):
            state_lines.append(f"- 사용자에게 되물을 내부 질문 초안: {state['question_for_user']}")
        if state.get("main_prompt_hint"):
            state_lines.append(f"- 응답 힌트: {state['main_prompt_hint']}")
        if state.get("retrieved_context_ids"):
            state_lines.append(f"- 참고 문맥 ID: {', '.join(state['retrieved_context_ids'][:4])}")
        parts.append("현재 내부 상태(사용자 발화 아님):\n" + "\n".join(state_lines))
    if facts:
        parts.append(
            "장기 기억 후보:\n" + "\n".join(f"- {clean_text(str(row.get('text', '')))}" for row in facts)
        )
    if questions:
        parts.append(
            "열린 질문/가설:\n" + "\n".join(f"- {clean_text(str(row.get('text', '')))}" for row in questions)
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


async def ask_summary_llm(
    messages: list[dict],
    *,
    max_tokens: int = 500,
    timeout_seconds: float = 90,
) -> dict:
    session = await get_http_session()
    payload = {
        "model": SUMMARY_MODEL_NAME,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "stream": False,
    }
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)

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


async def update_cognitive_state(guild_id: int, user_text: str) -> dict:
    started_at = time.monotonic()
    lock = cognitive_locks.setdefault(guild_id, asyncio.Lock())
    async with lock:
        current_summary = compact_working_summary(read_text_file(memory_summary_path(guild_id)))
        current_state = normalize_cognitive_state(read_json_file(cognitive_state_path(guild_id)))
        recent_raw = read_jsonl(memory_raw_path(guild_id))[-MEMORY_COGNITIVE_RAW_LIMIT:]
        recent_facts = read_fact_rows(guild_id)[-4:]
        recent_questions = read_question_rows(guild_id)[-4:]

        messages = [
            {
                "role": "system",
                "content": (
                    "너는 실시간 대화 조율자다. 반드시 JSON 객체 하나만 출력한다. "
                    "형식은 {\"action\": \"answer|ask|wait\", \"confidence\": number, \"user_intent\": string, \"state_summary\": string, \"question_for_user\": string, \"main_prompt_hint\": string, \"reason_brief\": string, \"retrieved_context_ids\": string[]}. "
                    "answer는 지금 답하면 되는 경우다. ask는 사용자의 원래 발화에 이어서 짧게 되묻거나 확인 질문을 하는 편이 자연스러운 경우다. wait는 아직 단정하지 말고 더 듣거나 짧게 여지를 두는 편이 자연스러운 경우다. "
                    "question_for_user는 사용자가 한 말이 아니라, 메인 LLM이 사용자에게 되물을 내부 질문 초안이다. 절대로 사용자의 질문을 베껴 쓰거나 사용자가 이미 한 말처럼 적지 마라. "
                    "user_intent에는 사용자가 진짜로 하려는 말을 아주 짧게 적어라. state_summary에는 현재 상황을 한두 문장으로 적어라. main_prompt_hint에는 메인 LLM이 말할 때 지켜야 할 한 줄 힌트를 적어라. confidence는 0~1, reason_brief는 아주 짧게 써라. JSON 외 다른 텍스트는 절대 출력하지 마라."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"이전 cognitive_state:\n{json.dumps(current_state, ensure_ascii=False)}\n\n"
                    f"현재 rolling_summary:\n{current_summary or '(없음)'}\n\n"
                    f"최근 raw_transcript:\n{json.dumps(recent_raw, ensure_ascii=False)}\n\n"
                    f"최근 durable_facts:\n{json.dumps(recent_facts, ensure_ascii=False)}\n\n"
                    f"최근 open_questions:\n{json.dumps(recent_questions, ensure_ascii=False)}\n\n"
                    f"현재 사용자 입력:\n{clean_text(user_text)}"
                ),
            },
        ]

        try:
            result = await ask_summary_llm(
                messages,
                max_tokens=COGNITIVE_MAX_TOKENS,
                timeout_seconds=COGNITIVE_TIMEOUT_SEC,
            )
        except Exception as e:
            print(f"[COGNITIVE] 상태 업데이트 실패 또는 timeout: {e}")
            elapsed_ms = (time.monotonic() - started_at) * 1000.0
            if should_log_voice_timing(elapsed_ms):
                print(f"[COGNITIVE LATENCY] guild={guild_id} failed_after_ms={elapsed_ms:.0f}")
            fallback = current_state or {
                "action": "answer",
                "confidence": 0.5,
                "user_intent": clean_text(user_text),
                "state_summary": clean_text(user_text),
                "question_for_user": "",
                "main_prompt_hint": "짧고 자연스럽게 답해라.",
                "reason_brief": "fallback",
                "retrieved_context_ids": [],
                "updated_at": int(time.time()),
            }
            write_json_file(cognitive_state_path(guild_id), fallback)
            return fallback

        state = normalize_cognitive_state(result)
        if not state.get("state_summary"):
            state["state_summary"] = current_state.get("state_summary", "") or clean_text(user_text)
        if not state.get("main_prompt_hint"):
            state["main_prompt_hint"] = "짧고 자연스럽게 답해라."
        state["updated_at"] = int(time.time())
        write_json_file(cognitive_state_path(guild_id), state)
        elapsed_ms = (time.monotonic() - started_at) * 1000.0
        if should_log_voice_timing(elapsed_ms):
            print(f"[COGNITIVE LATENCY] guild={guild_id} action={state.get('action')} ms={elapsed_ms:.0f}")

        if state.get("action") == "ask" and state.get("question_for_user"):
            print(
                f"[COGNITIVE ASK] guild={guild_id} question={state['question_for_user']!r} reason={state.get('reason_brief', '')!r} confidence={state.get('confidence', 0.0):.2f}"
            )

        return state


async def update_long_term_memory(guild_id: int, user_text: str, answer: str) -> None:
    started_at = time.monotonic()
    lock = memory_locks.setdefault(guild_id, asyncio.Lock())
    async with lock:
        current_summary = compact_working_summary(read_text_file(memory_summary_path(guild_id)))
        recent_raw = read_jsonl(memory_raw_path(guild_id))[-MEMORY_LONGTERM_RAW_LIMIT:]
        recent_facts = read_fact_rows(guild_id)[-6:]
        recent_questions = read_question_rows(guild_id)[-4:]

        messages = [
            {
                "role": "system",
                "content": (
                    "너는 대화 장기기억 관리자이자 상황 정리자다. 반드시 JSON 객체 하나만 출력한다. "
                    "형식은 {\"summary_update\": string, \"durable_facts\": [{\"type\": string, \"text\": string}], \"open_questions\": [{\"type\": string, \"text\": string}]}. "
                    "summary_update는 지금 상황을 짧고 자연스러운 한국어로 압축한 누적 요약이다. "
                    "durable_facts에는 오래 기억할 만한 선호, 설정, 프로젝트 결정, 반복되는 사실만 넣어라. "
                    "open_questions에는 아직 확정되지 않은 추정, 확인이 필요한 질문, 다음에 물어볼 만한 포인트만 넣어라. "
                    "잡담, 일회성 노이즈, 이미 해결된 내용은 넣지 마라. JSON 외 다른 텍스트는 절대 출력하지 마라."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"현재 요약:\n{current_summary or '(없음)'}\n\n"
                    f"최근 raw_transcript:\n{json.dumps(recent_raw, ensure_ascii=False)}\n\n"
                    f"최근 durable_facts:\n{json.dumps(recent_facts, ensure_ascii=False)}\n\n"
                    f"최근 open_questions:\n{json.dumps(recent_questions, ensure_ascii=False)}\n\n"
                    f"새 대화:\nuser: {clean_text(user_text)}\nassistant: {clean_text(answer)}"
                ),
            },
        ]

        try:
            result = await ask_summary_llm(messages)
        except Exception as e:
            print(f"[MEMORY] 요약 업데이트 실패: {e}")
            elapsed_ms = (time.monotonic() - started_at) * 1000.0
            if should_log_voice_timing(elapsed_ms):
                print(f"[MEMORY LATENCY] guild={guild_id} failed_after_ms={elapsed_ms:.0f}")
            return

        summary_update = compact_working_summary(str(result.get("summary_update", "")))
        if summary_update:
            write_text_file(memory_summary_path(guild_id), summary_update)

        durable_facts = result.get("durable_facts", [])
        if isinstance(durable_facts, list):
            append_unique_memory_rows(
                memory_facts_path(guild_id),
                [row for row in durable_facts if isinstance(row, dict)],
                MEMORY_FACT_LIMIT,
                mirror_path=vault_facts_path(guild_id),
            )

        open_questions = result.get("open_questions", [])
        if isinstance(open_questions, list):
            append_unique_memory_rows(
                memory_questions_path(guild_id),
                [row for row in open_questions if isinstance(row, dict)],
                MEMORY_LOOP_LIMIT,
                mirror_path=vault_questions_path(guild_id),
            )

        elapsed_ms = (time.monotonic() - started_at) * 1000.0
        if should_log_voice_timing(elapsed_ms):
            print(f"[MEMORY LATENCY] guild={guild_id} ms={elapsed_ms:.0f}")


def schedule_memory_update(
    guild_id: int,
    user_text: str,
    answer: str,
    *,
    source: str = "chat",
    user_speaker: str = "user",
    assistant_speaker: str = "Evelyn",
) -> None:
    append_raw_transcript_rows(
        guild_id,
        [
            {"role": "user", "speaker": user_speaker, "source": source, "text": user_text},
            {"role": "assistant", "speaker": assistant_speaker, "source": source, "text": answer},
        ],
    )
    asyncio.create_task(update_long_term_memory(guild_id, user_text, answer))
    asyncio.create_task(update_cognitive_state(guild_id, user_text))


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
    text = normalize_omnivoice_tags(text)
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

    explicit_patterns = [
        r"(?:최종\s*답변|답변|response|assistant)\s*[:：]\s*([^\n]{4,120})",
        r"(?:최종\s*출력|final\s*answer)\s*[:：]\s*([^\n]{4,120})",
    ]
    for pattern in explicit_patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            candidate = strip_markdown_noise(match)
            if not candidate or not re.search(r"[가-힣]", candidate):
                continue
            if looks_like_meta_line(candidate):
                continue
            if clean_text(candidate) == clean_text(user_text):
                continue
            candidates.append(candidate)

    seen = set()
    filtered: list[str] = []
    for c in candidates:
        c = clean_text(c).strip("\"'“”‘’")
        if not c or c in seen:
            continue
        seen.add(c)
        if len(c) < 6 or len(c) > 120:
            continue
        filtered.append(c)

    return filtered[-1] if filtered else ""


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

        # 맨 앞 호출 제거: "이블린", "이블린아", "이블린,"
        pattern_front = rf"^\s*{re.escape(ww)}[야아]?\s*[, ]*"
        new_text = re.sub(pattern_front, "", text_n, count=1)
        if new_text != text_n:
            return clean_text(new_text)

        # 문장 중 첫 1회 제거
        pattern_once = rf"{re.escape(ww)}[야아]?"
        new_text = re.sub(pattern_once, "", text_n, count=1)
        if new_text != text_n:
            return clean_text(new_text)

    return clean_text(text_n)

def apply_stt_post_corrections(text: str, *, wake_detected: bool = False) -> str:
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

def compute_voice_band_metrics(audio16k: np.ndarray) -> tuple[float, float, float]:
    if audio16k.size == 0:
        return 0.0, 1.0, 0.0

    audio = np.asarray(audio16k, dtype=np.float32)
    spectrum = np.abs(np.fft.rfft(audio))
    if spectrum.size == 0:
        return 0.0, 1.0, 0.0

    freqs = np.fft.rfftfreq(len(audio), d=1.0 / TARGET_RATE)
    total_energy = float(np.sum(spectrum)) + 1e-8
    human_mask = (freqs >= 85.0) & (freqs <= 3400.0)
    human_energy = float(np.sum(spectrum[human_mask]))
    band_ratio = human_energy / total_energy

    geometric = float(np.exp(np.mean(np.log(spectrum + 1e-8))))
    arithmetic = float(np.mean(spectrum + 1e-8))
    flatness = geometric / arithmetic if arithmetic > 0 else 1.0

    rms = float(np.sqrt(np.mean(np.square(audio))))
    return band_ratio, flatness, rms


def is_likely_environment_noise(audio16k: np.ndarray) -> bool:
    band_ratio, flatness, rms = compute_voice_band_metrics(audio16k)
    return (
        rms <= VOICE_ENV_RMS_MAX
        and band_ratio < VOICE_HUMAN_BAND_RATIO_MIN
        and flatness > VOICE_ENV_FLATNESS_MAX
    )


def looks_like_repetitive_noise_text(text: str) -> bool:
    tokens = [t for t in normalize_voice_text(text).split() if t]
    if len(tokens) < 8:
        return False
    unique_ratio = len(set(tokens)) / max(1, len(tokens))
    longest_same_run = 1
    current_run = 1
    for prev, cur in zip(tokens, tokens[1:]):
        if prev == cur:
            current_run += 1
            longest_same_run = max(longest_same_run, current_run)
        else:
            current_run = 1
    return unique_ratio < 0.35 or longest_same_run >= 4


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


def get_silero_vad_model():
    global silero_vad_model

    if silero_vad_model is not None:
        return silero_vad_model

    if load_silero_vad is None or get_speech_timestamps is None:
        raise RuntimeError("silero_vad is not available")

    silero_vad_model = load_silero_vad(onnx=SILERO_VAD_ONNX)
    provider_text = ""
    if SILERO_VAD_ONNX:
        providers = getattr(getattr(silero_vad_model, "session", None), "get_providers", lambda: None)()
        if providers:
            provider_text = f" | providers={providers}"
    print(f"Silero VAD 로드 완료 | onnx={SILERO_VAD_ONNX}{provider_text}")
    return silero_vad_model


def _is_voiced_vad_chunk_energy(chunk: np.ndarray) -> bool:
    if chunk.size == 0:
        return False

    abs_chunk = np.abs(chunk)
    rms = float(np.sqrt(np.mean(np.square(chunk))))
    voiced_ratio = float(np.mean(abs_chunk > VAD_PEAK_THRESHOLD))
    return rms >= VAD_RMS_THRESHOLD and voiced_ratio >= VAD_MIN_VOICED_RATIO


def is_probably_silent_energy(audio16k: np.ndarray) -> bool:
    if audio16k.size == 0:
        return True

    chunk_samples = max(1, int(TARGET_RATE * (VAD_CHUNK_MS / 1000.0)))
    required_streak = max(1, VAD_START_CONSECUTIVE)
    voiced_streak = 0

    for start in range(0, len(audio16k), chunk_samples):
        chunk = audio16k[start:start + chunk_samples]
        if _is_voiced_vad_chunk_energy(chunk):
            voiced_streak += 1
            if voiced_streak >= required_streak:
                return False
        else:
            voiced_streak = 0

    return True


def is_probably_silent_silero(audio16k: np.ndarray) -> bool:
    if audio16k.size == 0:
        return True

    model = get_silero_vad_model()
    audio_tensor = torch.from_numpy(np.asarray(audio16k, dtype=np.float32))
    speech_timestamps = get_speech_timestamps(
        audio_tensor,
        model,
        threshold=SILERO_VAD_THRESHOLD,
        sampling_rate=TARGET_RATE,
        min_speech_duration_ms=SILERO_MIN_SPEECH_MS,
        min_silence_duration_ms=SILERO_MIN_SILENCE_MS,
        speech_pad_ms=SILERO_SPEECH_PAD_MS,
        return_seconds=False,
    )
    return len(speech_timestamps) == 0


def is_probably_silent(audio16k: np.ndarray) -> bool:
    global silero_vad_warned

    if audio16k.size == 0:
        return True

    if not VAD_ENABLED:
        return False

    if VAD_PROVIDER == "silero":
        try:
            silero_silent = is_probably_silent_silero(audio16k)
            if silero_silent:
                energy_silent = is_probably_silent_energy(audio16k)
                if not energy_silent:
                    duration_sec = len(audio16k) / float(TARGET_RATE)
                    peak = float(np.max(np.abs(audio16k))) if audio16k.size else 0.0
                    rms = float(np.sqrt(np.mean(np.square(audio16k)))) if audio16k.size else 0.0
                    print(
                        f"[VAD OVERRIDE] silero=silent energy=voiced sec={duration_sec:.2f} peak={peak:.4f} rms={rms:.4f}"
                    )
                    return False
            return silero_silent
        except Exception as e:
            if not silero_vad_warned:
                print(f"[VAD FALLBACK] Silero VAD 실패 -> energy 사용 | err={e}")
                silero_vad_warned = True
            return is_probably_silent_energy(audio16k)

    return is_probably_silent_energy(audio16k)

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

    payload = {
        "model": OMNIVOICE_MODEL,
        "input": "안녕",
        "voice": OMNIVOICE_VOICE if OMNIVOICE_VOICE else "auto",
        "response_format": "pcm",
        "stream": True,
    }
    if OMNIVOICE_LANGUAGE:
        payload["language"] = OMNIVOICE_LANGUAGE

    async with session.post(
        f"{OMNIVOICE_SERVER_URL}/v1/audio/speech",
        json=payload,
        timeout=aiohttp.ClientTimeout(total=20),
    ) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"OmniVoice warmup 실패: {resp.status} / {text[:200]}")
        async for chunk in resp.content.iter_chunked(4096):
            if chunk:
                print("OmniVoice TTS 워밍업 완료")
                break


def should_log_voice_timing(elapsed_ms: float) -> bool:
    return elapsed_ms >= VOICE_TIMING_LOG_THRESHOLD_MS


def log_voice_latency(metrics: dict | None, key: str, label: str) -> None:
    if not metrics or metrics.get(key):
        return

    started_at = metrics.get("started_at")
    if started_at is None:
        return

    elapsed_ms = (time.monotonic() - float(started_at)) * 1000.0
    metrics[key] = True
    if should_log_voice_timing(elapsed_ms):
        print(f"[VOICE LATENCY] {label}: {elapsed_ms:.0f}ms")


def log_voice_stage(metrics: dict | None, label: str, *, extra: str = "") -> None:
    if not metrics:
        return
    started_at = metrics.get("started_at")
    if started_at is None:
        return
    elapsed_ms = (time.monotonic() - float(started_at)) * 1000.0
    if not should_log_voice_timing(elapsed_ms):
        return
    suffix = f" | {extra}" if extra else ""
    print(f"[VOICE STAGE] {label}: {elapsed_ms:.0f}ms{suffix}")


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

    processor_kwargs = {
        "sampling_rate": TARGET_RATE,
        "return_tensors": "pt",
    }
    if STT_FORCE_LANGUAGE:
        processor_kwargs["language"] = STT_LANGUAGE

    inputs = processor(
        audio16k,
        **processor_kwargs,
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

    generate_kwargs = {
        "max_new_tokens": max_new_tokens,
    }

    if STT_FORCE_LANGUAGE and hasattr(processor, "get_decoder_prompt_ids"):
        try:
            decoder_prompt_ids = processor.get_decoder_prompt_ids(
                language=STT_LANGUAGE,
                punctuation=STT_FORCE_PUNCTUATION,
            )
            if decoder_prompt_ids:
                moved["decoder_input_ids"] = torch.tensor(
                    [decoder_prompt_ids],
                    device=model.device,
                    dtype=torch.long,
                )
        except Exception as e:
            print(f"[STT] decoder prompt 강제 실패 | err={e}")

    with torch.inference_mode():
        outputs = model.generate(**moved, **generate_kwargs)

    text = processor.decode(outputs[0], skip_special_tokens=True)
    return clean_text(text)


def transcribe_voice_sync(pcm_bytes: bytes) -> str:
    return transcribe_audio16k_sync(prepare_stt_audio(pcm_bytes), max_new_tokens=VOICE_STT_MAX_NEW_TOKENS)


def detect_wake_word_sync(audio16k: np.ndarray) -> tuple[bool, str]:
    wake_audio = slice_audio_window(audio16k, WAKE_AUDIO_SEC)
    wake_text = transcribe_audio16k_sync(wake_audio, max_new_tokens=WAKE_MAX_TOKENS)
    return contains_leading_wake_word(wake_text), wake_text


# =========================================================
# 디스코드 음성
# =========================================================
async def connect_evelyn_voice_client(target_channel: discord.VoiceChannel) -> EvelynVoiceClient:
    last_error: Exception | None = None

    for attempt in range(1, VOICE_CONNECT_RETRIES + 1):
        try:
            print(
                f"[VOICE CONNECT] attempt={attempt}/{VOICE_CONNECT_RETRIES} channel={target_channel.name} timeout={VOICE_CONNECT_TIMEOUT}"
            )
            vc = await target_channel.connect(
                cls=EvelynVoiceClient,
                timeout=VOICE_CONNECT_TIMEOUT,
                reconnect=False,
            )
            if not isinstance(vc, EvelynVoiceClient):
                raise RuntimeError(f"unexpected voice client type: {type(vc)!r}")
            return vc
        except Exception as e:
            last_error = e
            print(
                f"[VOICE CONNECT FAIL] attempt={attempt}/{VOICE_CONNECT_RETRIES} channel={target_channel.name} err={e!r}"
            )

            stale_vc = target_channel.guild.voice_client
            if stale_vc is not None:
                try:
                    await stale_vc.disconnect(force=True)
                except Exception:
                    pass

            try:
                await target_channel.guild.change_voice_state(channel=None, self_deaf=False, self_mute=False)
            except Exception:
                pass

            if attempt < VOICE_CONNECT_RETRIES:
                await asyncio.sleep(VOICE_CONNECT_RETRY_DELAY_SEC)

    assert last_error is not None
    raise last_error


async def ensure_listening_voice_client(guild: discord.Guild, target_channel: discord.VoiceChannel) -> Optional[EvelynVoiceClient]:
    vc = guild.voice_client

    if vc is not None and not isinstance(vc, EvelynVoiceClient):
        await vc.disconnect(force=True)
        vc = None

    if vc is None:
        vc = await connect_evelyn_voice_client(target_channel)
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


async def ask_llm_once(
    user_text: str,
    guild_id: int | None = None,
    *,
    source: str = "text",
    debug_text: str | None = None,
) -> str:
    messages, cognitive_state, _route = await prepare_llm_messages(
        user_text,
        guild_id=guild_id,
        source=source,
        debug_text=debug_text,
    )

    final_user_text = f"{user_text}\n\n{build_main_response_guidance(cognitive_state, source=source)}"

    payload = {
        "model": MODEL_NAME,
        "messages": messages + [{"role": "user", "content": final_user_text}],
        "temperature": 0.1,
        "max_tokens": VOICE_LLM_MAX_TOKENS,
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


def _extract_text_payload(value) -> str:
    if isinstance(value, str):
        return value

    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)

    return ""


def extract_stream_delta_text(data: dict) -> str:
    choices = data.get("choices", [])
    if not choices:
        return ""

    choice = choices[0]
    delta = choice.get("delta") or {}
    content = _extract_text_payload(delta.get("content"))
    if content:
        return content

    message = choice.get("message") or {}
    content = _extract_text_payload(message.get("content"))
    if content:
        return content

    text = choice.get("text")
    return text if isinstance(text, str) else ""


def extract_stream_reasoning_text(data: dict) -> str:
    choices = data.get("choices", [])
    if not choices:
        return ""

    choice = choices[0]
    delta = choice.get("delta") or {}
    reasoning = _extract_text_payload(delta.get("reasoning_content"))
    if reasoning:
        return reasoning

    reasoning = _extract_text_payload(delta.get("reasoning"))
    if reasoning:
        return reasoning

    message = choice.get("message") or {}
    reasoning = _extract_text_payload(message.get("reasoning_content"))
    if reasoning:
        return reasoning

    reasoning = _extract_text_payload(message.get("reasoning"))
    return reasoning


async def ask_llm_streaming(
    user_text: str,
    guild_id: int | None = None,
    on_sentence: Callable[[str], Awaitable[None]] | None = None,
    on_first_chunk: Callable[[], None] | None = None,
    *,
    source: str = "text",
    debug_text: str | None = None,
) -> str:
    messages, cognitive_state, _route = await prepare_llm_messages(
        user_text,
        guild_id=guild_id,
        source=source,
        debug_text=debug_text,
    )

    final_user_text = f"{user_text}\n\n{build_main_response_guidance(cognitive_state, source=source)}"

    payload = {
        "model": MODEL_NAME,
        "messages": messages + [{"role": "user", "content": final_user_text}],
        "temperature": 0.1,
        "max_tokens": VOICE_LLM_MAX_TOKENS,
        "stream": True,
    }

    timeout = aiohttp.ClientTimeout(total=120)
    session = await get_http_session()
    raw_parts: list[str] = []
    reasoning_parts: list[str] = []
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
                print("[LLM STREAM] json 응답 본문 비어 있음, non-stream 재시도")
                answer = await ask_llm_once(user_text, guild_id=guild_id, source=source, debug_text=debug_text)
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
            reasoning_text = extract_stream_reasoning_text(data)
            if reasoning_text:
                reasoning_parts.append(reasoning_text)

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
        print(
            f"[LLM STREAM] stream 본문 비어 있음, non-stream 재시도 | raw_len={len(''.join(raw_parts))} reasoning_len={len(''.join(reasoning_parts))} emitted_any={emitted_any}"
        )
        answer = await ask_llm_once(user_text, guild_id=guild_id, source=source, debug_text=debug_text)

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
    *,
    source: str = "voice",
    debug_text: str | None = None,
) -> str:
    metrics = {
        "started_at": time.monotonic(),
        "llm_first_chunk_logged": False,
        "tts_first_byte_logged": False,
        "playback_start_logged": False,
    }
    log_voice_stage(metrics, "LLM/TTS 파이프라인 시작", extra=f"source={source}")
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
            source=source,
            debug_text=debug_text,
        )
        log_voice_stage(metrics, "LLM 완료", extra=f"chars={len(answer)}")
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
    metrics = {"started_at": time.monotonic()}
    log_voice_stage(metrics, "process_member_audio 시작", extra=f"speaker={member.display_name} pcm_bytes={len(pcm_bytes)}")

    audio16k = prepare_stt_audio(pcm_bytes)
    if audio16k.size == 0:
        log_voice_stage(metrics, "오디오 비어있음")
        return

    if VAD_ENABLED and is_probably_silent(audio16k):
        duration_sec = len(audio16k) / float(TARGET_RATE)
        peak = float(np.max(np.abs(audio16k))) if audio16k.size else 0.0
        rms = float(np.sqrt(np.mean(np.square(audio16k)))) if audio16k.size else 0.0
        print(f"[VAD IGNORE] speaker={member.display_name} sec={duration_sec:.2f} peak={peak:.4f} rms={rms:.4f}")
        log_voice_stage(metrics, "VAD 무음 판정", extra=f"sec={duration_sec:.2f} peak={peak:.4f} rms={rms:.4f}")
        return

    log_voice_stage(metrics, "STT 시작", extra=f"samples={audio16k.size}")
    try:
        text = await asyncio.to_thread(transcribe_audio16k_sync, audio16k, VOICE_STT_MAX_NEW_TOKENS)
    except Exception as e:
        print(f"❌ [STT] {e}")
        log_voice_stage(metrics, "STT 실패", extra=repr(e))
        return

    log_voice_stage(metrics, "STT 완료", extra=f"text_len={len(text)}")

    if not text:
        log_voice_stage(metrics, "STT 빈 결과")
        return

    corrected_text = apply_stt_post_corrections(text, wake_detected=False)
    wake_detected = contains_leading_wake_word(strip_leading_voice_fillers(corrected_text))
    wake_probe = corrected_text
    text = corrected_text

    if is_likely_environment_noise(audio16k):
        band_ratio, flatness, rms = compute_voice_band_metrics(audio16k)
        print(
            f"[ENV IGNORE] speaker={member.display_name} band_ratio={band_ratio:.3f} flatness={flatness:.3f} rms={rms:.4f} text={text!r}"
        )
        return

    if looks_like_repetitive_noise_text(text):
        print(f"[NOISE TEXT IGNORE] speaker={member.display_name} text={text!r}")
        return

    if not wake_detected:
        if wake_probe:
            print(f"[WAKE IGNORE] {member.display_name}: {wake_probe!r}")
        log_voice_stage(metrics, "웨이크 미검출", extra=f"probe={wake_probe!r}")
        return

    corrected_text = apply_stt_post_corrections(text, wake_detected=wake_detected)
    if corrected_text != text:
        print(f"[STT CORRECT] raw={text!r} -> corrected={corrected_text!r}")
    text = corrected_text

    if should_ignore_short_transcription(text, pcm_bytes, wake_detected=wake_detected):
        print(f"[STT IGNORE] short_noise: {text!r}")
        log_voice_stage(metrics, "짧은 STT 무시", extra=f"text={text!r}")
        return

    print(f"🎤 [{member.display_name}] wake={wake_probe!r} text={text}")

    ok, reason = should_reply_to_voice(guild_id, text, wake_detected=True)
    if not ok:
        print(f"[STT IGNORE] {reason}: {text!r}")
        log_voice_stage(metrics, "응답 차단", extra=f"reason={reason}")
        return

    log_voice_stage(metrics, "웨이크 통과", extra=f"user_text={strip_voice_wake_word(text)!r}")

    raw_user_text = strip_voice_wake_word(text)
    prompt_user_text = raw_user_text or "사용자가 너를 이름만 불렀다. 아주 짧고 자연스럽게 반응해라."
    history_user_text = raw_user_text or text
    lock = guild_locks.setdefault(guild_id, asyncio.Lock())

    if lock.locked():
        print(f"[VOICE WAIT] guild={guild_id} speaker={member.display_name} text={history_user_text!r}")
        log_voice_stage(metrics, "길드 락 대기", extra=f"guild={guild_id}")

    async with lock:
        log_voice_stage(metrics, "길드 락 획득", extra=f"guild={guild_id}")
        vc = guild.voice_client
        if vc is None:
            return

        async def on_final_answer(answer_text: str) -> None:
            print(f"💬 [Evelyn] {visible_text(answer_text)}")

        try:
            answer = await ask_llm_and_speak_streaming(
                vc,
                prompt_user_text,
                guild_id=guild_id,
                on_final_answer=on_final_answer,
                source="voice",
                debug_text=history_user_text,
            )
            log_voice_stage(metrics, "LLM/TTS 완료", extra=f"answer_len={len(answer)}")
        except Exception as e:
            print(f"❌ [LLM/TTS] {e}")
            return

        answer = clean_text(answer)
        if not answer:
            log_voice_stage(metrics, "최종 답변 비어있음")
            return

        plain_answer = strip_omnivoice_tags(answer)
        if not plain_answer:
            plain_answer = answer

        append_history(history_user_text, plain_answer)
        schedule_memory_update(
            guild_id,
            history_user_text,
            plain_answer,
            source="voice",
            user_speaker=member.display_name,
            assistant_speaker="Evelyn",
        )
        log_voice_stage(metrics, "process_member_audio 완료", extra=f"speaker={member.display_name}")


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

                answer = await ask_llm_once(user_text, guild_id=message.guild.id, source="text", debug_text=user_text)
                plain_answer = strip_omnivoice_tags(answer)
                if not plain_answer:
                    plain_answer = answer

                await message.channel.send(visible_text(answer))

            append_history(user_text, plain_answer)
            schedule_memory_update(
                message.guild.id,
                user_text,
                plain_answer,
                source="text",
                user_speaker=message.author.display_name,
                assistant_speaker="Evelyn",
            )

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
