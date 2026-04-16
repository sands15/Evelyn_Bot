import audioop
import os
import queue
import re
import time
import asyncio
from difflib import SequenceMatcher
from typing import Optional

import aiohttp
import numpy as np
import torch
import discord
from discord.ext import commands
from faster_whisper import WhisperModel

from evelyn_voice import EvelynVoiceClient


# =========================================================
# 기본 설정
# =========================================================
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

LLM_SERVER_URL = os.getenv("LLM_SERVER_URL", "http://127.0.0.1:9820/v1/chat/completions")
MODEL_NAME = os.getenv("LLM_MODEL_NAME", "Qwen3.5-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf")

OMNIVOICE_SERVER_URL = os.getenv("OMNIVOICE_SERVER_URL", "http://127.0.0.1:8880")
OMNIVOICE_MODEL = os.getenv("OMNIVOICE_MODEL", "omnivoice")
OMNIVOICE_VOICE = os.getenv("OMNIVOICE_VOICE", "clone:evelyn")
OMNIVOICE_LANGUAGE = os.getenv("OMNIVOICE_LANGUAGE", "ko")
OMNIVOICE_STREAM = os.getenv("OMNIVOICE_STREAM", "true").lower() == "true"
OMNIVOICE_TIMEOUT_SEC = float(os.getenv("OMNIVOICE_TIMEOUT_SEC", "180"))

STT_MODEL_NAME = os.getenv("STT_MODEL_NAME", "large-v3-turbo")
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "ko")
STT_COMPUTE_TYPE = os.getenv("STT_COMPUTE_TYPE", "float16")

MAX_HISTORY_ITEMS = 1024
MAX_VISIBLE_TEXT = 1800
AUTO_JOIN_VOICE = os.getenv("AUTO_JOIN_VOICE", "true").lower() == "true"

WAKE_WORD = os.getenv("WAKE_WORD", "이블린")
MIN_TEXT_LEN = int(os.getenv("VOICE_MIN_TEXT_LEN", "4"))
REPLY_COOLDOWN_SEC = float(os.getenv("VOICE_REPLY_COOLDOWN_SEC", "2.5"))
POST_TTS_IGNORE_SEC = float(os.getenv("VOICE_POST_TTS_IGNORE_SEC", "1.2"))
SIMILARITY_BLOCK = float(os.getenv("VOICE_SIMILARITY_BLOCK", "0.88"))

RATE = 48000
CHANNELS = 2
TARGET_RATE = 16000
DISCORD_PCM_RATE = 48000
DISCORD_PCM_CHANNELS = 2
OMNIVOICE_PCM_RATE = 24000
OMNIVOICE_PCM_CHANNELS = 1
DISCORD_FRAME_BYTES = 3840

BAD_SHORTS = {
    "안녕",
    "안녕하세요",
    "감사합니다",
    "고맙습니다",
    "네",
    "네 감사합니다",
    "시청해주셔서 감사합니다",
    "감사합니다 여러분",
}


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

stt_model: Optional[WhisperModel] = None
http_session: Optional[aiohttp.ClientSession] = None

last_voice_reply_at: dict[int, float] = {}
last_voice_text: dict[int, str] = {}
last_bot_audio_end_at: dict[int, float] = {}
bot_speaking_guilds: set[int] = set()


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


def clean_tts_text(text: str) -> str:
    text = clean_text(text)
    text = re.sub(r"[\"'`~*_#@^|<>\[\]{}()]", "", text)
    return text


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


def is_similar(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return SequenceMatcher(None, a, b).ratio() >= SIMILARITY_BLOCK


def strip_voice_wake_word(text: str) -> str:
    text = clean_text(text)
    text = re.sub(rf"^\s*{re.escape(WAKE_WORD)}[야아]?[\s,]*", "", text)
    text = re.sub(rf"\b{re.escape(WAKE_WORD)}[야아]?\b", "", text, count=1)
    text = clean_text(text)
    return text or "부르셨나요?"


def should_reply_to_voice(guild_id: int, text: str) -> tuple[bool, str]:
    now = time.monotonic()
    text_n = normalize_voice_text(text)

    if guild_id in bot_speaking_guilds:
        return False, "bot_is_speaking"

    if now - last_bot_audio_end_at.get(guild_id, 0.0) < POST_TTS_IGNORE_SEC:
        return False, "post_tts_ignore"

    if not text_n:
        return False, "empty"

    if WAKE_WORD not in text_n:
        return False, "no_wake_word"

    if text_n in BAD_SHORTS:
        return False, "bad_short"

    if len(text_n) < MIN_TEXT_LEN:
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


async def create_omnivoice_source(text: str) -> OmniVoicePCMStream:
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

            async with session.post(
                f"{OMNIVOICE_SERVER_URL}/v1/audio/speech",
                json=payload,
                timeout=timeout,
            ) as resp:
                if resp.status != 200:
                    return False, await resp.text()

                async for chunk in resp.content.iter_chunked(8192):
                    if chunk:
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
def get_stt_model() -> WhisperModel:
    global stt_model

    if stt_model is not None:
        return stt_model

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Whisper 로드 시작: model={STT_MODEL_NAME}, device={device}, compute_type={STT_COMPUTE_TYPE}")
    stt_model = WhisperModel(
        STT_MODEL_NAME,
        device=device,
        compute_type=STT_COMPUTE_TYPE if device == "cuda" else "int8",
    )
    print("Whisper 로드 완료")
    return stt_model


def transcribe_voice_sync(pcm_bytes: bytes) -> str:
    audio16k = downmix_and_resample_int16_stereo_to_mono16k(pcm_bytes)
    if audio16k.size == 0:
        return ""

    model = get_stt_model()
    segments, _ = model.transcribe(
        audio16k,
        beam_size=3,
        best_of=1,
        temperature=0.0,
        language=STT_LANGUAGE,
        vad_filter=False,
        condition_on_previous_text=False,
        no_speech_threshold=0.55,
        log_prob_threshold=-0.9,
        compression_ratio_threshold=2.3,
    )
    return clean_text("".join(seg.text for seg in segments))


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


async def play_audio_source(vc: discord.VoiceClient, source: discord.AudioSource) -> None:
    await wait_until_not_playing(vc)

    done = asyncio.Event()
    playback_error: list[Exception | None] = [None]

    def after_play(err):
        if err:
            playback_error[0] = err
        bot.loop.call_soon_threadsafe(done.set)

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


# =========================================================
# LLM
# =========================================================
def fallback_answer_for(user_text: str) -> str:
    user_text = clean_text(user_text)
    if not user_text:
        return "응, 듣고 있어."
    return "응, 잠깐만."


async def ask_llm_once(user_text: str) -> str:
    final_user_text = (
        f"{user_text}\n\n"
        "주의: 생각 과정 말하지 말고, 최종 답변만 한국어로 한두 문장으로 짧게 말해."
    )

    payload = {
        "model": MODEL_NAME,
        "messages": conversation_history + [{"role": "user", "content": final_user_text}],
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

    try:
        text = await asyncio.to_thread(transcribe_voice_sync, pcm_bytes)
    except Exception as e:
        print(f"❌ [STT] {e}")
        return

    if not text:
        return

    print(f"🎤 [{member.display_name}] {text}")

    ok, reason = should_reply_to_voice(guild_id, text)
    if not ok:
        print(f"[STT IGNORE] {reason}: {text!r}")
        return

    user_text = strip_voice_wake_word(text)
    lock = guild_locks.setdefault(guild_id, asyncio.Lock())

    if lock.locked():
        print(f"[VOICE SKIP] busy guild={guild_id} text={user_text!r}")
        return

    async with lock:
        vc = guild.voice_client
        if vc is None:
            return

        try:
            answer = await ask_llm_once(user_text)
        except Exception as e:
            print(f"❌ [LLM] {e}")
            return

        answer = clean_text(answer)
        if not answer:
            return

        append_history(user_text, answer)
        print(f"💬 [Evelyn] {answer}")

        try:
            await speak_answer(vc, answer)
        except Exception as e:
            print(f"❌ [TTS/PLAY] {e}")


# =========================================================
# 이벤트
# =========================================================
@bot.event
async def on_ready():
    print(f"로그인 완료: {bot.user}")
    try:
        await asyncio.to_thread(get_stt_model)
    except Exception as e:
        print("Whisper 사전 로드 실패:", repr(e))

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

    is_wake_word = WAKE_WORD in message.content
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

    user_text = message.content[len(WAKE_WORD):].strip() if is_wake_word else message.content.strip()
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

                answer = await ask_llm_once(user_text)

                await message.channel.send(visible_text(answer))

            append_history(user_text, answer)

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
