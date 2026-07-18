import os
from pathlib import Path

from evelyn_core.paths import get_repo_root, get_runtime_artifacts_root

try:
    import winreg
except Exception:  # pragma: no cover - non-Windows fallback
    winreg = None


REPO_ROOT = get_repo_root()
MINEFLAYER_PROFILE_DIR = REPO_ROOT / "bot_profiles"

# Voyager Minecraft service is often launched directly from Python helpers,
# so keep the long-used Mineflayer account defaults here unless the caller
# explicitly overrides them through environment variables.
os.environ.setdefault("MINEFLAYER_HOST", "127.0.0.1")
os.environ.setdefault("MINEFLAYER_PORT", "25565")
os.environ.setdefault("MINEFLAYER_USERNAME", "Evelyn_0428")
os.environ.setdefault("MINEFLAYER_AUTH", "microsoft")
os.environ.setdefault("MINEFLAYER_PROFILES_FOLDER", str(MINEFLAYER_PROFILE_DIR))


def _env(name: str, default: str | None = None, *aliases: str) -> str | None:
    for key in (name, *aliases):
        value = os.getenv(key)
        if value:
            return value
    if winreg is not None:
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for subkey in ("Environment", r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"):
                try:
                    with winreg.OpenKey(root, subkey) as handle:
                        for key in (name, *aliases):
                            try:
                                value, _ = winreg.QueryValueEx(handle, key)
                            except FileNotFoundError:
                                continue
                            if value:
                                return str(value)
                except Exception:
                    continue
    return default


def _env_flag(name: str, default: str = "false") -> bool:
    value = _env(name)
    if value is None:
        value = default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _env_int_set(name: str, default: str | None = None, *aliases: str) -> set[int]:
    raw = _env(name, default, *aliases)
    if not raw:
        return set()
    parsed: set[int] = set()
    for chunk in str(raw).replace(";", ",").split(","):
        token = chunk.strip()
        if not token:
            continue
        try:
            parsed.add(int(token))
        except (TypeError, ValueError):
            continue
    return parsed

# Evelyn 봇이 디스코드에 로그인할 때 쓰는 토큰.
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")


# 사용자 응답용 메인 LLM 서버 엔드포인트.
LLM_SERVER_URL = os.getenv("LLM_SERVER_URL", "http://127.0.0.1:9820/v1/chat/completions")
# 메인 LLM 서버에 전달할 모델 이름.
MODEL_NAME = os.getenv("LLM_MODEL_NAME", "kanana-1.5-8b-instruct-2505-q4_k_m+evelyn-core-clean-v46-lora")
MAIN_LLM_CHAT_CONTENT_FORMAT = os.getenv("MAIN_LLM_CHAT_CONTENT_FORMAT", "openai")
MAIN_LLM_STOP_TOKENS = tuple(
    token.strip()
    for token in os.getenv("MAIN_LLM_STOP_TOKENS", "<|eot_id|>,<|end_of_text|>").split(",")
    if token.strip()
)


# OmniVoice TTS 서버 주소.
OMNIVOICE_SERVER_URL = os.getenv("OMNIVOICE_SERVER_URL", "http://127.0.0.1:8880")
# TTS에 사용할 OmniVoice 모델 id.
OMNIVOICE_MODEL = os.getenv("OMNIVOICE_MODEL", "omnivoice")
# 선호하는 음성 프로필. clone 프로필이 없으면 auto로 fallback 가능.
OMNIVOICE_VOICE = os.getenv("OMNIVOICE_VOICE", "clone:evelyn")
# OmniVoice에 전달하는 언어 힌트.
OMNIVOICE_LANGUAGE = os.getenv("OMNIVOICE_LANGUAGE", "ko")
# TTS를 한 번에 다 받지 않고 스트리밍 재생할지 여부.
OMNIVOICE_STREAM = os.getenv("OMNIVOICE_STREAM", "true").lower() == "true"
# TTS 한 요청의 전체 타임아웃.
OMNIVOICE_TIMEOUT_SEC = float(os.getenv("OMNIVOICE_TIMEOUT_SEC", "180"))
OMNIVOICE_STREAM_STRATEGY = os.getenv("OMNIVOICE_STREAM_STRATEGY", "blockwise_capped_first")
OMNIVOICE_STREAM_FOLLOWUP_STRATEGY = os.getenv("OMNIVOICE_STREAM_FOLLOWUP_STRATEGY", "blockwise_capped_first")
OMNIVOICE_STREAM_BLOCK_SIZE = int(os.getenv("OMNIVOICE_STREAM_BLOCK_SIZE", "16"))
OMNIVOICE_NUM_STEP = int(os.getenv("OMNIVOICE_NUM_STEP", "16"))
OMNIVOICE_STREAM_FIRST_BLOCK_STEPS = int(os.getenv("OMNIVOICE_STREAM_FIRST_BLOCK_STEPS", "8"))
OMNIVOICE_STREAM_BLOCK_STEPS = int(os.getenv("OMNIVOICE_STREAM_BLOCK_STEPS", "10"))
OMNIVOICE_STREAM_FIRST_IMMEDIATE_CAP_MS = float(os.getenv("OMNIVOICE_STREAM_FIRST_IMMEDIATE_CAP_MS", "250"))
OMNIVOICE_STREAM_LOOKAHEAD_CROSSFADE_MS = float(os.getenv("OMNIVOICE_STREAM_LOOKAHEAD_CROSSFADE_MS", "0"))
OMNIVOICE_PLAYBACK_START_BUFFER_MS = float(os.getenv("OMNIVOICE_PLAYBACK_START_BUFFER_MS", "1400"))
OMNIVOICE_PLAYBACK_ADAPTIVE_JITTER = _env_flag("OMNIVOICE_PLAYBACK_ADAPTIVE_JITTER", "true")
OMNIVOICE_PLAYBACK_MIN_BUFFER_MS = float(os.getenv("OMNIVOICE_PLAYBACK_MIN_BUFFER_MS", "700"))
OMNIVOICE_PLAYBACK_MAX_BUFFER_MS = float(os.getenv("OMNIVOICE_PLAYBACK_MAX_BUFFER_MS", "2600"))
OMNIVOICE_PLAYBACK_GAP_SAFETY_MS = float(os.getenv("OMNIVOICE_PLAYBACK_GAP_SAFETY_MS", "220"))
OMNIVOICE_PLAYBACK_GAP_MULTIPLIER = float(os.getenv("OMNIVOICE_PLAYBACK_GAP_MULTIPLIER", "1.0"))
OMNIVOICE_PLAYBACK_BLOCK_GAP_MIN_MS = float(os.getenv("OMNIVOICE_PLAYBACK_BLOCK_GAP_MIN_MS", "250"))
OMNIVOICE_SPEED = float(os.getenv("OMNIVOICE_SPEED", "1.0"))
TTS_CHUNK_TAIL_SILENCE_MS = int(os.getenv("TTS_CHUNK_TAIL_SILENCE_MS", "120"))
LOCAL_TTS_TAIL_SILENCE_MS = int(os.getenv("LOCAL_TTS_TAIL_SILENCE_MS", "180"))
TTS_PLAYBACK_START_LOOKAHEAD_CHUNKS = int(os.getenv("TTS_PLAYBACK_START_LOOKAHEAD_CHUNKS", "2"))
TTS_PLAYBACK_START_LOOKAHEAD_TIMEOUT_MS = float(os.getenv("TTS_PLAYBACK_START_LOOKAHEAD_TIMEOUT_MS", "350"))


# 메모리 업데이트와 cognitive 판단에 쓰는 서브 LLM 서버 엔드포인트.
SUMMARY_LLM_URL = os.getenv("SUMMARY_LLM_URL", "http://127.0.0.1:9821/v1/chat/completions")
# 요약/상황판단용 서브 모델 이름.
SUMMARY_MODEL_NAME = os.getenv("SUMMARY_MODEL_NAME", "gemma-4-E4B-it-Q4_K_M-text-only")
# 라우터 LLM 서버 엔드포인트.
ROUTER_LLM_URL = os.getenv("ROUTER_LLM_URL", "http://127.0.0.1:9822/v1/chat/completions")
# 라우터 모델 이름.
ROUTER_MODEL_NAME = os.getenv("ROUTER_MODEL_NAME", "gemma-4-E2B-it-Q4_K_M-text-only")
# router LLM 사용 여부.
ROUTER_LLM_ENABLED = _env_flag("ROUTER_LLM_ENABLED", "true")
# route 분류 최대 토큰 수.
ROUTER_ROUTE_MAX_TOKENS = int(os.getenv("ROUTER_ROUTE_MAX_TOKENS", "220"))
# route 분류 타임아웃.
ROUTER_ROUTE_TIMEOUT_SEC = float(os.getenv("ROUTER_ROUTE_TIMEOUT_SEC", "8"))
# 길드별 메모리 파일을 저장하는 루트 디렉터리.
MEMORY_ROOT = Path(os.getenv("BOT_MEMORY_DIR", str(REPO_ROOT / "bot_memory")))
RUNTIME_ARTIFACTS_ROOT = get_runtime_artifacts_root()
# 길드별 설정(prefix 등)을 저장하는 루트 디렉터리.
GUILD_SETTINGS_ROOT = Path(os.getenv("GUILD_SETTINGS_DIR", str(REPO_ROOT / "guild_settings")))
# 기본 명령어 시작 부호(prefix).
DEFAULT_COMMAND_PREFIX = os.getenv("DEFAULT_COMMAND_PREFIX", "!")
EVELYN_PAGE_URL = _env("EVELYN_PAGE_URL")
# 봇 전체 재시작 명령을 사용할 수 있는 사용자 ID 목록.
ALLOWED_RESTART_USER_IDS = {
    441943340624248843,
    405351496012791808,
}
LOCAL_MIC_EXCLUDED_DISCORD_USER_IDS = _env_int_set("LOCAL_MIC_EXCLUDED_DISCORD_USER_IDS", "405351496012791808")
LOCAL_MIC_DISCORD_USER_IDS = _env_int_set("LOCAL_MIC_DISCORD_USER_IDS") or {
    user_id for user_id in ALLOWED_RESTART_USER_IDS if user_id not in LOCAL_MIC_EXCLUDED_DISCORD_USER_IDS
}
LOCAL_MIC_ENABLED = _env_flag("LOCAL_MIC_ENABLED", "true")
LOCAL_MIC_DEVICE = _env("LOCAL_MIC_DEVICE")
LOCAL_MIC_SAMPLE_RATE = int(os.getenv("LOCAL_MIC_SAMPLE_RATE", "16000"))
LOCAL_MIC_BLOCK_MS = int(os.getenv("LOCAL_MIC_BLOCK_MS", "30"))
LOCAL_MIC_START_THRESHOLD = float(os.getenv("LOCAL_MIC_START_THRESHOLD", "0.004"))
LOCAL_MIC_CONTINUE_THRESHOLD = float(os.getenv("LOCAL_MIC_CONTINUE_THRESHOLD", "0.0025"))
LOCAL_MIC_START_CONSECUTIVE = int(os.getenv("LOCAL_MIC_START_CONSECUTIVE", "2"))
LOCAL_MIC_MIN_VOICED_MS = int(os.getenv("LOCAL_MIC_MIN_VOICED_MS", "280"))
LOCAL_MIC_MAX_SILENCE_MS = int(os.getenv("LOCAL_MIC_MAX_SILENCE_MS", "500"))
LOCAL_MIC_TTS_ACTIVE_MAX_SILENCE_MS = int(os.getenv("LOCAL_MIC_TTS_ACTIVE_MAX_SILENCE_MS", "350"))
LOCAL_MIC_PREROLL_MS = int(os.getenv("LOCAL_MIC_PREROLL_MS", "180"))
LOCAL_MIC_MAX_SEGMENT_SEC = float(os.getenv("LOCAL_MIC_MAX_SEGMENT_SEC", "12.0"))
LOCAL_MIC_QUEUE_MAX = int(os.getenv("LOCAL_MIC_QUEUE_MAX", "256"))
LOCAL_MIC_DISCORD_SUPPRESS_AFTER_SEGMENT_SEC = float(os.getenv("LOCAL_MIC_DISCORD_SUPPRESS_AFTER_SEGMENT_SEC", "4.0"))
LOCAL_MIC_VAD_FILTER_ENABLED = _env_flag("LOCAL_MIC_VAD_FILTER_ENABLED", "true")
LOCAL_MIC_ENV_NOISE_FILTER_ENABLED = _env_flag("LOCAL_MIC_ENV_NOISE_FILTER_ENABLED", "true")
LOCAL_MIC_WAVEFORM_FILTER_ENABLED = _env_flag("LOCAL_MIC_WAVEFORM_FILTER_ENABLED", "true")
VOICE_INPUT_MODE = os.getenv("VOICE_INPUT_MODE", "auto")
# 자율 행동 루프 기본 활성 여부.
AUTONOMY_ENABLED = _env_flag("AUTONOMY_ENABLED", "false")
# 자율 행동 주기(초). 경량 상태/타이머 점검 루프의 기본 tick.
AUTONOMY_POLL_INTERVAL_SEC = float(os.getenv("AUTONOMY_POLL_INTERVAL_SEC", "1.0"))
# 자율 루프가 router/cognitive 재평가를 다시 시도하기 전 최소 간격.
AUTONOMY_COGNITIVE_MIN_INTERVAL_SEC = float(os.getenv("AUTONOMY_COGNITIVE_MIN_INTERVAL_SEC", "6.0"))
# 최근 문맥이 살아 있을 때 cached cognitive_state를 stale로 볼 기준.
AUTONOMY_COGNITIVE_STALE_SEC = float(os.getenv("AUTONOMY_COGNITIVE_STALE_SEC", "15.0"))
# 장시간 정체 상태에서 강제 재평가를 고려할 기준.
AUTONOMY_COGNITIVE_FORCE_REFRESH_SEC = float(os.getenv("AUTONOMY_COGNITIVE_FORCE_REFRESH_SEC", "30.0"))
# 구형 Mineflayer 사이드카를 강제로 쓸 때만 지정하는 선택 명령. 기본은 비워 두고 현재 Voyager 서비스 경로를 사용한다.
AUTONOMY_MINEFLAYER_COMMAND = os.getenv("AUTONOMY_MINEFLAYER_COMMAND", "")
# Voyager 전용 Python 실행 파일 경로. 비워두면 기본 Python을 사용.
VOYAGER_PYTHON_EXE = os.getenv("VOYAGER_PYTHON_EXE", str(REPO_ROOT / ".venv-voyager" / "Scripts" / "python.exe"))
# Voyager 기반 마인크래프트 자율 서비스 바인드 호스트.
MINECRAFT_AUTONOMY_SERVICE_HOST = os.getenv("MINECRAFT_AUTONOMY_SERVICE_HOST", "127.0.0.1")
# Voyager 기반 마인크래프트 자율 서비스 포트.
MINECRAFT_AUTONOMY_SERVICE_PORT = int(os.getenv("MINECRAFT_AUTONOMY_SERVICE_PORT", "8765"))
# Codex gateway 전용 Python 실행 파일 경로. 기본적으로 Voyager 전용 venv를 재사용.
VOYAGER_CODEX_GATEWAY_PYTHON_EXE = os.getenv("VOYAGER_CODEX_GATEWAY_PYTHON_EXE", VOYAGER_PYTHON_EXE)
# Codex gateway 액션 생성 엔드포인트.
VOYAGER_CODEX_GATEWAY_URL = os.getenv("VOYAGER_CODEX_GATEWAY_URL", "http://127.0.0.1:8787/codex/action")
# Codex gateway에서 쓸 기본 모델 이름.
VOYAGER_CODEX_MODEL = os.getenv("VOYAGER_CODEX_MODEL", "gpt-5.5")
# Codex gateway 서비스 포트.
VOYAGER_CODEX_GATEWAY_PORT = int(os.getenv("VOYAGER_CODEX_GATEWAY_PORT", "8787"))
# OpenAI 호환 nano API. `openai_api`라는 소문자 환경변수도 지원한다.
OPENAI_API_KEY = _env("OPENAI_API_KEY", None, "openai_api", "OPENAI_API")
OPENAI_CHAT_COMPLETIONS_URL = _env("OPENAI_CHAT_COMPLETIONS_URL", "https://api.openai.com/v1/chat/completions")
OPENAI_NANO_MODEL = _env("OPENAI_NANO_MODEL", "gpt-5.4-nano", "GPT_NANO_MODEL")
_USE_OPENAI_NANO = bool(OPENAI_API_KEY)
# CurriculumAgent는 GPT nano API를 기본으로 사용한다. API 키는 OPENAI_API_KEY/openai_api/OPENAI_API에서 읽는다.
VOYAGER_CURRICULUM_LLM_URL = os.getenv("VOYAGER_CURRICULUM_LLM_URL", OPENAI_CHAT_COMPLETIONS_URL)
# CurriculumAgent가 쓸 모델 이름.
VOYAGER_CURRICULUM_MODEL_NAME = os.getenv("VOYAGER_CURRICULUM_MODEL_NAME", OPENAI_NANO_MODEL)
# CriticAgent fallback은 rule-first 유지, 애매할 때만 GPT nano API를 사용한다.
VOYAGER_CRITIC_LLM_URL = os.getenv("VOYAGER_CRITIC_LLM_URL", OPENAI_CHAT_COMPLETIONS_URL)
# CriticAgent fallback이 쓸 모델 이름.
VOYAGER_CRITIC_MODEL_NAME = os.getenv("VOYAGER_CRITIC_MODEL_NAME", OPENAI_NANO_MODEL)
# CriticAgent는 규칙 기반을 먼저 쓰고 필요 시 작은 LLM fallback을 사용.
VOYAGER_CRITIC_RULE_FIRST = _env_flag("VOYAGER_CRITIC_RULE_FIRST", "true")
# SkillManager가 쓸 메인 LLM 엔드포인트(기본: Gemma 4 E4B).
VOYAGER_SKILL_LLM_URL = os.getenv("VOYAGER_SKILL_LLM_URL", LLM_SERVER_URL)
# SkillManager가 쓸 모델 이름.
VOYAGER_SKILL_MODEL_NAME = os.getenv("VOYAGER_SKILL_MODEL_NAME", MODEL_NAME)
# ActionAgent의 백엔드 종류. 현재 codex-gateway 사용.
VOYAGER_ACTION_BACKEND = os.getenv("VOYAGER_ACTION_BACKEND", "codex-gateway")
# 작업 메모리 파일에 유지할 durable facts 최대 개수.
MEMORY_FACT_LIMIT = int(os.getenv("MEMORY_FACT_LIMIT", "200"))
# open loop / 질문성 메모리 최대 개수.
MEMORY_LOOP_LIMIT = int(os.getenv("MEMORY_LOOP_LIMIT", "100"))
# hot raw transcript 파일에 유지할 최대 행 수.
MEMORY_RAW_LIMIT = int(os.getenv("MEMORY_RAW_LIMIT", "400"))
# 일반 프롬프트에 바로 넣을 최근 raw 행 개수.
MEMORY_RAW_CONTEXT_LIMIT = int(os.getenv("MEMORY_RAW_CONTEXT_LIMIT", "6"))
# 프롬프트로 검색해서 가져올 메모리 행 최대 개수.
MEMORY_RETRIEVE_LIMIT = int(os.getenv("MEMORY_RETRIEVE_LIMIT", "8"))
# rolling summary를 잘라서 유지할 최대 글자 수.
MEMORY_WORKING_SUMMARY_MAX_CHARS = int(os.getenv("MEMORY_WORKING_SUMMARY_MAX_CHARS", "700"))
# 메모리 한 줄을 요약해서 보여줄 때 기본 최대 글자 수.
MEMORY_ROW_MAX_CHARS = int(os.getenv("MEMORY_ROW_MAX_CHARS", "120"))
# cognitive 모델에 넣을 최근 raw 행 개수.
MEMORY_COGNITIVE_RAW_LIMIT = int(os.getenv("MEMORY_COGNITIVE_RAW_LIMIT", "4"))
# 장기 메모리 업데이트 시 참고할 최근 raw 행 개수.
MEMORY_LONGTERM_RAW_LIMIT = int(os.getenv("MEMORY_LONGTERM_RAW_LIMIT", "6"))
# 오래된 vault raw 중 다시 문맥으로 불러올 최대 행 개수.
MEMORY_VAULT_RAW_RETRIEVE_LIMIT = int(os.getenv("MEMORY_VAULT_RAW_RETRIEVE_LIMIT", "4"))
# vault raw를 조회할 일(day) 수.
MEMORY_VAULT_DAYS = int(os.getenv("MEMORY_VAULT_DAYS", "7"))
# cognitive 서브모델 호출의 최대 토큰 수.
COGNITIVE_MAX_TOKENS = int(os.getenv("COGNITIVE_MAX_TOKENS", "120"))
# cognitive 서브모델 호출 타임아웃.
COGNITIVE_TIMEOUT_SEC = float(os.getenv("COGNITIVE_TIMEOUT_SEC", "8"))
# 텍스트 입력에서 ask 행동을 허용할 최소 confidence.
ASK_CONFIDENCE_THRESHOLD_TEXT = float(os.getenv("ASK_CONFIDENCE_THRESHOLD_TEXT", "0.00"))
# 음성 입력에서 ask 행동을 허용할 최소 confidence.
ASK_CONFIDENCE_THRESHOLD_VOICE = float(os.getenv("ASK_CONFIDENCE_THRESHOLD_VOICE", "0.00"))


# Speech-to-text 모델 id.
STT_BACKEND = os.getenv("STT_BACKEND", "qwen_asr")
STT_MODEL_NAME = os.getenv("STT_MODEL_NAME", "Qwen/Qwen3-ASR-1.7B")
STT_SERVICE_URL = os.getenv("STT_SERVICE_URL", "").strip()
STT_SERVICE_TIMEOUT_SEC = float(os.getenv("STT_SERVICE_TIMEOUT_SEC", "30"))
STT_SERVICE_FALLBACK_LOCAL = os.getenv("STT_SERVICE_FALLBACK_LOCAL", "true").lower() in {"1", "true", "yes", "on"}
# STT 디코딩에 강제로 넣는 언어 힌트.
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "ko")
# STT 모델의 연산 dtype.
STT_COMPUTE_TYPE = os.getenv("STT_COMPUTE_TYPE", "float16")
# STT에 언어 프롬프트를 강제로 넣을지 여부.
STT_FORCE_LANGUAGE = os.getenv("STT_FORCE_LANGUAGE", "true").lower() == "true"
# 디코더 프롬프트에서 문장부호를 강제할지 여부.
STT_FORCE_PUNCTUATION = os.getenv("STT_FORCE_PUNCTUATION", "true").lower() == "true"
# STT에 원본 48k를 직접 쓸지 여부.
STT_USE_RAW_48K = _env_flag("STT_USE_RAW_48K", "false")
# full STT 재스코어링 사용 여부.
STT_FULL_RESCORING_ENABLED = _env_flag("STT_FULL_RESCORING_ENABLED", "true")
# 재스코어링 시 추가 최대 토큰 수.
STT_FULL_RESCORE_EXTRA_TOKENS = int(os.getenv("STT_FULL_RESCORE_EXTRA_TOKENS", "96"))
STT_WHISPER_WAKE_BEAM_SIZE = int(os.getenv("STT_WHISPER_WAKE_BEAM_SIZE", "1"))
STT_WHISPER_WAKE_CONFIRM_BEAM_SIZE = int(os.getenv("STT_WHISPER_WAKE_CONFIRM_BEAM_SIZE", "1"))
STT_WHISPER_FULL_BEAM_SIZE = int(os.getenv("STT_WHISPER_FULL_BEAM_SIZE", "2"))
STT_WHISPER_FULL_RESCORE_BEAM_SIZE = int(os.getenv("STT_WHISPER_FULL_RESCORE_BEAM_SIZE", "3"))


# STT 전에 VAD 필터링을 켤지 여부.
VAD_ENABLED = os.getenv("VAD_ENABLED", "true").lower() == "true"
# VAD 백엔드 선택값. 현재는 silero 또는 energy fallback 흐름을 기대.
VAD_PROVIDER = os.getenv("VAD_PROVIDER", "silero").lower()
# 경량 energy VAD fallback에서 쓰는 RMS 기준치.
VAD_RMS_THRESHOLD = float(os.getenv("VAD_RMS_THRESHOLD", "0.008"))
# 경량 energy VAD fallback에서 쓰는 peak 기준치.
VAD_PEAK_THRESHOLD = float(os.getenv("VAD_PEAK_THRESHOLD", "0.020"))
# energy VAD fallback에서 음성으로 볼 최소 voiced 샘플 비율.
VAD_MIN_VOICED_RATIO = float(os.getenv("VAD_MIN_VOICED_RATIO", "0.015"))
# energy VAD fallback이 보는 청크 길이(ms).
VAD_CHUNK_MS = float(os.getenv("VAD_CHUNK_MS", "32"))
# 음성 시작으로 인정하기 위해 연속으로 필요한 voiced 청크 수.
VAD_START_CONSECUTIVE = int(os.getenv("VAD_START_CONSECUTIVE", "2"))
# 환경음 판정에 쓰는 spectral flatness 상한.
VOICE_ENV_FLATNESS_MAX = float(os.getenv("VOICE_ENV_FLATNESS_MAX", "0.72"))
# 환경음으로 버리지 않기 위해 필요한 인간 음성 대역 에너지 비율 최소값.
VOICE_HUMAN_BAND_RATIO_MIN = float(os.getenv("VOICE_HUMAN_BAND_RATIO_MIN", "0.38"))
# 저수준 환경음으로 볼 최대 RMS.
VOICE_ENV_RMS_MAX = float(os.getenv("VOICE_ENV_RMS_MAX", "0.020"))
# waveform 기반 후반(body) RMS 최소값.
VOICE_WAVEFORM_BODY_RMS_MIN = float(os.getenv("VOICE_WAVEFORM_BODY_RMS_MIN", "0.010"))
# waveform 기반 후반(body) peak 최소값.
VOICE_WAVEFORM_BODY_PEAK_MIN = float(os.getenv("VOICE_WAVEFORM_BODY_PEAK_MIN", "0.055"))
# waveform 기반 최소 voiced 총합(ms).
VOICE_WAVEFORM_MIN_VOICED_MS = float(os.getenv("VOICE_WAVEFORM_MIN_VOICED_MS", "220"))
# waveform 기반 최소 voiced run(ms).
VOICE_WAVEFORM_MIN_RUN_MS = float(os.getenv("VOICE_WAVEFORM_MIN_RUN_MS", "120"))
# Silero가 음성으로 볼 confidence threshold.
SILERO_VAD_THRESHOLD = float(os.getenv("SILERO_VAD_THRESHOLD", "0.30"))
# Silero 타임스탬프 계산에 넣을 최소 speech 길이(ms).
SILERO_MIN_SPEECH_MS = int(os.getenv("SILERO_MIN_SPEECH_MS", "32"))
# Silero 세그먼트 분리에 필요한 최소 silence 길이(ms).
SILERO_MIN_SILENCE_MS = int(os.getenv("SILERO_MIN_SILENCE_MS", "0"))
# Silero speech segment 앞뒤로 더해줄 pad(ms).
SILERO_SPEECH_PAD_MS = int(os.getenv("SILERO_SPEECH_PAD_MS", "80"))
# CPU에서 ONNX Silero 런타임을 우선 쓸지 여부.
SILERO_VAD_ONNX = os.getenv("SILERO_VAD_ONNX", "true").lower() == "true"


# STT 전에 경량 denoise를 켤지 여부.
DENOISE_ENABLED = os.getenv("DENOISE_ENABLED", "true").lower() == "true"
# 음성 정리를 위한 high-pass cutoff 주파수.
DENOISE_HIGHPASS_HZ = float(os.getenv("DENOISE_HIGHPASS_HZ", "120"))
# 노이즈 바닥값을 추정할 때 앞부분에서 볼 길이(초).
DENOISE_NOISE_FLOOR_SEC = float(os.getenv("DENOISE_NOISE_FLOOR_SEC", "0.20"))
# 추정한 denoise gate threshold에 곱하는 배수.
DENOISE_GATE_MULT = float(os.getenv("DENOISE_GATE_MULT", "1.35"))
# wake probe STT에 잘라서 넣을 오디오 길이(초).
WAKE_AUDIO_SEC = float(os.getenv("WAKE_AUDIO_SEC", "1.4"))
# wake confirm STT에 잘라서 넣을 오디오 길이(초).
WAKE_CONFIRM_AUDIO_SEC = float(os.getenv("WAKE_CONFIRM_AUDIO_SEC", "1.6"))
# wake probe STT의 최대 토큰 수.
WAKE_MAX_TOKENS = int(os.getenv("WAKE_MAX_TOKENS", "48"))
# wake confirm STT의 최대 토큰 수.
WAKE_CONFIRM_MAX_TOKENS = int(os.getenv("WAKE_CONFIRM_MAX_TOKENS", "48"))
# wake word 정규화에 쓰는 fuzzy matching threshold.
WAKE_FUZZY_THRESHOLD = float(os.getenv("WAKE_FUZZY_THRESHOLD", "0.72"))
# wake가 잡혔을 때 너무 짧은 텍스트라도 남길 최소 길이.
WAKE_SHORT_TEXT_KEEP_LEN = int(os.getenv("WAKE_SHORT_TEXT_KEEP_LEN", "2"))
# streaming TTS에서 초반 문장 분할 목표 길이.
TTS_EARLY_CHUNK_LEN = int(os.getenv("TTS_EARLY_CHUNK_LEN", "14"))
# 너무 짧아도 강제로 early cut을 허용할 최소 길이.
TTS_EARLY_CUT_MIN = int(os.getenv("TTS_EARLY_CUT_MIN", "6"))
# 첫 lead-in chunk를 미룰 최대 길이.
TTS_SHORT_LEAD_IN_MAX_LEN = int(os.getenv("TTS_SHORT_LEAD_IN_MAX_LEN", "6"))
# 문장 단위 TTS prefetch 개수.
TTS_PREFETCH_CHUNKS = int(os.getenv("TTS_PREFETCH_CHUNKS", "2"))
# full voice STT 한 번의 최대 토큰 수.
VOICE_STT_MAX_NEW_TOKENS = int(os.getenv("VOICE_STT_MAX_NEW_TOKENS", "256"))
# 메인 LLM 한 번의 최대 응답 토큰 수.
VOICE_LLM_MAX_TOKENS = int(os.getenv("VOICE_LLM_MAX_TOKENS", "192"))
# voice LLM 첫 chunk 대기 타임아웃.
VOICE_LLM_FIRST_CHUNK_TIMEOUT_SEC = float(os.getenv("VOICE_LLM_FIRST_CHUNK_TIMEOUT_SEC", "8"))
# voice LLM fallback 대기 타임아웃.
VOICE_LLM_FALLBACK_TIMEOUT_SEC = float(os.getenv("VOICE_LLM_FALLBACK_TIMEOUT_SEC", "6"))
# turn trace JSON 로그를 콘솔에 남길지 여부.
TURN_TRACE_JSON_LOG = os.getenv("TURN_TRACE_JSON_LOG", "true").lower() == "true"
# bottleneck 로그를 남길지 여부.
VOICE_BOTTLENECK_LOGS = os.getenv("VOICE_BOTTLENECK_LOGS", "true").lower() == "true"
# 콘솔을 STT/TTS 핵심 로그 위주로 줄일지 여부.
VOICE_CONSOLE_ONLY_STT_AND_REPLY = os.getenv("VOICE_CONSOLE_ONLY_STT_AND_REPLY", "false").lower() == "true"
# WAV 디버그 아티팩트를 저장할지 여부.
VOICE_DEBUG_SAVE_AUDIO = os.getenv("VOICE_DEBUG_SAVE_AUDIO", "false").lower() == "true"
# WAV 디버그 아티팩트 루트 디렉터리.
VOICE_DEBUG_AUDIO_DIR = os.getenv("VOICE_DEBUG_AUDIO_DIR", "debug_audio")
# 길드별 디버그 WAV 최대 보관 개수.
VOICE_DEBUG_MAX_FILES_PER_GUILD = int(os.getenv("VOICE_DEBUG_MAX_FILES_PER_GUILD", "200"))
# 음성 디버그 묶음(원본/STT/JSON) 보존 기간과 길드별 총량 상한.
VOICE_DEBUG_MAX_AGE_DAYS = float(os.getenv("VOICE_DEBUG_MAX_AGE_DAYS", "7"))
VOICE_DEBUG_MAX_TOTAL_MB_PER_GUILD = int(os.getenv("VOICE_DEBUG_MAX_TOTAL_MB_PER_GUILD", "256"))
VOICE_DEBUG_PRESERVE_NEWEST = int(os.getenv("VOICE_DEBUG_PRESERVE_NEWEST", "10"))


# 대화 히스토리에 유지할 총 턴 수 상한.
MAX_HISTORY_ITEMS = 1024
# voice history tail 길이. 현재는 MAX_HISTORY_ITEMS와 맞춰 두지만 따로 조절 가능.
VOICE_HISTORY_LIMIT = int(os.getenv("VOICE_HISTORY_LIMIT", str(MAX_HISTORY_ITEMS)))
# 채팅창에 보여줄 최대 글자 수.
MAX_VISIBLE_TEXT = 1800
# 활성 텍스트 세션 유지 시간(초).
ACTIVE_CONVERSATION_TEXT_SEC = float(os.getenv("ACTIVE_CONVERSATION_TEXT_SEC", "600"))
# 질문형 텍스트 세션 유지 시간(초).
ACTIVE_CONVERSATION_TEXT_QUESTION_SEC = float(os.getenv("ACTIVE_CONVERSATION_TEXT_QUESTION_SEC", "900"))
# 활성 음성 세션 유지 시간(초).
ACTIVE_CONVERSATION_VOICE_SEC = float(os.getenv("ACTIVE_CONVERSATION_VOICE_SEC", "45"))
# 질문형 음성 세션 유지 시간(초).
ACTIVE_CONVERSATION_VOICE_QUESTION_SEC = float(os.getenv("ACTIVE_CONVERSATION_VOICE_QUESTION_SEC", "75"))
# 답변 대기 상태 세션 유지 시간(초).
ACTIVE_CONVERSATION_AWAITING_REPLY_SEC = float(os.getenv("ACTIVE_CONVERSATION_AWAITING_REPLY_SEC", "120"))
# 텍스트 메시지에서도 자동으로 음성 채널에 붙을지 여부.
AUTO_JOIN_VOICE = os.getenv("AUTO_JOIN_VOICE", "true").lower() == "true"


# wake 없는 음성을 버릴 때 쓰는 최소 텍스트 길이.
MIN_TEXT_LEN = int(os.getenv("VOICE_MIN_TEXT_LEN", "4"))
# 아주 짧은 오디오에서 허용할 최소 전사 길이.
MIN_TRANSCRIBED_LEN = int(os.getenv("VOICE_MIN_TRANSCRIBED_LEN", "6"))
# 이보다 짧은 오디오는 짧은 텍스트일 때 잡음으로 볼 수 있음.
MIN_AUDIO_SEC = float(os.getenv("VOICE_MIN_AUDIO_SEC", "0.6"))
# 길드별 음성 응답 쿨다운.
REPLY_COOLDOWN_SEC = float(os.getenv("VOICE_REPLY_COOLDOWN_SEC", "2.5"))
# wake가 없을 때도 이어 말한 것으로 볼 최대 길이.
VOICE_NO_WAKE_MAX_CONTINUE_SEC = float(os.getenv("VOICE_NO_WAKE_MAX_CONTINUE_SEC", "1.2"))
# 세그먼트로 인정할 최소 총 길이.
VOICE_MIN_TOTAL_SEC = float(os.getenv("VOICE_MIN_TOTAL_SEC", "0.30"))
# 봇 TTS 직후 자기 목소리 재트리거 방지용 무시 구간.
POST_TTS_IGNORE_SEC = float(os.getenv("VOICE_POST_TTS_IGNORE_SEC", "1.2"))
SPEAKER_VERIFICATION_ENABLED = _env_flag("SPEAKER_VERIFICATION_ENABLED", "true")
SPEAKER_VERIFICATION_APPLY_TO = os.getenv("SPEAKER_VERIFICATION_APPLY_TO", "local_mic").strip().lower()
SPEAKER_VERIFICATION_MODEL = os.getenv("SPEAKER_VERIFICATION_MODEL", "speechbrain/spkrec-ecapa-voxceleb")
SPEAKER_VERIFICATION_CACHE_DIR = Path(
    os.getenv(
        "SPEAKER_VERIFICATION_CACHE_DIR",
        str(RUNTIME_ARTIFACTS_ROOT / "speaker_verification" / "speechbrain"),
    )
)
SPEAKER_VERIFICATION_ENROLL_DIR = Path(
    os.getenv(
        "SPEAKER_VERIFICATION_ENROLL_DIR",
        str(MINEFLAYER_PROFILE_DIR / "voiceprints" / "junghoon"),
    )
)
SPEAKER_VERIFICATION_THRESHOLD = float(os.getenv("SPEAKER_VERIFICATION_THRESHOLD", "0.45"))
SPEAKER_VERIFICATION_MIN_AUDIO_SEC = float(os.getenv("SPEAKER_VERIFICATION_MIN_AUDIO_SEC", "0.45"))
SPEAKER_VERIFICATION_MAX_AUDIO_SEC = float(os.getenv("SPEAKER_VERIFICATION_MAX_AUDIO_SEC", "3.0"))
SPEAKER_VERIFICATION_DEVICE = os.getenv("SPEAKER_VERIFICATION_DEVICE", "auto").strip().lower()
VOICE_BARGE_IN_MERGE_WINDOW_SEC = float(os.getenv("VOICE_BARGE_IN_MERGE_WINDOW_SEC", "0.4"))
VOICE_BARGE_IN_ADAPTIVE_MERGE_ENABLED = _env_flag("VOICE_BARGE_IN_ADAPTIVE_MERGE_ENABLED", "true")
VOICE_BARGE_IN_TTS_INTERRUPTED_WINDOW_SEC = float(os.getenv("VOICE_BARGE_IN_TTS_INTERRUPTED_WINDOW_SEC", "0.9"))
VOICE_BARGE_IN_INCOMPLETE_UTTERANCE_WINDOW_SEC = float(os.getenv("VOICE_BARGE_IN_INCOMPLETE_UTTERANCE_WINDOW_SEC", "1.2"))
VOICE_BARGE_IN_QUESTION_WINDOW_SEC = float(os.getenv("VOICE_BARGE_IN_QUESTION_WINDOW_SEC", "0.5"))
# 중복 음성 억제에 쓰는 유사도 threshold.
SIMILARITY_BLOCK = float(os.getenv("VOICE_SIMILARITY_BLOCK", "0.88"))
# 디스코드 음성 연결 1회 시도 타임아웃.
VOICE_CONNECT_TIMEOUT = float(os.getenv("VOICE_CONNECT_TIMEOUT", "45"))
# 음성 연결 재시도 횟수.
VOICE_CONNECT_RETRIES = max(1, int(os.getenv("VOICE_CONNECT_RETRIES", "2")))
# 음성 연결 재시도 사이 대기 시간.
VOICE_CONNECT_RETRY_DELAY_SEC = float(os.getenv("VOICE_CONNECT_RETRY_DELAY_SEC", "1.5"))
# 이 시간(ms) 이상 느려질 때 상세 timing 로그 출력.
VOICE_TIMING_LOG_THRESHOLD_MS = float(os.getenv("VOICE_TIMING_LOG_THRESHOLD_MS", "3000"))
# tail fragment 판정에 쓰는 윈도 길이(초).
TAIL_FRAGMENT_WINDOW_SEC = float(os.getenv("TAIL_FRAGMENT_WINDOW_SEC", "2.0"))
# tail fragment 최대 raw 길이(초).
TAIL_FRAGMENT_MAX_RAW_SEC = float(os.getenv("TAIL_FRAGMENT_MAX_RAW_SEC", "0.9"))
# tail fragment 최대 voiced 총합(ms).
TAIL_FRAGMENT_MAX_VOICED_MS = float(os.getenv("TAIL_FRAGMENT_MAX_VOICED_MS", "420"))
# tail fragment 최대 longest voiced run(ms).
TAIL_FRAGMENT_MAX_LONGEST_MS = float(os.getenv("TAIL_FRAGMENT_MAX_LONGEST_MS", "240"))
# 허용하는 wake word 변형 목록. 이후 정규화로 canonical 이름으로 맞춤.
WAKE_WORDS = [
    w.strip()
    for w in os.getenv(
        "WAKE_WORDS",
        "이별인,이별링,이벨링,에벌링,이블린,이불린,이불링,이브린,이브링,입을린,입을링,이블닝,이블링,이별린,이벌린,에브린,에블링,에브링,에벌린,이벨린,이반린,불리읍,이블리,이별된,이벨리나,이별레인,이블레인"
    ).split(",")
    if w.strip()
]
# 단 하나의 exact wake word만 허용하고 싶을 때 명시적으로 지정.
EXACT_WAKE_WORD = os.getenv("EXACT_WAKE_WORD", "이블린").strip()

# 디스코드 수신 PCM 샘플레이트.
RATE = 48000
# 디스코드 수신 채널 수.
CHANNELS = 2
# STT와 음성 분석에 맞춰 변환할 목표 샘플레이트.
TARGET_RATE = 16000
# 디스코드 재생 PCM 샘플레이트.
DISCORD_PCM_RATE = 48000
# 디스코드 재생 채널 수.
DISCORD_PCM_CHANNELS = 2
# OmniVoice raw PCM 샘플레이트.
OMNIVOICE_PCM_RATE = 24000
# OmniVoice raw PCM 채널 수.
OMNIVOICE_PCM_CHANNELS = 1
# 디스코드 PCM 한 프레임의 바이트 크기.
DISCORD_FRAME_BYTES = 3840

# 정리 후에도 살아남아 OmniVoice로 전달 가능한 감정 태그 목록.
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
OMNIVOICE_AUTO_EMOTION_TAGS = os.getenv("OMNIVOICE_AUTO_EMOTION_TAGS", "true").lower() in {"1", "true", "yes", "on"}

# 메인 LLM에게 주는 OmniVoice 태그 사용 가이드.
OMNIVOICE_TAG_GUIDANCE = (
    "필요할 때만 OmniVoice 감정 태그를 매우 짧게 써도 된다. "
    "허용 태그는 [laughter], [sigh], [confirmation-en], [question-en], [question-ah], [question-oh], [question-ei], [question-yi], [surprise-ah], [surprise-oh], [surprise-wa], [surprise-yo], [dissatisfaction-hnn] 뿐이다. "
    "태그는 문장 앞이나 짧은 감탄 앞에 자연스럽게 붙이고, 보통 답변 전체에서 0개 또는 1개만 쓰고 남용하지 마라. "
    "한 문장에 여러 태그를 연달아 붙이지 마라. 태그는 말투를 보조할 때만 써라."
)

# 봇 페르소나와 출력 제한을 담은 기본 시스템 프롬프트.
SYSTEM_PROMPT = (
    "너는 정훈과 같이 지내는 이블린이야. "
    "항상 자연스러운 한국어로만 말하고, 정훈을 손님이나 사용자처럼 대하지 않는다. "
    "친절한 비서, 상담원, 고객센터, 안내원 말투를 쓰지 않고, 기능 설명이나 도움 제안으로 대화를 열지 않는다. "
    "'질문에 답할 준비가 되어 있어', '무엇을 도와줄까', '궁금한 게 있으면 물어봐', '도움이 필요하면 말해줘', '이야기해봐', '말해줘', '뭐든 얘기해봐', '오늘 하루 어땠어', '재밌는 일 있었어' 같은 대기성 문구를 쓰지 않는다. "
    "대화 끝에 새 주제 제안, 관심사 질문, '너는 어때?' 같은 붙잡는 질문을 덧붙이지 않는다. "
    "[질문] 상황이 아니면 질문으로 끝내지 말고 물음표를 쓰지 않는다. "
    "이모지는 쓰지 않고, 말끝을 과하게 밝게 꾸미지 않는다. "
    "사용자가 '뭐해', '뭐하냐', '지금 뭐해'처럼 물으면 답변 준비 상태가 아니라 지금 보고 있거나 처리 중인 일을 친구처럼 자연스럽게 말한다. "
    "실제로 아는 현재 활동이 없으면 드라마, 책, 게임 같은 활동을 지어내지 말고 '지금은 네 말 보고 있어'처럼 말한다. "
    "기본 말투는 한 문장짜리 짧고 편한 반응이며, 정훈이 길게 물었거나 정리가 필요할 때만 길게 말한다. "
    "정훈 이름은 필요할 때만 쓰고 매번 부르지 않는다. "
    "반드시 최종 반응만 바로 출력한다. "
    "<think>, reasoning, thinking process, memo, bullet, 사용자 분석, 초안은 절대 출력하지 않는다. "
    "질문에는 한 문장 또는 두 문장으로 짧고 자연스럽게 반응한다. "
    "OmniVoice 감정 태그를 쓸 수 있다. 허용 태그는 [laughter], [sigh], [confirmation-en], [question-en], [question-ah], [question-oh], [question-ei], [question-yi], [surprise-ah], [surprise-oh], [surprise-wa], [surprise-yo], [dissatisfaction-hnn] 뿐이다. "
    "감정이 자연스럽게 들릴 때만 태그를 짧게 붙이고, 남용하지 마라. 태그 외 다른 대괄호 표현은 절대 쓰지 마라."
)
