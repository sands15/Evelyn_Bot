import os
from pathlib import Path

# Discord bot token used to log in the Evelyn bot.
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# Main LLM endpoint for normal user-facing replies.
LLM_SERVER_URL = os.getenv("LLM_SERVER_URL", "http://127.0.0.1:9820/v1/chat/completions")
# Main model name sent to the primary LLM server.
MODEL_NAME = os.getenv("LLM_MODEL_NAME", "Qwen3-8B-Q4_K_M.gguf")

# OmniVoice HTTP endpoint for speech synthesis.
OMNIVOICE_SERVER_URL = os.getenv("OMNIVOICE_SERVER_URL", "http://127.0.0.1:8880")
# TTS model id used by the OmniVoice server.
OMNIVOICE_MODEL = os.getenv("OMNIVOICE_MODEL", "omnivoice")
# Preferred voice profile. Clone voices can fall back to auto if missing.
OMNIVOICE_VOICE = os.getenv("OMNIVOICE_VOICE", "clone:evelyn")
# Language hint sent to OmniVoice.
OMNIVOICE_LANGUAGE = os.getenv("OMNIVOICE_LANGUAGE", "ko")
# Whether to stream TTS audio instead of waiting for the whole file.
OMNIVOICE_STREAM = os.getenv("OMNIVOICE_STREAM", "true").lower() == "true"
# Total timeout for one TTS request.
OMNIVOICE_TIMEOUT_SEC = float(os.getenv("OMNIVOICE_TIMEOUT_SEC", "180"))

# Secondary LLM endpoint used for memory updates and cognitive routing.
SUMMARY_LLM_URL = os.getenv("SUMMARY_LLM_URL", "http://127.0.0.1:9821/v1/chat/completions")
# Secondary model name used for summaries / cognitive state.
SUMMARY_MODEL_NAME = os.getenv("SUMMARY_MODEL_NAME", "Qwen3.6-35B-A3B-UD-Q3_K_XL.gguf")
# Root directory for per-guild memory files.
MEMORY_ROOT = Path(os.getenv("BOT_MEMORY_DIR", str(Path(__file__).resolve().parent / "bot_memory")))
# Max durable fact rows kept in the working memory file.
MEMORY_FACT_LIMIT = int(os.getenv("MEMORY_FACT_LIMIT", "200"))
# Max open loop / question rows kept locally.
MEMORY_LOOP_LIMIT = int(os.getenv("MEMORY_LOOP_LIMIT", "100"))
# Max rolling raw transcript rows kept in the hot file.
MEMORY_RAW_LIMIT = int(os.getenv("MEMORY_RAW_LIMIT", "400"))
# How many recent raw rows to place directly into normal prompt context.
MEMORY_RAW_CONTEXT_LIMIT = int(os.getenv("MEMORY_RAW_CONTEXT_LIMIT", "6"))
# Max memory rows retrieved into prompt context.
MEMORY_RETRIEVE_LIMIT = int(os.getenv("MEMORY_RETRIEVE_LIMIT", "8"))
# Max chars kept in the compact rolling summary.
MEMORY_WORKING_SUMMARY_MAX_CHARS = int(os.getenv("MEMORY_WORKING_SUMMARY_MAX_CHARS", "700"))
# Default max chars for one memory row snippet.
MEMORY_ROW_MAX_CHARS = int(os.getenv("MEMORY_ROW_MAX_CHARS", "120"))
# Recent raw rows shown to the cognitive model.
MEMORY_COGNITIVE_RAW_LIMIT = int(os.getenv("MEMORY_COGNITIVE_RAW_LIMIT", "4"))
# Recent raw rows shown to the long-term memory updater.
MEMORY_LONGTERM_RAW_LIMIT = int(os.getenv("MEMORY_LONGTERM_RAW_LIMIT", "6"))
# Older vault raw rows retrieved back into context.
MEMORY_VAULT_RAW_RETRIEVE_LIMIT = int(os.getenv("MEMORY_VAULT_RAW_RETRIEVE_LIMIT", "4"))
# Number of daily raw vault files to scan.
MEMORY_VAULT_DAYS = int(os.getenv("MEMORY_VAULT_DAYS", "7"))
# Max tokens for the cognitive sub-model call.
COGNITIVE_MAX_TOKENS = int(os.getenv("COGNITIVE_MAX_TOKENS", "120"))
# Timeout for the cognitive sub-model call.
COGNITIVE_TIMEOUT_SEC = float(os.getenv("COGNITIVE_TIMEOUT_SEC", "8"))
# Minimum confidence for text-mode ask behavior.
ASK_CONFIDENCE_THRESHOLD_TEXT = float(os.getenv("ASK_CONFIDENCE_THRESHOLD_TEXT", "0.30"))
# Minimum confidence for voice-mode ask behavior.
ASK_CONFIDENCE_THRESHOLD_VOICE = float(os.getenv("ASK_CONFIDENCE_THRESHOLD_VOICE", "0.30"))

# Speech-to-text model id.
STT_MODEL_NAME = os.getenv("STT_MODEL_NAME", "CohereLabs/cohere-transcribe-03-2026")
# Language hint forced into STT decoding.
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "ko")
# Compute dtype for the STT model.
STT_COMPUTE_TYPE = os.getenv("STT_COMPUTE_TYPE", "float16")
# Whether to force the language prompt for STT.
STT_FORCE_LANGUAGE = os.getenv("STT_FORCE_LANGUAGE", "true").lower() == "true"
# Whether to force punctuation in the decoder prompt.
STT_FORCE_PUNCTUATION = os.getenv("STT_FORCE_PUNCTUATION", "true").lower() == "true"

# Master switch for VAD filtering before STT.
VAD_ENABLED = os.getenv("VAD_ENABLED", "true").lower() == "true"
# VAD backend selector. Currently expects silero or energy fallback behavior.
VAD_PROVIDER = os.getenv("VAD_PROVIDER", "silero").lower()
# RMS threshold for the lightweight energy-based VAD fallback.
VAD_RMS_THRESHOLD = float(os.getenv("VAD_RMS_THRESHOLD", "0.008"))
# Peak amplitude threshold for the lightweight energy-based VAD fallback.
VAD_PEAK_THRESHOLD = float(os.getenv("VAD_PEAK_THRESHOLD", "0.020"))
# Minimum voiced-sample ratio for the energy VAD fallback.
VAD_MIN_VOICED_RATIO = float(os.getenv("VAD_MIN_VOICED_RATIO", "0.015"))
# Chunk size in ms used by the energy VAD fallback.
VAD_CHUNK_MS = float(os.getenv("VAD_CHUNK_MS", "32"))
# How many voiced chunks in a row are required to count as speech start.
VAD_START_CONSECUTIVE = int(os.getenv("VAD_START_CONSECUTIVE", "2"))
# Spectral flatness threshold for classifying environment-like noise.
VOICE_ENV_FLATNESS_MAX = float(os.getenv("VOICE_ENV_FLATNESS_MAX", "0.72"))
# Minimum voice-band energy ratio required to avoid environment-noise rejection.
VOICE_HUMAN_BAND_RATIO_MIN = float(os.getenv("VOICE_HUMAN_BAND_RATIO_MIN", "0.38"))
# Maximum RMS that still counts as low-level environment noise.
VOICE_ENV_RMS_MAX = float(os.getenv("VOICE_ENV_RMS_MAX", "0.020"))
# Silero confidence threshold for speech detection.
SILERO_VAD_THRESHOLD = float(os.getenv("SILERO_VAD_THRESHOLD", "0.30"))
# Minimum speech duration fed into Silero timestamps.
SILERO_MIN_SPEECH_MS = int(os.getenv("SILERO_MIN_SPEECH_MS", "32"))
# Minimum silence duration for segment splitting in Silero.
SILERO_MIN_SILENCE_MS = int(os.getenv("SILERO_MIN_SILENCE_MS", "0"))
# Padding added around Silero speech segments.
SILERO_SPEECH_PAD_MS = int(os.getenv("SILERO_SPEECH_PAD_MS", "80"))
# Whether to prefer the ONNX Silero runtime on CPU.
SILERO_VAD_ONNX = os.getenv("SILERO_VAD_ONNX", "true").lower() == "true"

# Master switch for lightweight denoise before STT.
DENOISE_ENABLED = os.getenv("DENOISE_ENABLED", "true").lower() == "true"
# High-pass cutoff frequency for speech cleanup.
DENOISE_HIGHPASS_HZ = float(os.getenv("DENOISE_HIGHPASS_HZ", "120"))
# Leading audio window used to estimate the noise floor.
DENOISE_NOISE_FLOOR_SEC = float(os.getenv("DENOISE_NOISE_FLOOR_SEC", "0.20"))
# Multiplier applied to the estimated denoise gate threshold.
DENOISE_GATE_MULT = float(os.getenv("DENOISE_GATE_MULT", "1.35"))
# Number of seconds used for the wake probe STT slice.
WAKE_AUDIO_SEC = float(os.getenv("WAKE_AUDIO_SEC", "1.4"))
# Max tokens for the wake probe STT pass.
WAKE_MAX_TOKENS = int(os.getenv("WAKE_MAX_TOKENS", "48"))
# Fuzzy matching threshold for wake-word normalization.
WAKE_FUZZY_THRESHOLD = float(os.getenv("WAKE_FUZZY_THRESHOLD", "0.72"))
# Minimum short wake text length kept after wake detection.
WAKE_SHORT_TEXT_KEEP_LEN = int(os.getenv("WAKE_SHORT_TEXT_KEEP_LEN", "2"))
# Early TTS sentence target used by streaming sentence splitting.
TTS_EARLY_CHUNK_LEN = int(os.getenv("TTS_EARLY_CHUNK_LEN", "14"))
# Minimum chars before forcing an early TTS cut.
TTS_EARLY_CUT_MIN = int(os.getenv("TTS_EARLY_CUT_MIN", "6"))
# Max tokens for one full voice STT pass.
VOICE_STT_MAX_NEW_TOKENS = int(os.getenv("VOICE_STT_MAX_NEW_TOKENS", "256"))
# Max tokens for one main LLM reply.
VOICE_LLM_MAX_TOKENS = int(os.getenv("VOICE_LLM_MAX_TOKENS", "320"))

# Hard cap on retained conversation turns in memory.
MAX_HISTORY_ITEMS = 1024
# Voice history tail size. Kept configurable even if aligned to MAX_HISTORY_ITEMS.
VOICE_HISTORY_LIMIT = int(os.getenv("VOICE_HISTORY_LIMIT", str(MAX_HISTORY_ITEMS)))
# Max visible chars when echoing answer text into chat.
MAX_VISIBLE_TEXT = 1800
# Whether text messages should auto-join the caller's voice channel.
AUTO_JOIN_VOICE = os.getenv("AUTO_JOIN_VOICE", "true").lower() == "true"

# Minimum text length before non-wake voice gets discarded.
MIN_TEXT_LEN = int(os.getenv("VOICE_MIN_TEXT_LEN", "4"))
# Minimum transcription length allowed for very short audio.
MIN_TRANSCRIBED_LEN = int(os.getenv("VOICE_MIN_TRANSCRIBED_LEN", "6"))
# Minimum audio duration before short text is considered suspicious noise.
MIN_AUDIO_SEC = float(os.getenv("VOICE_MIN_AUDIO_SEC", "0.6"))
# Cooldown between spoken replies per guild.
REPLY_COOLDOWN_SEC = float(os.getenv("VOICE_REPLY_COOLDOWN_SEC", "2.5"))
# Ignore window right after bot TTS ends to avoid self-trigger loops.
POST_TTS_IGNORE_SEC = float(os.getenv("VOICE_POST_TTS_IGNORE_SEC", "1.2"))
# Similarity threshold for duplicate voice suppression.
SIMILARITY_BLOCK = float(os.getenv("VOICE_SIMILARITY_BLOCK", "0.88"))
# Timeout for one Discord voice connect attempt.
VOICE_CONNECT_TIMEOUT = float(os.getenv("VOICE_CONNECT_TIMEOUT", "45"))
# Number of connect attempts before giving up.
VOICE_CONNECT_RETRIES = max(1, int(os.getenv("VOICE_CONNECT_RETRIES", "2")))
# Delay between repeated voice connect attempts.
VOICE_CONNECT_RETRY_DELAY_SEC = float(os.getenv("VOICE_CONNECT_RETRY_DELAY_SEC", "1.5"))
# Latency threshold after which detailed timing logs are printed.
VOICE_TIMING_LOG_THRESHOLD_MS = float(os.getenv("VOICE_TIMING_LOG_THRESHOLD_MS", "3000"))
# Accepted wake-word variants. Normalization later maps noisy STT back to the canonical name.
WAKE_WORDS = [
    w.strip()
    for w in os.getenv(
        "WAKE_WORDS",
        "이별인,이별링,이벨링,에벌링,이블린,이불린,이불링,이브린,이브링,입을린,입을링,이블닝,이블링,이별린,이벌린,에블린,에브린,에블링,에브링,에벌린,이벨린,이반린,불리읍,이블리,이별된,이벨리나,이별레인"
    ).split(",")
    if w.strip()
]

# Discord receive PCM sample rate.
RATE = 48000
# Discord receive channel count.
CHANNELS = 2
# Target sample rate for STT and voice analysis.
TARGET_RATE = 16000
# Discord playback sample rate.
DISCORD_PCM_RATE = 48000
# Discord playback channel count.
DISCORD_PCM_CHANNELS = 2
# OmniVoice raw PCM sample rate.
OMNIVOICE_PCM_RATE = 24000
# OmniVoice raw PCM channel count.
OMNIVOICE_PCM_CHANNELS = 1
# One Discord PCM frame size in bytes.
DISCORD_FRAME_BYTES = 3840

# Allowed inline emotion tags that can survive cleanup and be sent to OmniVoice.
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

# Shared prompt hint for how the main LLM should use OmniVoice tags.
OMNIVOICE_TAG_GUIDANCE = (
    "필요할 때만 OmniVoice 감정 태그를 매우 짧게 써도 된다. "
    "허용 태그는 [laughter], [sigh], [confirmation-en], [question-en], [question-ah], [question-oh], [question-ei], [question-yi], [surprise-ah], [surprise-oh], [surprise-wa], [surprise-yo], [dissatisfaction-hnn] 뿐이다. "
    "태그는 문장 앞이나 짧은 감탄 앞에 자연스럽게 붙이고, 보통 답변 전체에서 0개 또는 1개만 쓰고 남용하지 마라. "
    "한 문장에 여러 태그를 연달아 붙이지 마라. 태그는 말투를 보조할 때만 써라."
)

# Base system prompt for the bot persona and output constraints.
SYSTEM_PROMPT = (
    "너는 사용자의 친구 이블린이야, 나이는 20살이고 친절하고 상냥해 "
    "항상 자연스러운 한국어로만 답한다. "
    "반드시 최종 답변만 바로 출력한다. "
    "<think>, reasoning, thinking process, memo, bullet, 사용자 분석, 초안은 절대 출력하지 않는다. "
    "질문에는 한 문장 또는 두 문장으로 짧고 자연스럽게 답한다. "
    "OmniVoice 감정 태그를 쓸 수 있다. 허용 태그는 [laughter], [sigh], [confirmation-en], [question-en], [question-ah], [question-oh], [question-ei], [question-yi], [surprise-ah], [surprise-oh], [surprise-wa], [surprise-yo], [dissatisfaction-hnn] 뿐이다. "
    "감정이 자연스럽게 들릴 때만 태그를 짧게 붙이고, 남용하지 마라. 태그 외 다른 대괄호 표현은 절대 쓰지 마라."
)
