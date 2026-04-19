import os
from pathlib import Path


def _env_flag(name: str, default: str = "false") -> bool:
    value = os.getenv(name)
    if value is None:
        value = default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}

# Evelyn 봇이 디스코드에 로그인할 때 쓰는 토큰.
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")


# 사용자 응답용 메인 LLM 서버 엔드포인트.
LLM_SERVER_URL = os.getenv("LLM_SERVER_URL", "http://127.0.0.1:9820/v1/chat/completions")
# 메인 LLM 서버에 전달할 모델 이름.
MODEL_NAME = os.getenv("LLM_MODEL_NAME", "gemma-4-E4B-it-Q5_K_M.gguf")


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


# 메모리 업데이트에 쓰는 서브 LLM 서버 엔드포인트.
SUMMARY_LLM_URL = os.getenv("SUMMARY_LLM_URL", "http://127.0.0.1:9821/v1/chat/completions")
# 요약/메모리 관리용 서브 모델 이름.
SUMMARY_MODEL_NAME = os.getenv("SUMMARY_MODEL_NAME", "EXAONE-3.5-7.8B-Instruct-BF16.gguf")
# 라우팅/인지 판단에 쓰는 router LLM 서버 엔드포인트.
ROUTER_LLM_URL = os.getenv("ROUTER_LLM_URL", "http://127.0.0.1:9822/v1/chat/completions")
# router 서버에 전달할 모델 이름.
ROUTER_MODEL_NAME = os.getenv("ROUTER_MODEL_NAME", "gemma-4-E2B-it-UD-Q6_K_XL.gguf")
# router LLM 사용 여부. false면 기존 heuristic/fallback만 사용한다.
ROUTER_LLM_ENABLED = _env_flag("ROUTER_LLM_ENABLED", "true")
# route 분류 한 번의 최대 토큰 수.
ROUTER_ROUTE_MAX_TOKENS = int(os.getenv("ROUTER_ROUTE_MAX_TOKENS", "80"))
# route 분류 타임아웃(초).
ROUTER_ROUTE_TIMEOUT_SEC = float(os.getenv("ROUTER_ROUTE_TIMEOUT_SEC", "8"))
# 길드별 메모리 파일을 저장하는 루트 디렉터리.
MEMORY_ROOT = Path(os.getenv("BOT_MEMORY_DIR", str(Path(__file__).resolve().parent.parent / "bot_memory")))
# 길드별 설정(prefix 등)을 저장하는 루트 디렉터리.
GUILD_SETTINGS_ROOT = Path(os.getenv("GUILD_SETTINGS_DIR", str(Path(__file__).resolve().parent.parent / "guild_settings")))
# 기본 명령어 시작 부호(prefix).
DEFAULT_COMMAND_PREFIX = os.getenv("DEFAULT_COMMAND_PREFIX", "!")
# 봇 전체 재시작 명령을 사용할 수 있는 사용자 ID 목록.
ALLOWED_RESTART_USER_IDS = {
    441943340624248843,
    405351496012791808,
}
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


# Speech-to-text 모델 id. 기본값은 한국어 파인튜닝 Whisper 모델.
STT_MODEL_NAME = os.getenv("STT_MODEL_NAME", "seastar105/whisper-medium-komixv2")
# STT 디코딩에 강제로 넣는 언어 힌트.
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "ko")
# STT 모델의 연산 dtype.
STT_COMPUTE_TYPE = os.getenv("STT_COMPUTE_TYPE", "float16")
# STT에 언어 프롬프트를 강제로 넣을지 여부.
STT_FORCE_LANGUAGE = _env_flag("STT_FORCE_LANGUAGE", "true")
# 디코더 프롬프트에서 문장부호를 강제할지 여부.
STT_FORCE_PUNCTUATION = _env_flag("STT_FORCE_PUNCTUATION", "true")
# STT/VAD/wake 경로를 원본 48k 대신 16k 준비 오디오로 통일할지 여부.
STT_USE_RAW_48K = _env_flag("STT_USE_RAW_48K", "false")
# 전체 길이가 이 값 이하인 음성은 STT 전에 바로 무시한다.
VOICE_MIN_TOTAL_SEC = float(os.getenv("VOICE_MIN_TOTAL_SEC", "0.30"))
VOICE_WAVEFORM_MIN_VOICED_MS = float(os.getenv("VOICE_WAVEFORM_MIN_VOICED_MS", "220"))
VOICE_WAVEFORM_MIN_RUN_MS = float(os.getenv("VOICE_WAVEFORM_MIN_RUN_MS", "120"))
VOICE_WAVEFORM_BODY_RMS_MIN = float(os.getenv("VOICE_WAVEFORM_BODY_RMS_MIN", "0.010"))
VOICE_WAVEFORM_BODY_PEAK_MIN = float(os.getenv("VOICE_WAVEFORM_BODY_PEAK_MIN", "0.055"))


# STT 전에 VAD 필터링을 켤지 여부.
VAD_ENABLED = _env_flag("VAD_ENABLED", "true")
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
# Silero가 음성으로 볼 confidence threshold.
SILERO_VAD_THRESHOLD = float(os.getenv("SILERO_VAD_THRESHOLD", "0.50"))
# Silero 타임스탬프 계산에 넣을 최소 speech 길이(ms).
SILERO_MIN_SPEECH_MS = int(os.getenv("SILERO_MIN_SPEECH_MS", "32"))
# Silero 세그먼트 분리에 필요한 최소 silence 길이(ms).
SILERO_MIN_SILENCE_MS = int(os.getenv("SILERO_MIN_SILENCE_MS", "0"))
# Silero speech segment 앞뒤로 더해줄 pad(ms).
SILERO_SPEECH_PAD_MS = int(os.getenv("SILERO_SPEECH_PAD_MS", "80"))
# CPU에서 ONNX Silero 런타임을 우선 쓸지 여부.
SILERO_VAD_ONNX = _env_flag("SILERO_VAD_ONNX", "true")


# STT 전에 경량 denoise를 켤지 여부.
DENOISE_ENABLED = _env_flag("DENOISE_ENABLED", "true")
# 음성 정리를 위한 high-pass cutoff 주파수.
DENOISE_HIGHPASS_HZ = float(os.getenv("DENOISE_HIGHPASS_HZ", "120"))
# 노이즈 바닥값을 추정할 때 앞부분에서 볼 길이(초).
DENOISE_NOISE_FLOOR_SEC = float(os.getenv("DENOISE_NOISE_FLOOR_SEC", "0.20"))
# 추정한 denoise gate threshold에 곱하는 배수.
DENOISE_GATE_MULT = float(os.getenv("DENOISE_GATE_MULT", "1.35"))
# wake probe STT에 잘라서 넣을 오디오 길이(초).
WAKE_AUDIO_SEC = float(os.getenv("WAKE_AUDIO_SEC", "1.1"))
# wake 2차 확인 단계에서 쓸 오디오 길이(초).
WAKE_CONFIRM_AUDIO_SEC = float(os.getenv("WAKE_CONFIRM_AUDIO_SEC", "1.6"))
# wake probe STT의 최대 토큰 수.
WAKE_MAX_TOKENS = int(os.getenv("WAKE_MAX_TOKENS", "32"))
# wake 2차 확인 STT 최대 토큰 수.
WAKE_CONFIRM_MAX_TOKENS = int(os.getenv("WAKE_CONFIRM_MAX_TOKENS", "48"))
# Whisper wake probe beam size.
STT_WHISPER_WAKE_BEAM_SIZE = int(os.getenv("STT_WHISPER_WAKE_BEAM_SIZE", "1"))
# Whisper wake probe best_of 값.
STT_WHISPER_WAKE_BEST_OF = int(os.getenv("STT_WHISPER_WAKE_BEST_OF", "1"))
# Whisper wake confirm beam size.
STT_WHISPER_WAKE_CONFIRM_BEAM_SIZE = int(os.getenv("STT_WHISPER_WAKE_CONFIRM_BEAM_SIZE", "2"))
# Whisper wake confirm best_of 값.
STT_WHISPER_WAKE_CONFIRM_BEST_OF = int(os.getenv("STT_WHISPER_WAKE_CONFIRM_BEST_OF", "1"))
# Whisper full STT beam size.
STT_WHISPER_FULL_BEAM_SIZE = int(os.getenv("STT_WHISPER_FULL_BEAM_SIZE", "2"))
# Whisper full STT best_of 값.
STT_WHISPER_FULL_BEST_OF = int(os.getenv("STT_WHISPER_FULL_BEST_OF", "1"))
# full STT 뒤에 더 무거운 2차 rescoring pass를 돌릴지 여부.
STT_FULL_RESCORING_ENABLED = _env_flag("STT_FULL_RESCORING_ENABLED", "true")
# 2차 full STT rescoring pass에서 쓸 beam size.
STT_WHISPER_FULL_RESCORE_BEAM_SIZE = int(os.getenv("STT_WHISPER_FULL_RESCORE_BEAM_SIZE", "5"))
# 2차 full STT rescoring pass에서 추가로 허용할 토큰 수.
STT_FULL_RESCORE_EXTRA_TOKENS = int(os.getenv("STT_FULL_RESCORE_EXTRA_TOKENS", "96"))
# wake word 정규화에 쓰는 fuzzy matching threshold.
WAKE_FUZZY_THRESHOLD = float(os.getenv("WAKE_FUZZY_THRESHOLD", "0.72"))
# wake가 잡혔을 때 너무 짧은 텍스트라도 남길 최소 길이.
WAKE_SHORT_TEXT_KEEP_LEN = int(os.getenv("WAKE_SHORT_TEXT_KEEP_LEN", "2"))
# streaming TTS에서 초반 문장 분할 목표 길이.
TTS_EARLY_CHUNK_LEN = int(os.getenv("TTS_EARLY_CHUNK_LEN", "14"))
# 너무 짧아도 강제로 early cut을 허용할 최소 길이.
TTS_EARLY_CUT_MIN = int(os.getenv("TTS_EARLY_CUT_MIN", "6"))
# full voice STT 한 번의 최대 토큰 수.
VOICE_STT_MAX_NEW_TOKENS = int(os.getenv("VOICE_STT_MAX_NEW_TOKENS", "160"))
# 메인 LLM 한 번의 최대 응답 토큰 수.
VOICE_LLM_MAX_TOKENS = int(os.getenv("VOICE_LLM_MAX_TOKENS", "320"))


# 대화 히스토리에 유지할 총 턴 수 상한.
MAX_HISTORY_ITEMS = 1024
# voice history tail 길이. 현재는 MAX_HISTORY_ITEMS와 맞춰 두지만 따로 조절 가능.
VOICE_HISTORY_LIMIT = int(os.getenv("VOICE_HISTORY_LIMIT", str(MAX_HISTORY_ITEMS)))
# 채팅창에 보여줄 최대 글자 수.
MAX_VISIBLE_TEXT = 1800
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
# 봇 TTS 직후 자기 목소리 재트리거 방지용 무시 구간.
POST_TTS_IGNORE_SEC = float(os.getenv("VOICE_POST_TTS_IGNORE_SEC", "1.2"))
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
# 턴 단위 구조화 로그(JSON)를 출력할지 여부.
TURN_TRACE_JSON_LOG = _env_flag("TURN_TRACE_JSON_LOG", "true")
# 텍스트 세션이 활성 상태로 유지되는 기본 시간(초).
ACTIVE_CONVERSATION_TEXT_SEC = float(os.getenv("ACTIVE_CONVERSATION_TEXT_SEC", "90"))
# 텍스트에서 봇이 질문을 던졌을 때 follow-up 창을 더 길게 유지하는 시간(초).
ACTIVE_CONVERSATION_TEXT_QUESTION_SEC = float(os.getenv("ACTIVE_CONVERSATION_TEXT_QUESTION_SEC", "150"))
# 음성 세션이 활성 상태로 유지되는 기본 시간(초).
ACTIVE_CONVERSATION_VOICE_SEC = float(os.getenv("ACTIVE_CONVERSATION_VOICE_SEC", "45"))
# 음성에서 봇이 질문을 던졌을 때 follow-up 창을 더 길게 유지하는 시간(초).
ACTIVE_CONVERSATION_VOICE_QUESTION_SEC = float(os.getenv("ACTIVE_CONVERSATION_VOICE_QUESTION_SEC", "75"))
# await 상태일 때 후속 입력을 기다리는 추가 여유 시간(초).
ACTIVE_CONVERSATION_AWAITING_REPLY_SEC = float(os.getenv("ACTIVE_CONVERSATION_AWAITING_REPLY_SEC", "180"))
# wake 미검출 상태에서 짧은 환경음 후보를 full STT로 계속 넘길 최대 길이(초).
VOICE_NO_WAKE_MAX_CONTINUE_SEC = float(os.getenv("VOICE_NO_WAKE_MAX_CONTINUE_SEC", "2.4"))
# 직전 accepted turn 직후 tail fragment를 바로 버릴 최대 시간창(초).
TAIL_FRAGMENT_WINDOW_SEC = float(os.getenv("TAIL_FRAGMENT_WINDOW_SEC", "1.2"))
# tail fragment로 볼 최대 raw 길이(초).
TAIL_FRAGMENT_MAX_RAW_SEC = float(os.getenv("TAIL_FRAGMENT_MAX_RAW_SEC", "0.9"))
# tail fragment로 볼 최대 voiced 길이(ms).
TAIL_FRAGMENT_MAX_VOICED_MS = float(os.getenv("TAIL_FRAGMENT_MAX_VOICED_MS", "260"))
# tail fragment로 볼 최대 longest voiced run(ms).
TAIL_FRAGMENT_MAX_LONGEST_MS = float(os.getenv("TAIL_FRAGMENT_MAX_LONGEST_MS", "170"))
# 수신 원본/전처리 오디오를 디버그용으로 저장할지 여부.
VOICE_DEBUG_SAVE_AUDIO = os.getenv("VOICE_DEBUG_SAVE_AUDIO", "true").lower() == "true"
# 디버그 WAV 저장 루트 디렉터리.
VOICE_DEBUG_AUDIO_DIR = os.getenv("VOICE_DEBUG_AUDIO_DIR", "debug_audio")
# 저장 개수를 제한하기 위한 길드별 최대 utterance 수. 0 이하면 무제한.
VOICE_DEBUG_MAX_FILES_PER_GUILD = int(os.getenv("VOICE_DEBUG_MAX_FILES_PER_GUILD", "200"))
# 허용하는 wake word 변형 목록. 이후 정규화로 canonical 이름으로 맞춤.
WAKE_WORDS = [
    w.strip()
    for w in os.getenv(
        "WAKE_WORDS",
        "이블린,이브린,에블린,이블린아"
    ).split(",")
    if w.strip()
]

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

# 메인 LLM에게 주는 OmniVoice 태그 사용 가이드.
OMNIVOICE_TAG_GUIDANCE = (
    "필요할 때만 OmniVoice 감정 태그를 매우 짧게 써도 된다. "
    "허용 태그는 [laughter], [sigh], [confirmation-en], [question-en], [question-ah], [question-oh], [question-ei], [question-yi], [surprise-ah], [surprise-oh], [surprise-wa], [surprise-yo], [dissatisfaction-hnn] 뿐이다. "
    "태그는 문장 앞이나 짧은 감탄 앞에 자연스럽게 붙이고, 보통 답변 전체에서 0개 또는 1개만 쓰고 남용하지 마라. "
    "한 문장에 여러 태그를 연달아 붙이지 마라. 태그는 말투를 보조할 때만 써라."
)

# 봇 페르소나와 출력 제한을 담은 기본 시스템 프롬프트.
SYSTEM_PROMPT = (
    "너는 사용자의 친구 이블린이야, 나이는 20살이고 친절하고 상냥해 "
    "항상 자연스러운 한국어로만 답한다. "
    "반드시 최종 답변만 바로 출력한다. "
    "<think>, reasoning, thinking process, memo, bullet, 사용자 분석, 초안은 절대 출력하지 않는다. "
    "질문에는 한 문장 또는 두 문장으로 짧고 자연스럽게 답한다. "
    "OmniVoice 감정 태그를 쓸 수 있다. 허용 태그는 [laughter], [sigh], [confirmation-en], [question-en], [question-ah], [question-oh], [question-ei], [question-yi], [surprise-ah], [surprise-oh], [surprise-wa], [surprise-yo], [dissatisfaction-hnn] 뿐이다. "
    "감정이 자연스럽게 들릴 때만 태그를 짧게 붙이고, 남용하지 마라. 태그 외 다른 대괄호 표현은 절대 쓰지 마라."
)
