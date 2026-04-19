from __future__ import annotations

import asyncio
import logging
import os
import struct
from collections import deque
from copy import deepcopy
from dataclasses import dataclass

import davey
import discord
import numpy as np
from discord.opus import Decoder
from nacl.bindings import crypto_aead_xchacha20poly1305_ietf_decrypt

from .dave_session import DaveSession
from .gateway import VoiceGateway
from .sink import AudioSink, NullSink
from .state import VoiceRuntimeState
from .udp import VoiceUDPTransport


async def on_user_audio(member, pcm_bytes: bytes, debug_meta: dict | None = None):
    return None


log = logging.getLogger(__name__)

OPUS_ERROR_TO_SILENCE = os.getenv("OPUS_ERROR_TO_SILENCE", "false").lower() == "true"
VOICE_TIMING_LOG_THRESHOLD_MS = float(os.getenv("VOICE_TIMING_LOG_THRESHOLD_MS", "3000"))
VOICE_MAP_RETRY_MS = float(os.getenv("VOICE_MAP_RETRY_MS", "700"))
VOICE_MAP_RETRY_MAX = max(0, int(os.getenv("VOICE_MAP_RETRY_MAX", "2")))
VOICE_INITIAL_MAP_HOLD_MS = float(os.getenv("VOICE_INITIAL_MAP_HOLD_MS", "900"))
VOICE_DAVE_WARMUP_GRACE_PACKETS = max(0, int(os.getenv("VOICE_DAVE_WARMUP_GRACE_PACKETS", "6")))
VOICE_PENDING_SSRC_MAX_PACKETS = max(1, int(os.getenv("VOICE_PENDING_SSRC_MAX_PACKETS", "96")))
VOICE_LEADING_DROP_MAX_PACKETS = max(0, int(os.getenv("VOICE_LEADING_DROP_MAX_PACKETS", "4")))
VOICE_START_STABLE_PACKETS = max(1, int(os.getenv("VOICE_START_STABLE_PACKETS", "3")))
VOICE_GAP_CONCEAL_MAX = max(0, int(os.getenv("VOICE_GAP_CONCEAL_MAX", "6")))
VOICE_LEADING_GOOD_DROP_PACKETS = max(0, int(os.getenv("VOICE_LEADING_GOOD_DROP_PACKETS", "3")))
VOICE_HARD_TRIM_MS = max(0.0, float(os.getenv("VOICE_HARD_TRIM_MS", "80")))
VOICE_DYNAMIC_TRIM_ENABLE = os.getenv("VOICE_DYNAMIC_TRIM_ENABLE", "true").lower() == "true"
VOICE_DYNAMIC_TRIM_MIN_MS = max(0.0, float(os.getenv("VOICE_DYNAMIC_TRIM_MIN_MS", "120")))
VOICE_DYNAMIC_TRIM_MAX_MS = max(VOICE_DYNAMIC_TRIM_MIN_MS, float(os.getenv("VOICE_DYNAMIC_TRIM_MAX_MS", "480")))
VOICE_DYNAMIC_TRIM_CONSECUTIVE = max(1, int(os.getenv("VOICE_DYNAMIC_TRIM_CONSECUTIVE", "4")))
VOICE_PCM_BYTES_PER_MS = int((48000 * 2 * 2) / 1000)
VOICE_PENDING_INNER_MAX_ATTEMPTS = max(1, int(os.getenv("VOICE_PENDING_INNER_MAX_ATTEMPTS", "8")))
VOICE_PENDING_INNER_MAX_AGE_SEC = max(0.2, float(os.getenv("VOICE_PENDING_INNER_MAX_AGE_SEC", "1.8")))
VOICE_PENDING_INNER_LOG_INTERVAL_SEC = max(0.5, float(os.getenv("VOICE_PENDING_INNER_LOG_INTERVAL_SEC", "8.0")))
VOICE_UNKNOWN_SSRC_MAX_AGE_SEC = max(0.4, float(os.getenv("VOICE_UNKNOWN_SSRC_MAX_AGE_SEC", "2.8")))
VOICE_UNKNOWN_SSRC_RETRY_MS = max(80.0, float(os.getenv("VOICE_UNKNOWN_SSRC_RETRY_MS", "350")))
VOICE_UNKNOWN_SSRC_LOG_INTERVAL_SEC = max(0.5, float(os.getenv("VOICE_UNKNOWN_SSRC_LOG_INTERVAL_SEC", "8.0")))

_DAVE_MARKER = b"\xfa\xfa"


@dataclass(slots=True)
class DaveSupplemental:
    supplemental_size: int
    supplemental_start: int
    nonce: int
    ranges: tuple[tuple[int, int], ...]
    ciphertext_len: int

    @property
    def ranges_count(self) -> int:
        return len(self.ranges)


def _pcm16le_stereo_to_mono_float(pcm_bytes: bytes) -> np.ndarray:
    audio = np.frombuffer(pcm_bytes, dtype=np.int16)
    if audio.size == 0:
        return np.zeros(0, dtype=np.float32)
    audio = audio.reshape(-1, 2).mean(axis=1)
    return (audio.astype(np.float32) / 32768.0).astype(np.float32)


def _resample_mono_float(audio: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    audio = np.asarray(audio, dtype=np.float32)
    src = max(1, int(from_rate))
    dst = max(1, int(to_rate))
    if audio.size == 0 or src == dst:
        return audio.astype(np.float32, copy=True)
    new_len = max(1, int(round(len(audio) * (dst / float(src)))))
    x_old = np.linspace(0.0, 1.0, len(audio), endpoint=False)
    x_new = np.linspace(0.0, 1.0, new_len, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.float32)


def _estimate_leading_trim_ms(pcm_bytes: bytes, *, sampling_rate: int = 48000) -> tuple[float, dict]:
    if not VOICE_DYNAMIC_TRIM_ENABLE or not pcm_bytes:
        return VOICE_HARD_TRIM_MS, {"stable_ms": None, "body_rms": 0.0, "mode": "disabled"}

    audio = _pcm16le_stereo_to_mono_float(pcm_bytes)
    if audio.size < int(max(1, sampling_rate) * 0.25):
        return VOICE_HARD_TRIM_MS, {"stable_ms": None, "body_rms": 0.0, "mode": "short"}

    sr = 16000
    audio16 = _resample_mono_float(audio, sampling_rate, sr)
    inspect_samples = min(audio16.size, int(sr * 1.2))
    window = max(1, int(sr * 0.02))
    body_start = int(sr * 0.6) if audio16.size > int(sr * 0.8) else int(audio16.size * 0.5)
    body = audio16[body_start:]
    body_rms = float(np.sqrt(np.mean(np.square(body))) + 1e-12) if body.size else 0.0

    flags: list[bool] = []
    chunk_rms: list[float] = []
    chunk_peak: list[float] = []
    for start in range(0, inspect_samples, window):
        chunk = audio16[start:start + window]
        if chunk.size == 0:
            break
        rms = float(np.sqrt(np.mean(np.square(chunk))) + 1e-12)
        peak = float(np.max(np.abs(chunk))) if chunk.size else 0.0
        zcr = float(np.mean(chunk[:-1] * chunk[1:] < 0)) if chunk.size > 1 else 0.0
        spec = np.abs(np.fft.rfft(chunk * np.hanning(chunk.size))) + 1e-9
        freqs = np.fft.rfftfreq(chunk.size, d=1.0 / sr)
        flatness = float(np.exp(np.mean(np.log(spec))) / np.mean(spec)) if spec.size else 1.0
        hi_ratio = float(spec[freqs >= 4000.0].sum() / spec.sum()) if spec.size and spec.sum() > 0 else 1.0
        voiced_like = (
            rms > max(0.006, body_rms * 0.50)
            and zcr < 0.16
            and flatness < 0.28
            and hi_ratio < 0.14
        )
        flags.append(voiced_like)
        chunk_rms.append(rms)
        chunk_peak.append(peak)

    stable_ms = None
    consec = VOICE_DYNAMIC_TRIM_CONSECUTIVE
    silence_needed = 6
    for idx in range(silence_needed, max(0, len(flags) - consec + 1)):
        if sum(flags[idx - silence_needed:idx]) != 0:
            continue
        if all(flags[idx + off] for off in range(consec)):
            stable_ms = idx * 20.0
            break

    if stable_ms is None:
        run = 0
        for idx, flag in enumerate(flags):
            run = run + 1 if flag else 0
            if run >= consec:
                stable_ms = max(0.0, (idx - consec + 1) * 20.0)
                break

    early4_rms = max(chunk_rms[:4], default=0.0)
    early8_rms = max(chunk_rms[:8], default=0.0)
    early4_peak = max(chunk_peak[:4], default=0.0)
    early8_peak = max(chunk_peak[:8], default=0.0)
    burst_trim_ms = 0.0
    if early4_peak >= 0.90 and early4_rms > max(0.10, body_rms * 2.5):
        burst_trim_ms = max(burst_trim_ms, 160.0)
    if early8_peak >= 0.98 and early8_rms > max(0.12, body_rms * 2.2):
        burst_trim_ms = max(burst_trim_ms, 240.0)
    if body_rms < 0.02 and early8_peak >= 0.98 and early8_rms > 0.18:
        burst_trim_ms = max(burst_trim_ms, 320.0)

    trim_ms = max(VOICE_HARD_TRIM_MS, burst_trim_ms, 0.0 if stable_ms is None else stable_ms)
    if trim_ms < VOICE_DYNAMIC_TRIM_MIN_MS:
        trim_ms = max(VOICE_HARD_TRIM_MS, burst_trim_ms)
    trim_ms = min(trim_ms, VOICE_DYNAMIC_TRIM_MAX_MS)
    return trim_ms, {
        "stable_ms": stable_ms,
        "body_rms": body_rms,
        "early4_rms": early4_rms,
        "early8_rms": early8_rms,
        "early4_peak": early4_peak,
        "early8_peak": early8_peak,
        "burst_trim_ms": burst_trim_ms,
        "mode": "16k-leading-scan",
    }


def _round_metric(value: float | None, digits: int = 1) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)



def _build_voice_receive_debug_meta(
    *,
    idx: int,
    ssrc: int,
    packet_count: int,
    expanded_count: int,
    success: int,
    failed: int,
    started_output: bool,
    dave_success: int,
    dave_warmup_skips: int,
    outer_fail: int,
    dave_fail: int,
    opus_fail: int,
    opus_silence_fill: int,
    real_silence: int,
    plc_packets: int,
    fec_packets: int,
    trim_ms: float,
    trim_meta: dict,
    first_packet_wait_ms: float | None,
    queue_wait_ms: float | None,
    decrypt_ms: float | None,
    utterance_total_ms: float | None,
    pcm_bytes_len: int,
) -> dict:
    reasons: list[str] = []

    audio_seconds = pcm_bytes_len / float(max(1, VOICE_PCM_BYTES_PER_MS) * 1000)
    short_clip = audio_seconds < 1.0
    burst_trim_ms = float(trim_meta.get("burst_trim_ms") or 0.0)
    early8_rms = float(trim_meta.get("early8_rms") or 0.0)
    early8_peak = float(trim_meta.get("early8_peak") or 0.0)
    body_rms = float(trim_meta.get("body_rms") or 0.0)

    if not started_output:
        reasons.append("no_started_output")
    if dave_warmup_skips > 0:
        reasons.append(f"dave_warmup_skips={dave_warmup_skips}")
    if outer_fail > 0:
        reasons.append(f"outer_decrypt_fail={outer_fail}")
    if dave_fail > dave_warmup_skips:
        reasons.append(f"dave_fail={dave_fail}")
    if opus_fail >= (8 if short_clip else 4):
        reasons.append(f"opus_fail={opus_fail}")
    if plc_packets >= (4 if short_clip else 2):
        reasons.append(f"plc={plc_packets}")
    if fec_packets >= (6 if short_clip else 4):
        reasons.append(f"fec={fec_packets}")
    if real_silence >= (28 if short_clip else 24):
        reasons.append(f"real_silence={real_silence}")
    if opus_silence_fill > 0:
        reasons.append(f"opus_silence_fill={opus_silence_fill}")
    if failed >= max(5 if short_clip else 3, int(round(packet_count * (0.22 if short_clip else 0.18)))):
        reasons.append(f"high_failed_ratio={failed}/{packet_count}")
    if not short_clip and burst_trim_ms >= 280.0:
        reasons.append(f"burst_trim_ms={int(round(burst_trim_ms))}")
    if not short_clip and trim_ms >= 320.0 and body_rms < 0.010:
        reasons.append(f"heavy_trim_ms={int(round(trim_ms))}")
    if (not short_clip) and early8_peak >= 0.98 and early8_rms > max(0.16, body_rms * 3.0) and body_rms < 0.010:
        reasons.append("front_burst_detected")
    if first_packet_wait_ms is not None and first_packet_wait_ms >= 250.0:
        reasons.append(f"first_packet_wait_ms={int(round(first_packet_wait_ms))}")

    return {
        "unstable": bool(reasons),
        "reasons": reasons,
        "idx": int(idx),
        "ssrc": int(ssrc),
        "audio_seconds": _round_metric(audio_seconds, 3),
        "short_clip": bool(short_clip),
        "packets": {
            "input": int(packet_count),
            "expanded": int(expanded_count),
            "success": int(success),
            "failed": int(failed),
            "started_output": bool(started_output),
        },
        "repair": {
            "dave_success": int(dave_success),
            "dave_warmup_skips": int(dave_warmup_skips),
            "outer_fail": int(outer_fail),
            "dave_fail": int(dave_fail),
            "opus_fail": int(opus_fail),
            "opus_silence_fill": int(opus_silence_fill),
            "real_silence": int(real_silence),
            "plc_packets": int(plc_packets),
            "fec_packets": int(fec_packets),
        },
        "trim": {
            "trim_ms": _round_metric(trim_ms, 1),
            "stable_ms": _round_metric(trim_meta.get("stable_ms"), 1),
            "burst_trim_ms": _round_metric(burst_trim_ms, 1),
            "early4_rms": _round_metric(trim_meta.get("early4_rms"), 4),
            "early8_rms": _round_metric(early8_rms, 4),
            "early4_peak": _round_metric(trim_meta.get("early4_peak"), 4),
            "early8_peak": _round_metric(early8_peak, 4),
            "body_rms": _round_metric(body_rms, 4),
            "mode": str(trim_meta.get("mode") or "unknown"),
        },
        "timing": {
            "first_packet_wait_ms": _round_metric(first_packet_wait_ms, 1),
            "queue_wait_ms": _round_metric(queue_wait_ms, 1),
            "decrypt_ms": _round_metric(decrypt_ms, 1),
            "utterance_total_ms": _round_metric(utterance_total_ms, 1),
        },
        "out_bytes": int(pcm_bytes_len),
    }


def _read_uleb128(buf: bytes, start: int, end: int) -> tuple[int, int]:
    shift = 0
    value = 0
    index = start
    while index < end and shift <= 63:
        byte = buf[index]
        index += 1
        value |= (byte & 0x7F) << shift
        if (byte & 0x80) == 0:
            return value, index
        shift += 7
    raise ValueError("invalid_uleb128")


def parse_dave_payload(payload: bytes) -> DaveSupplemental | None:
    if len(payload) < 2 or payload[-2:] != _DAVE_MARKER:
        return None
    if len(payload) <= 10:
        return None

    supplemental_size = payload[-3]
    if supplemental_size > len(payload) or supplemental_size <= 10:
        return None

    supplemental_start = len(payload) - supplemental_size
    nonce_start = supplemental_start + 8
    supplemental_end = len(payload) - 3
    if nonce_start > supplemental_end:
        return None

    try:
        nonce, cursor = _read_uleb128(payload, nonce_start, supplemental_end)
    except ValueError:
        return None

    ranges: list[tuple[int, int]] = []
    while cursor < supplemental_end:
        try:
            offset, cursor = _read_uleb128(payload, cursor, supplemental_end)
            size, cursor = _read_uleb128(payload, cursor, supplemental_end)
        except ValueError:
            return None
        ranges.append((offset, size))

    ciphertext_len = len(payload) - supplemental_size
    if ciphertext_len <= 0:
        return None

    return DaveSupplemental(
        supplemental_size=supplemental_size,
        supplemental_start=supplemental_start,
        nonce=nonce,
        ranges=tuple(ranges),
        ciphertext_len=ciphertext_len,
    )


def _parse_rtp_header(packet: bytes):
    if len(packet) < 12:
        return None

    b0, b1 = packet[0], packet[1]
    version = (b0 >> 6) & 0b11
    cc = b0 & 0x0F
    x = (b0 >> 4) & 0x01
    marker = (b1 >> 7) & 0x01
    payload_type = b1 & 0x7F
    sequence = struct.unpack_from(">H", packet, 2)[0]
    timestamp = struct.unpack_from(">I", packet, 4)[0]
    ssrc = struct.unpack_from(">I", packet, 8)[0]

    base_header_len = 12 + (cc * 4)
    unencrypted_header_len = base_header_len
    full_header_len = base_header_len

    if x:
        if len(packet) < base_header_len + 4:
            return None

        unencrypted_header_len = base_header_len + 4
        ext_len_words = struct.unpack_from(">H", packet, base_header_len + 2)[0]
        full_header_len = base_header_len + 4 + (ext_len_words * 4)

    if len(packet) < full_header_len:
        return None

    return {
        "version": version,
        "payload_type": payload_type,
        "marker": marker,
        "sequence": sequence,
        "timestamp": timestamp,
        "ssrc": ssrc,
        "header_len": full_header_len,
        "unencrypted_header_len": unencrypted_header_len,
    }


class EvelynVoiceClient(discord.VoiceClient):

    def _dump_frame_probe(
        self,
        *,
        kind: str,
        idx: int,
        group_index: int,
        ts: int,
        first_seq: int,
        last_seq: int,
        packet_count: int,
        frame_bytes: bytes,
        note: str = "",
    ) -> None:
        return

    def _sync_dave_from_base(self) -> None:
        base_conn = getattr(self, "_connection", None)
        base_dave = getattr(base_conn, "dave_session", None)

        if base_dave is None:
            log.info("DAVE BASE SYNC | no base dave_session yet")
            return

        if self.dave.session is not base_dave:
            self.dave.session = base_dave
            self.dave.own_user_id = int(self.client.user.id) if self.client.user else None
            self.dave.channel_id = int(self.channel.id)
            log.info("DAVE BASE SYNC | adopted base dave_session object")

        # custom wrapper 상태 갱신
        self.dave._refresh_state("sync_from_base")

        self.runtime.dave_ready = self.dave.ready
        self.runtime.dave_status = str(self.dave.status)
        self.runtime.dave_epoch = self.dave.epoch
        self.runtime.dave_protocol_version = self.dave.protocol_version

        log.info(
            "DAVE BASE SYNC | ready=%s status=%r epoch=%r proto=%r",
            self.dave.ready,
            self.dave.status,
            self.dave.epoch,
            self.dave.protocol_version,
        )

    def __init__(self, client: discord.Client, channel: discord.abc.Connectable):
        super().__init__(client, channel)

        self.runtime = VoiceRuntimeState(
            guild_id=getattr(channel.guild, "id", None),
            channel_id=getattr(channel, "id", None),
        )
        self.dave_inner_fail_log_count = 0
        self.dave_inner_fail_log_limit = 3

        self.dave = DaveSession()
        self.gateway = VoiceGateway(self.runtime, self.dave)
        self.opus_decoder = Decoder()

        self.udp_transport: VoiceUDPTransport | None = None
        self.sink: AudioSink | None = None
        self.on_user_audio = on_user_audio

        self._receive_task: asyncio.Task | None = None
        self._decrypt_task: asyncio.Task | None = None
        self._utterance_task: asyncio.Task | None = None

        self.media_queue: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self.media_packet_count = 0
        self.decrypt_packet_count = 0

        self.end_silence_sec = float(os.getenv("VOICE_END_SILENCE_SEC", "0.52"))
        self.voice_payload_threshold = 60
        self.preroll_packet_limit = max(0, int(round(float(os.getenv("VOICE_PREROLL_MS", "520")) / 20.0)))

        self.utterance_states: dict[int, dict] = {}
        self.pending_ssrc_packets: dict[int, deque] = {}
        self.pending_inner_packets: dict[int, list[dict]] = {}
        self.pending_inner_log_times: dict[int, float] = {}
        self.unknown_ssrc_log_times: dict[int, float] = {}
        self.utterance_count = 0
        self.utterance_queue: asyncio.Queue = asyncio.Queue(maxsize=32)
        self._utterance_processing_tasks: set[asyncio.Task] = set()
        self.connected_at: float | None = None

    def _latency_ms(self, started_at: float | None) -> float | None:
        if started_at is None:
            return None
        return (asyncio.get_running_loop().time() - float(started_at)) * 1000.0

    def _should_log_timing(self, *values: float | None) -> bool:
        return any(value is not None and value >= VOICE_TIMING_LOG_THRESHOLD_MS for value in values)

    @staticmethod
    def _sequence_gap(previous_seq: int | None, current_seq: int) -> int:
        if previous_seq is None:
            return 0
        return max(0, ((int(current_seq) - int(previous_seq) - 1) & 0xFFFF))

    @staticmethod
    def _ordered_unique_packets(packets: list[dict]) -> list[dict]:
        if len(packets) <= 1:
            return packets

        anchor = int(packets[0].get("sequence", 0))
        ordered: dict[int, dict] = {}
        for packet in packets:
            seq = int(packet.get("sequence", 0))
            delta = (seq - anchor) & 0xFFFF
            existing = ordered.get(delta)
            if existing is None:
                ordered[delta] = packet
                continue

            existing_has_raw = existing.get("raw_packet") is not None
            packet_has_raw = packet.get("raw_packet") is not None
            if packet_has_raw and not existing_has_raw:
                ordered[delta] = packet
            elif packet_has_raw == existing_has_raw and len(packet.get("payload", b"")) >= len(existing.get("payload", b"")):
                ordered[delta] = packet

        return [ordered[key] for key in sorted(ordered)]

    def _expand_packets_with_fakes(self, packets: list[dict]) -> list[dict]:
        if not packets:
            return []

        expanded: list[dict] = []
        prev_seq: int | None = None
        for packet in packets:
            seq = int(packet.get("sequence", 0))
            gap = self._sequence_gap(prev_seq, seq)
            if gap > 0:
                gap_to_fill = min(gap, VOICE_GAP_CONCEAL_MAX)
                for missing_idx in range(gap_to_fill):
                    fake_seq = ((prev_seq or 0) + missing_idx + 1) & 0xFFFF
                    expanded.append(
                        {
                            "fake_packet": True,
                            "fec_candidate": missing_idx == (gap_to_fill - 1),
                            "sequence": fake_seq,
                            "timestamp": packet.get("timestamp"),
                            "payload": b"",
                        }
                    )
            expanded.append({**packet, "fake_packet": False, "fec_candidate": False})
            prev_seq = seq

        return expanded

    def _decrypt_standard_voice_packet(self, packet_bytes: bytes) -> tuple[bytes, dict] | None:
        info = _parse_rtp_header(packet_bytes)
        if info is None:
            return None

        mode = self.runtime.voice_mode
        key = self.runtime.voice_secret_key

        if mode != "aead_xchacha20_poly1305_rtpsize":
            return None
        if not key:
            return None

        encrypted = packet_bytes[info["unencrypted_header_len"]:]
        if len(encrypted) < 4:
            return None

        nonce_suffix = encrypted[-4:]
        ciphertext = encrypted[:-4]

        nonce = bytearray(24)
        nonce[:4] = nonce_suffix
        nonce = bytes(nonce)

        aad_candidates = [packet_bytes[:12]]

        unenc_header_len = info["unencrypted_header_len"]
        if unenc_header_len > 12:
            aad_candidates.append(packet_bytes[:unenc_header_len])

        decrypted_extension_len = max(0, info["header_len"] - info["unencrypted_header_len"])

        for aad in aad_candidates:
            try:
                plaintext = crypto_aead_xchacha20poly1305_ietf_decrypt(
                    ciphertext,
                    aad,
                    nonce,
                    key,
                )
                if decrypted_extension_len:
                    if len(plaintext) < decrypted_extension_len:
                        log.warning(
                            "STD DECRYPT ext underflow | ssrc=%s seq=%s ts=%s plain_len=%d ext_len=%d",
                            info["ssrc"],
                            info["sequence"],
                            info["timestamp"],
                            len(plaintext),
                            decrypted_extension_len,
                        )
                        return None
                    plaintext = plaintext[decrypted_extension_len:]
                return plaintext, info
            except Exception:
                pass

        log.warning(
            "STD DECRYPT failed | ssrc=%s seq=%s ts=%s mode=%s header_len=%s unenc_header_len=%s enc_len=%s",
            info["ssrc"],
            info["sequence"],
            info["timestamp"],
            mode,
            info["header_len"],
            info["unencrypted_header_len"],
            len(encrypted),
        )
        return None

    async def connect(
        self,
        *,
        timeout: float,
        reconnect: bool,
        self_deaf: bool = False,
        self_mute: bool = False,
    ) -> None:
        log.info(
            "EvelynVoiceClient.connect() called | timeout=%s reconnect=%s",
            timeout,
            reconnect,
        )

        self.dave.init_session(
            user_id=int(self.client.user.id),
            channel_id=int(self.channel.id),
        )
        self.runtime.dave_protocol_version = self.dave.protocol_version
        self.runtime.dave_ready = self.dave.ready
        self.runtime.dave_status = str(self.dave.status)

        connect_task = asyncio.create_task(
            super().connect(
                timeout=timeout,
                reconnect=reconnect,
                self_deaf=self_deaf,
                self_mute=self_mute,
            )
        )

        hook_deadline = asyncio.get_running_loop().time() + max(1.5, min(float(timeout), 8.0))
        while not connect_task.done() and asyncio.get_running_loop().time() < hook_deadline:
            ws = getattr(self, "ws", None)
            if (
                ws is not None
                and hasattr(ws, "received_message")
                and not getattr(ws, "_evelyn_gateway_hooked", False)
            ):
                self.gateway.bind_ws(ws)
                log.info("VOICE WS HOOK EARLY | handshake_phase=true")
                break
            await asyncio.sleep(0.01)

        await connect_task

        self.runtime.endpoint = getattr(self, "endpoint", None)
        self.runtime.session_id = getattr(self, "session_id", None)
        self.runtime.token = getattr(self, "token", None)

        # SESSION_DESCRIPTION 백필
        ws_secret = getattr(self.ws, "secret_key", None)
        vc_mode = getattr(self, "mode", None)
        vc_secret = getattr(self, "secret_key", None)

        if self.runtime.voice_mode is None and vc_mode:
            self.runtime.voice_mode = vc_mode

        if self.runtime.voice_secret_key is None:
            if isinstance(vc_secret, (bytes, bytearray)) and vc_secret:
                self.runtime.voice_secret_key = bytes(vc_secret)
            elif isinstance(ws_secret, list) and ws_secret:
                try:
                    self.runtime.voice_secret_key = bytes(int(x) & 0xFF for x in ws_secret)
                except Exception as e:
                    log.warning("CONNECT BACKFILL | ws.secret_key parse failed | err=%r", e)

        log.info(
            "CONNECT BACKFILL | mode=%r key_len=%s ws_secret=%s",
            self.runtime.voice_mode,
            len(self.runtime.voice_secret_key) if self.runtime.voice_secret_key else None,
            isinstance(ws_secret, list) and len(ws_secret) or None,
        )

        self.gateway.bind_ws(self.ws)
        self.connected_at = asyncio.get_running_loop().time()

        await self.gateway.connect()

        await self.gateway.start()

        # TEMP: custom gateway apply 대신 base discord dave_session 기준으로 동기화
        self._sync_dave_from_base()
        self._enable_dave_passthrough()

        self.gateway.try_apply_pending_dave()

        log.info(
            "Base voice connected | endpoint=%s session_id=%s ssrc=%s",
            self.runtime.endpoint,
            self.runtime.session_id,
            getattr(self, "ssrc", None),
        )

        base_sock = self._find_base_udp_socket()
        log.info("Base UDP socket found: %r", base_sock)

        if base_sock is None:
            log.warning("Could not find base discord.py UDP socket")
        else:
            self.udp_transport = VoiceUDPTransport(base_sock)
            await self.udp_transport.open()
            self.runtime.udp_ready.set()

        log.info("EvelynVoiceClient ready | udp=%s", self.runtime.udp_ready.is_set())
    
    def _get_base_dave_session(self):
        base_conn = getattr(self, "_connection", None)
        return getattr(base_conn, "dave_session", None)

    def _enable_dave_passthrough(self) -> None:
        base_dave = self._get_base_dave_session()
        if base_dave is None:
            return

        try:
            base_dave.set_passthrough_mode(True, 10)
            log.info("DAVE passthrough enabled")
        except Exception as e:
            log.warning("DAVE passthrough enable failed | err=%r", e)

    def _dave_can_passthrough(self, user_id: int | None) -> bool:
        if user_id is None:
            return False

        base_dave = self._get_base_dave_session()
        if base_dave is None:
            return False

        checker = getattr(base_dave, "can_passthrough", None)
        if checker is None:
            return False

        try:
            return bool(checker(int(user_id)))
        except Exception:
            return False

    def _get_dave_decryption_stats(self, user_id: int | None) -> dict | None:
        if user_id is None:
            return None

        base_dave = self._get_base_dave_session()
        if base_dave is None:
            return None

        getter = getattr(base_dave, "get_decryption_stats", None)
        if getter is None:
            return None

        try:
            stats = getter(int(user_id), davey.MediaType.audio)
        except Exception:
            return None

        if stats is None:
            return None

        return {
            "attempts": getattr(stats, "attempts", None),
            "successes": getattr(stats, "successes", None),
            "failures": getattr(stats, "failures", None),
            "passthroughs": getattr(stats, "passthroughs", None),
            "duration": getattr(stats, "duration", None),
        }

    def _get_dave_known_user_stats(self) -> dict[int, dict | None]:
        return {
            int(user_id): self._get_dave_decryption_stats(int(user_id))
            for user_id in self._get_dave_known_user_ids()
        }

    def _get_dave_known_user_ids(self) -> list[int]:
        base_dave = self._get_base_dave_session()
        if base_dave is None:
            return []

        getter = getattr(base_dave, "get_user_ids", None)
        if getter is None:
            return []

        try:
            return [int(uid) for uid in getter()]
        except Exception:
            return []

    def _candidate_dave_user_ids(self, primary_user_id: int, ssrc: int | None = None) -> list[int]:
        candidates: list[int] = []

        def add(value) -> None:
            if value is None:
                return
            try:
                value_i = int(value)
            except Exception:
                return
            if value_i not in candidates:
                candidates.append(value_i)

        add(primary_user_id)
        if ssrc is not None:
            add(self.runtime.dave_ssrc_to_user_id.get(int(ssrc)))
            add(self.runtime.get_preferred_user_id(int(ssrc)))
        add(self.runtime.current_speaking_user_id)

        for known_user_id in self._get_dave_known_user_ids():
            add(known_user_id)

        for mapped_user_id in self.runtime.ssrc_to_user_id.values():
            add(mapped_user_id)

        try:
            for member in getattr(self.channel, "members", []):
                if not getattr(member, "bot", False):
                    add(member.id)
        except Exception:
            pass

        return candidates

    @staticmethod
    def _is_retryable_inner_reason(reason: str | None) -> bool:
        return reason in {"not_ready", "no_session", "no_valid_cryptor", "retry_candidate_failed", "cryptor_pending"}

    @staticmethod
    def _is_terminal_inner_reason(reason: str | None) -> bool:
        return reason in {"empty", "silence", "passthrough", "passthrough_disabled", "error", "plain", "strip_only"}

    @staticmethod
    def _normalize_inner_error_reason(err_text: str, *, current_successes: int = 0) -> str:
        if "NoValidCryptorFound" in err_text:
            return "no_valid_cryptor" if int(current_successes or 0) > 0 else "cryptor_pending"
        if "UnencryptedWhenPassthroughDisabled" in err_text:
            return "passthrough_disabled"
        if "not ready" in err_text.lower() or "session" in err_text.lower() and "ready" in err_text.lower():
            return "not_ready"
        return "error"

    def _log_pending_inner_event(self, *, ssrc: int, reason: str, message: str, level: int = logging.WARNING) -> None:
        now = asyncio.get_running_loop().time()
        last_logged = float(self.pending_inner_log_times.get(int(ssrc), 0.0))
        if (now - last_logged) < VOICE_PENDING_INNER_LOG_INTERVAL_SEC:
            return
        self.pending_inner_log_times[int(ssrc)] = now
        log.log(level, "DAVE INNER PENDING | ssrc=%d reason=%s %s", ssrc, reason, message)

    def _try_dave_inner_decrypt(self, *, user_id: int, ssrc: int | None, outer_plain: bytes) -> tuple[bytes | None, int | None, str]:
        if not outer_plain:
            return None, None, "empty"

        if outer_plain == b"\xF8\xFF\xFE":
            return outer_plain, user_id, "silence"

        base_dave = self._get_base_dave_session()
        if base_dave is None:
            log.warning("DAVE INNER | no base dave_session")
            return None, None, "no_session"

        allow_passthrough = self._dave_can_passthrough(user_id)
        if not getattr(base_dave, "ready", False):
            return ((outer_plain, user_id, "passthrough_not_ready") if allow_passthrough else (None, None, "not_ready"))

        try:
            decrypted = base_dave.decrypt(
                int(user_id),
                davey.MediaType.audio,
                bytes(outer_plain),
            )
            return decrypted, user_id, "ok"
        except Exception as e:
            err_text = repr(e)
            log_allowed = self.dave_inner_fail_log_count < self.dave_inner_fail_log_limit
            current_stats = self._get_dave_decryption_stats(user_id)
            current_successes = int((current_stats or {}).get("successes") or 0)
            normalized_reason = self._normalize_inner_error_reason(err_text, current_successes=current_successes)

            if normalized_reason == "passthrough_disabled":
                try:
                    self._enable_dave_passthrough()
                except Exception:
                    pass
                if log_allowed and allow_passthrough:
                    log.info(
                        "DAVE INNER passthrough | user_id=%s in_len=%d prefix=%s",
                        user_id,
                        len(outer_plain),
                        outer_plain[:8].hex(),
                    )
                return (outer_plain, user_id, "passthrough") if allow_passthrough else (None, None, "passthrough_disabled")

            if normalized_reason in {"no_valid_cryptor", "cryptor_pending"}:
                for candidate_user_id in self._candidate_dave_user_ids(user_id, ssrc):
                    if candidate_user_id == int(user_id):
                        continue
                    try:
                        decrypted = base_dave.decrypt(
                            int(candidate_user_id),
                            davey.MediaType.audio,
                            bytes(outer_plain),
                        )
                        if ssrc is not None:
                            self.runtime.bind_dave_ssrc(int(candidate_user_id), int(ssrc))
                            self.pending_inner_log_times.pop(int(ssrc), None)
                        log.warning(
                            "DAVE INNER remap | old_user_id=%s new_user_id=%s ssrc=%s in_len=%d prefix=%s",
                            user_id,
                            candidate_user_id,
                            ssrc,
                            len(outer_plain),
                            outer_plain[:8].hex(),
                        )
                        return decrypted, candidate_user_id, "remap"
                    except Exception:
                        pass
                if normalized_reason == "cryptor_pending":
                    return None, user_id, "cryptor_pending"
                if current_successes > 0:
                    return None, user_id, "no_valid_cryptor"

            if log_allowed:
                log.warning(
                    "DAVE INNER failed | user_id=%s ssrc=%s preferred_user_id=%s in_len=%d passthrough=%s reason=%s prefix=%s stats=%r known_ids=%r known_stats=%r dave_ssrc_map=%r ssrc_map=%r candidates=%r dave_ready=%s dave_status=%r dave_epoch=%r dave_proto=%r last_ws_op=%r last_server_seq=%r err=%r",
                    user_id,
                    ssrc,
                    self.runtime.get_preferred_user_id(int(ssrc)) if ssrc is not None else None,
                    len(outer_plain),
                    allow_passthrough,
                    normalized_reason,
                    outer_plain[:8].hex(),
                    current_stats,
                    self._get_dave_known_user_ids(),
                    self._get_dave_known_user_stats(),
                    dict(self.runtime.dave_ssrc_to_user_id),
                    dict(self.runtime.ssrc_to_user_id),
                    self._candidate_dave_user_ids(user_id, ssrc),
                    self.dave.ready,
                    self.dave.status,
                    self.dave.epoch,
                    self.dave.protocol_version,
                    self.runtime.last_voice_ws_op,
                    self.runtime.last_server_seq,
                    e,
                )
            self.dave_inner_fail_log_count += 1
            return None, user_id, normalized_reason

    def _queue_pending_inner_packet(self, *, ssrc: int, packet: dict, payload: bytes, user_id: int, ranges_count: int, reason: str) -> None:
        now = asyncio.get_running_loop().time()
        queue = self.pending_inner_packets.setdefault(int(ssrc), [])
        queue.append(
            {
                "packet": dict(packet),
                "payload": bytes(payload),
                "user_id": int(user_id),
                "queued_at": now,
                "last_attempt_at": None,
                "attempts": 0,
                "ranges_count": int(ranges_count),
                "first_reason": reason,
                "last_reason": reason,
                "sequence": int(packet.get("sequence", 0)),
            }
        )
        if len(queue) > VOICE_PENDING_SSRC_MAX_PACKETS:
            del queue[:-VOICE_PENDING_SSRC_MAX_PACKETS]
        self._log_pending_inner_event(
            ssrc=int(ssrc),
            reason=reason,
            message=f"queued seq={int(packet.get('sequence', 0))} pending={len(queue)} ranges={int(ranges_count)}",
            level=logging.DEBUG,
        )

    def _prune_pending_ssrc_packets(self, *, now: float | None = None) -> None:
        if now is None:
            now = asyncio.get_running_loop().time()
        stale_ssrc: list[int] = []
        for ssrc, queue in list(self.pending_ssrc_packets.items()):
            kept = [p for p in queue if (now - float(p.get("received_at", now))) <= VOICE_UNKNOWN_SSRC_MAX_AGE_SEC]
            if kept:
                self.pending_ssrc_packets[int(ssrc)] = deque(kept, maxlen=VOICE_PENDING_SSRC_MAX_PACKETS)
            else:
                stale_ssrc.append(int(ssrc))
        for ssrc in stale_ssrc:
            self.pending_ssrc_packets.pop(ssrc, None)
            self.unknown_ssrc_log_times.pop(ssrc, None)

    def _pending_ssrc_snapshot(self, *, ssrc: int, now: float | None = None) -> tuple[list[dict], float]:
        if now is None:
            now = asyncio.get_running_loop().time()
        self._prune_pending_ssrc_packets(now=now)
        pending = list(self.pending_ssrc_packets.get(int(ssrc), ()))
        if not pending:
            return [], 0.0
        oldest = float(pending[0].get("received_at", now))
        return self._ordered_unique_packets(pending), max(0.0, now - oldest)

    def _log_unknown_ssrc_anomaly(self, *, idx: int, ssrc: int, pending_count: int, pending_age_sec: float, map_retry: int, reason: str) -> None:
        now = asyncio.get_running_loop().time()
        last_logged = float(self.unknown_ssrc_log_times.get(int(ssrc), 0.0))
        if (now - last_logged) < VOICE_UNKNOWN_SSRC_LOG_INTERVAL_SEC:
            return
        self.unknown_ssrc_log_times[int(ssrc)] = now
        log.warning(
            "UNKNOWN SSRC HOLD | idx=%d ssrc=%d pending=%d age_sec=%.2f retry=%d current_user=%s current_ssrc=%s reason=%s",
            idx,
            ssrc,
            pending_count,
            pending_age_sec,
            map_retry,
            self.runtime.current_speaking_user_id,
            self.runtime.current_speaking_ssrc,
            reason,
        )

    def _drain_pending_inner_packets(self, *, ssrc: int, user_id: int) -> list[dict]:
        pending = self.pending_inner_packets.get(int(ssrc))
        if not pending:
            return []

        now = asyncio.get_running_loop().time()
        recovered: list[dict] = []
        kept: list[dict] = []
        dropped = 0
        for item in pending:
            queued_at = float(item.get("queued_at", now))
            if (now - queued_at) > VOICE_PENDING_INNER_MAX_AGE_SEC:
                dropped += 1
                continue

            plain, resolved_user_id, reason = self._try_dave_inner_decrypt(
                user_id=int(item.get("user_id") or user_id),
                ssrc=int(ssrc),
                outer_plain=item["payload"],
            )
            if plain is not None:
                packet = dict(item["packet"])
                packet["opus_packet"] = plain
                packet["used_dave_inner"] = True
                packet["fake_packet"] = False
                packet["fec_candidate"] = False
                if resolved_user_id is not None:
                    self.runtime.bind_dave_ssrc(int(resolved_user_id), int(ssrc))
                    self.pending_inner_log_times.pop(int(ssrc), None)
                recovered.append(packet)
                self._log_pending_inner_event(
                    ssrc=int(ssrc),
                    reason=reason,
                    message=f"recovered seq={int(item.get('sequence', 0))} attempts={int(item.get('attempts', 0))} pending_left={max(0, len(pending) - len(recovered))}",
                    level=logging.DEBUG,
                )
                continue

            item["attempts"] = int(item.get("attempts", 0)) + 1
            item["last_attempt_at"] = now
            item["last_reason"] = reason
            if self._is_retryable_inner_reason(reason) and int(item["attempts"]) < VOICE_PENDING_INNER_MAX_ATTEMPTS:
                kept.append(item)
            else:
                dropped += 1

        if kept:
            self.pending_inner_packets[int(ssrc)] = kept
        else:
            self.pending_inner_packets.pop(int(ssrc), None)

        if dropped > 0:
            self._log_pending_inner_event(
                ssrc=int(ssrc),
                reason="drop",
                message=f"dropped={dropped} recovered={len(recovered)} kept={len(kept)}",
                level=logging.WARNING,
            )

        return recovered

    def _resolve_dave_audio_payload(self, *, user_id: int, ssrc: int, outer_plain: bytes, packet_meta: dict) -> tuple[bytes | None, int | None, str]:
        parsed = parse_dave_payload(outer_plain)
        if parsed is None:
            return outer_plain, user_id, "plain"

        plain, resolved_user_id, reason = self._try_dave_inner_decrypt(
            user_id=int(user_id),
            ssrc=int(ssrc),
            outer_plain=outer_plain,
        )
        if plain is not None and reason in {"ok", "remap", "silence", "passthrough"}:
            if resolved_user_id is not None:
                self.runtime.bind_dave_ssrc(int(resolved_user_id), int(ssrc))
            return plain, resolved_user_id, f"inner_{reason}"

        if parsed.ranges_count == 0 and 0 < parsed.ciphertext_len <= len(outer_plain):
            stripped = outer_plain[:parsed.ciphertext_len]
            log.info(
                "DAVE SUPPLEMENTAL STRIP | ssrc=%s seq=%s nonce=%s cipher_len=%s",
                ssrc,
                packet_meta.get("sequence"),
                parsed.nonce,
                parsed.ciphertext_len,
            )
            return stripped, user_id, "strip_only"

        if self._is_retryable_inner_reason(reason):
            self._queue_pending_inner_packet(
                ssrc=int(ssrc),
                packet=packet_meta,
                payload=outer_plain,
                user_id=int(resolved_user_id or user_id),
                ranges_count=parsed.ranges_count,
                reason=reason,
            )
            return None, resolved_user_id or user_id, f"deferred_{reason}"

        if not self._is_terminal_inner_reason(reason):
            self._log_pending_inner_event(
                ssrc=int(ssrc),
                reason=reason,
                message=f"terminal seq={packet_meta.get('sequence')} ranges={parsed.ranges_count}",
                level=logging.WARNING,
            )
        return None, resolved_user_id or user_id, f"unhandled_{reason}"

    def _decode_fake_packet_pcm(self, *, idx: int, packet_index: int, packet: dict, next_packet: dict | None) -> dict:
        result = {
            "pcm": b"",
            "failed": 0,
            "plc": 0,
            "fec": 0,
            "opus_fail": 0,
            "real_silence": 0,
            "opus_silence_fill": 0,
            "status": "fake_drop",
        }

        if packet.get("fec_candidate") and next_packet is not None and not next_packet.get("fake_packet"):
            next_opus = next_packet.get("opus_packet") or b""
            if next_opus not in (b"", b"\xF8\xFF\xFE"):
                try:
                    pcm = self.opus_decoder.decode(next_opus, fec=True)
                except Exception:
                    pcm = b""
                if pcm:
                    result["pcm"] = pcm
                    result["fec"] = 1
                    result["status"] = "fake_fec"
                    log.debug(
                        "PACKET fake -> FEC | idx=%d pkt=%d seq=%s next_seq=%s",
                        idx,
                        packet_index,
                        packet.get("sequence"),
                        next_packet.get("sequence"),
                    )
                    return result

        try:
            pcm = self.opus_decoder.decode(None, fec=False)
        except Exception:
            pcm = b""
        if pcm:
            result["pcm"] = pcm
            result["plc"] = 1
            result["status"] = "fake_plc"
            log.debug(
                "PACKET fake -> PLC | idx=%d pkt=%d seq=%s",
                idx,
                packet_index,
                packet.get("sequence"),
            )
            return result

        result["failed"] = 1
        return result

    def _decode_opus_packet_pcm(self, *, idx: int, packet_index: int, packet: dict, opus_packet: bytes, next_packet: dict | None, silence_pcm: bytes) -> dict:
        result = {
            "pcm": b"",
            "failed": 0,
            "plc": 0,
            "fec": 0,
            "opus_fail": 0,
            "real_silence": 0,
            "opus_silence_fill": 0,
            "status": "drop",
        }

        if opus_packet == b"\xF8\xFF\xFE":
            result["pcm"] = silence_pcm
            result["real_silence"] = 1
            result["status"] = "silence"
            return result

        if len(opus_packet) < 8:
            result["failed"] = 1
            result["opus_fail"] = 1
            result["status"] = "too_short"
            if packet_index <= 5:
                log.warning(
                    "PACKET too short | idx=%d pkt=%d seq=%d ts=%d len=%d",
                    idx,
                    packet_index,
                    packet["sequence"],
                    packet["timestamp"],
                    len(opus_packet),
                )
            return result

        try:
            pcm = self.opus_decoder.decode(opus_packet, fec=False)
            result["pcm"] = pcm
            result["status"] = "ok"
            return result
        except Exception as e:
            result["failed"] = 1
            result["opus_fail"] = 1
            result["status"] = "decode_fail"
            if packet_index <= 5:
                log.warning(
                    "PACKET OPUS failed | idx=%d pkt=%d seq=%d ts=%d bytes=%d err=%r",
                    idx,
                    packet_index,
                    packet["sequence"],
                    packet["timestamp"],
                    len(opus_packet),
                    e,
                )

        if next_packet is not None and not next_packet.get("fake_packet"):
            next_opus = next_packet.get("opus_packet") or b""
            if next_opus not in (b"", b"\xF8\xFF\xFE"):
                try:
                    pcm = self.opus_decoder.decode(next_opus, fec=True)
                except Exception:
                    pcm = b""
                if pcm:
                    result["pcm"] = pcm
                    result["fec"] = 1
                    result["status"] = "decode_fail_fec"
                    log.debug(
                        "PACKET OPUS corrupt -> FEC | idx=%d pkt=%d seq=%d next_seq=%d",
                        idx,
                        packet_index,
                        packet["sequence"],
                        next_packet.get("sequence"),
                    )
                    return result

        try:
            pcm = self.opus_decoder.decode(None, fec=False)
        except Exception:
            pcm = b""
        if pcm:
            result["pcm"] = pcm
            result["plc"] = 1
            result["status"] = "decode_fail_plc"
            log.debug(
                "PACKET OPUS corrupt -> PLC | idx=%d pkt=%d seq=%d",
                idx,
                packet_index,
                packet["sequence"],
            )
            return result

        if OPUS_ERROR_TO_SILENCE:
            result["pcm"] = silence_pcm
            result["opus_silence_fill"] = 1
            result["status"] = "decode_fail_silence"
            return result

        return result

    def _maybe_insert_leading_silence(
        self,
        *,
        idx: int,
        packet_index: int,
        packet: dict,
        started_output: bool,
        leading_bad_packets: int,
        pcm_chunks: list[bytes],
        silence_pcm: bytes,
        label: str,
    ) -> tuple[bool, bool, int]:
        if started_output or leading_bad_packets >= VOICE_LEADING_DROP_MAX_PACKETS:
            return False, started_output, leading_bad_packets

        leading_bad_packets += 1
        pcm_chunks.append(silence_pcm)
        log.debug(
            "%s -> SILENCE | idx=%d pkt=%d seq=%s",
            label,
            idx,
            packet_index,
            packet.get("sequence"),
        )
        return True, True, leading_bad_packets

    def _apply_output_packet_gating(
        self,
        *,
        pcm: bytes,
        started_output: bool,
        stable_voice_packets: int,
        leading_good_drop_remaining: int,
        pcm_chunks: list[bytes],
    ) -> tuple[bool, int, int, bool]:
        stable_voice_packets += 1
        if not started_output and stable_voice_packets < VOICE_START_STABLE_PACKETS:
            return started_output, stable_voice_packets, leading_good_drop_remaining, False
        if not started_output and leading_good_drop_remaining > 0:
            leading_good_drop_remaining -= 1
            return started_output, stable_voice_packets, leading_good_drop_remaining, False

        started_output = True
        pcm_chunks.append(pcm)
        return started_output, stable_voice_packets, leading_good_drop_remaining, True

    def is_connected(self) -> bool:
        return super().is_connected()

    def is_listening(self) -> bool:
        return self._receive_task is not None and not self._receive_task.done()

    def listen(self, sink: AudioSink | None = None) -> None:
        if self.udp_transport is None:
            raise RuntimeError("UDP transport가 아직 준비되지 않았습니다. 먼저 join 상태를 확인하세요.")

        if sink is None:
            sink = NullSink()
        self.sink = sink

        if self._receive_task is None:
            self._receive_task = asyncio.create_task(self._receive_loop())

        if self._decrypt_task is None:
            self._decrypt_task = asyncio.create_task(self._decrypt_loop())

        if self._utterance_task is None:
            self._utterance_task = asyncio.create_task(self._utterance_loop())

        log.info("Receive loop started")

    async def _receive_loop(self) -> None:
        assert self.udp_transport is not None
        self.runtime.receive_ready.set()

        try:
            while True:
                packet = await self.udp_transport.recv_packet()
                info = _parse_rtp_header(packet)
                if info is None:
                    continue

                if info["payload_type"] != 120:
                    continue

                payload = packet[info["header_len"]:]

                packet_info = {
                    "raw_packet": packet,
                    "ssrc": info["ssrc"],
                    "sequence": info["sequence"],
                    "timestamp": info["timestamp"],
                    "payload_type": info["payload_type"],
                    "marker": info["marker"],
                    "header_len": info["header_len"],
                    "payload": payload,
                    "received_at": asyncio.get_running_loop().time(),
                }

                pending = self.pending_ssrc_packets.setdefault(
                    int(info["ssrc"]),
                    deque(maxlen=VOICE_PENDING_SSRC_MAX_PACKETS),
                )
                pending.append(packet_info)
                self._prune_pending_ssrc_packets(now=packet_info["received_at"])

                try:
                    self.media_queue.put_nowait(packet_info)
                except asyncio.QueueFull:
                    try:
                        _ = self.media_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    self.media_queue.put_nowait(packet_info)

                self.media_packet_count += 1
        except asyncio.CancelledError:
            pass
        finally:
            self.runtime.receive_ready.clear()

    async def _decrypt_loop(self) -> None:
        try:
            while True:
                now = asyncio.get_running_loop().time()

                try:
                    packet_info = await asyncio.wait_for(self.media_queue.get(), timeout=0.05)
                except asyncio.TimeoutError:
                    packet_info = None

                if packet_info is not None:
                    payload = packet_info["payload"]
                    ssrc = packet_info["ssrc"]
                    sequence = packet_info["sequence"]
                    timestamp = packet_info["timestamp"]
                    payload_len = len(payload)

                    current_packet = {
                        "raw_packet": packet_info.get("raw_packet"),
                        "ssrc": ssrc,
                        "sequence": sequence,
                        "timestamp": timestamp,
                        "payload": payload,
                    }

                    state = self.utterance_states.setdefault(
                        ssrc,
                        {
                            "in_utterance": False,
                            "last_voice_like_at": 0.0,
                            "utterance_started_at": None,
                            "packets": [],
                            "preroll": deque(maxlen=self.preroll_packet_limit),
                        },
                    )

                    if payload_len >= self.voice_payload_threshold:
                        state["last_voice_like_at"] = now
                        if not state["in_utterance"]:
                            state["in_utterance"] = True
                            state["utterance_started_at"] = packet_info.get("received_at", now)
                            state["packets"] = list(state["preroll"])
                            if self.media_queue.qsize() * 20 >= VOICE_TIMING_LOG_THRESHOLD_MS:
                                log.info(
                                    "UTTERANCE START | ssrc=%d seq=%d ts=%d payload=%d preroll=%d media_q=%d",
                                    ssrc,
                                    sequence,
                                    timestamp,
                                    payload_len,
                                    len(state["packets"]),
                                    self.media_queue.qsize(),
                                )

                    if state["in_utterance"]:
                        state["packets"].append(current_packet)

                    state["preroll"].append(current_packet)
                    self.decrypt_packet_count += 1

                now = asyncio.get_running_loop().time()

                for ssrc, state in list(self.utterance_states.items()):
                    if not state["in_utterance"]:
                        continue
                    if (now - state["last_voice_like_at"]) < self.end_silence_sec:
                        continue

                    state["in_utterance"] = False
                    self.utterance_count += 1

                    packet_count = len(state["packets"])
                    first_seq = state["packets"][0]["sequence"] if packet_count else -1
                    last_seq = state["packets"][-1]["sequence"] if packet_count else -1

                    utterance_started_at = state.get("utterance_started_at")
                    utterance_age_ms = self._latency_ms(utterance_started_at)
                    if self._should_log_timing(utterance_age_ms):
                        log.info(
                            "UTTERANCE END | idx=%d ssrc=%d packets=%d first_seq=%d last_seq=%d gap=%.3f utterance_ms=%s",
                            self.utterance_count,
                            ssrc,
                            packet_count,
                            first_seq,
                            last_seq,
                            now - state["last_voice_like_at"],
                            f"{utterance_age_ms:.0f}" if utterance_age_ms is not None else "?",
                        )

                    utterance_packets = state["packets"].copy()

                    try:
                        self.utterance_queue.put_nowait(
                            {
                                "idx": self.utterance_count,
                                "ssrc": ssrc,
                                "packets": utterance_packets,
                                "utterance_started_at": utterance_started_at,
                                "utterance_ended_at": now,
                                "queued_at": asyncio.get_running_loop().time(),
                            }
                        )
                    except asyncio.QueueFull:
                        log.warning("utterance_queue is full, dropping utterance idx=%d ssrc=%d", self.utterance_count, ssrc)

                    state["packets"] = []
                    state["utterance_started_at"] = None

        except asyncio.CancelledError:
            pass

    async def _utterance_loop(self) -> None:
        try:
            while True:
                item = await self.utterance_queue.get()

                idx = item["idx"]
                ssrc = item["ssrc"]
                packets = item["packets"]

                packet_count = len(packets)
                first_seq = packets[0]["sequence"] if packet_count else -1
                last_seq = packets[-1]["sequence"] if packet_count else -1
                total_payload = sum(len(p["payload"]) for p in packets)

                queued_at = item.get("queued_at")
                queue_wait_ms = self._latency_ms(queued_at)
                if self._should_log_timing(queue_wait_ms) or self.utterance_queue.qsize() > 0:
                    log.info(
                        "UTTERANCE DISPATCH | idx=%d ssrc=%d packets=%d first_seq=%d last_seq=%d payload=%d active=%d queue_wait_ms=%s utterance_q=%d",
                        idx,
                        ssrc,
                        packet_count,
                        first_seq,
                        last_seq,
                        total_payload,
                        len(self._utterance_processing_tasks),
                        f"{queue_wait_ms:.0f}" if queue_wait_ms is not None else "?",
                        self.utterance_queue.qsize(),
                    )

                task = asyncio.create_task(self._process_utterance_packets(item))
                self._utterance_processing_tasks.add(task)
                task.add_done_callback(self._utterance_processing_tasks.discard)
        except asyncio.CancelledError:
            pass

    async def _process_utterance_packets(self, item: dict) -> None:
        outer_fail = 0
        dave_fail = 0
        opus_fail = 0
        opus_silence_fill = 0
        real_silence = 0
        self.dave_inner_fail_log_count = 0

        idx = item["idx"]
        ssrc = item["ssrc"]
        packets = item["packets"]
        utterance_started_at = item.get("utterance_started_at")
        queued_at = item.get("queued_at")
        processing_started_at = asyncio.get_running_loop().time()
        dave_success = 0
        map_retry = int(item.get("map_retry", 0))

        if not packets:
            return

        packets = self._ordered_unique_packets(list(packets))
        first_seq = packets[0]["sequence"]
        last_seq = packets[-1]["sequence"]
        total_payload = sum(len(p["payload"]) for p in packets)

        user_id = self.runtime.get_preferred_user_id(ssrc)

        if user_id is None:
            try:
                human_members = [m for m in getattr(self.channel, "members", []) if not getattr(m, "bot", False)]
            except Exception:
                human_members = []

            if len(human_members) == 1:
                user_id = int(human_members[0].id)
                self.runtime.bind_ssrc(user_id, ssrc)
                log.info("VOICE MAP FALLBACK | user_id=%d ssrc=%d", user_id, ssrc)

        if user_id is None:
            now = asyncio.get_running_loop().time()
            hold_remaining = 0.0
            if self.connected_at is not None:
                elapsed_ms = (now - float(self.connected_at)) * 1000.0
                hold_remaining = max(0.0, VOICE_INITIAL_MAP_HOLD_MS - elapsed_ms)

            pending_packets, pending_age_sec = self._pending_ssrc_snapshot(ssrc=int(ssrc), now=now)
            retry_delay_ms = max(VOICE_UNKNOWN_SSRC_RETRY_MS, VOICE_MAP_RETRY_MS)
            retry_reason = "unknown_ssrc_wait"
            if hold_remaining > retry_delay_ms:
                retry_delay_ms = hold_remaining
                retry_reason = "initial_map_hold"

            if pending_packets and pending_age_sec <= VOICE_UNKNOWN_SSRC_MAX_AGE_SEC and retry_delay_ms > 0:
                retry_delay = max(0.0, retry_delay_ms / 1000.0)
                self._log_unknown_ssrc_anomaly(
                    idx=idx,
                    ssrc=int(ssrc),
                    pending_count=len(pending_packets),
                    pending_age_sec=pending_age_sec,
                    map_retry=map_retry,
                    reason=retry_reason,
                )
                retry_item = deepcopy(item)
                retry_item["map_retry"] = map_retry + 1
                retry_item["packets"] = pending_packets
                first_pending = pending_packets[0].get("received_at") if pending_packets else None
                if first_pending is not None:
                    retry_item["utterance_started_at"] = first_pending

                async def _requeue_map_retry() -> None:
                    await asyncio.sleep(retry_delay)
                    try:
                        await self.utterance_queue.put(retry_item)
                    except Exception as e:
                        log.warning("VOICE MAP RETRY enqueue failed | idx=%d err=%r", idx, e)

                asyncio.create_task(_requeue_map_retry())
                return

            self._log_unknown_ssrc_anomaly(
                idx=idx,
                ssrc=int(ssrc),
                pending_count=len(pending_packets),
                pending_age_sec=pending_age_sec,
                map_retry=map_retry,
                reason="drop_unknown_ssrc",
            )
            return
        log.info(
            "MAP DEBUG | idx=%d ssrc=%d user_id=%s preferred_user_id=%s dave_user_id=%s",
            idx,
            ssrc,
            user_id,
            self.runtime.get_preferred_user_id(ssrc),
            self.runtime.dave_ssrc_to_user_id.get(ssrc),
        )
        self._sync_dave_from_base()

        use_dave = bool(self.dave.ready)
        use_std = bool(
            self.runtime.voice_secret_key
            and self.runtime.voice_mode == "aead_xchacha20_poly1305_rtpsize"
        )

        if not use_dave and not use_std:
            log.warning(
                "No decrypt path yet; skipping idx=%d | dave_ready=%s mode=%r key=%s",
                idx,
                self.dave.ready,
                getattr(self.runtime, "voice_mode", None),
                bool(getattr(self.runtime, "voice_secret_key", None)),
            )
            return

        success = 0
        failed = 0
        pcm_chunks: list[bytes] = []
        SILENCE_PCM = b"\x00" * (960 * 2 * 2)
        dave_warmup_skips = 0
        plc_packets = 0
        fec_packets = 0
        leading_bad_packets = 0
        stable_voice_packets = 0
        leading_good_drop_remaining = VOICE_LEADING_GOOD_DROP_PACKETS
        started_output = False

        normalized_packets: list[dict] = []
        for packet_index, p in enumerate(packets, start=1):
            raw_packet = p.get("raw_packet")
            if raw_packet is None:
                failed += 1
                outer_fail += 1
                if packet_index <= 5:
                    log.warning(
                        "RAW PACKET missing | idx=%d pkt=%d seq=%d ts=%d",
                        idx,
                        packet_index,
                        p["sequence"],
                        p["timestamp"],
                    )
                continue

            outer_result = self._decrypt_standard_voice_packet(raw_packet)
            if not outer_result:
                failed += 1
                outer_fail += 1
                if packet_index <= 5:
                    log.warning(
                        "OUTER DECRYPT failed | idx=%d pkt=%d seq=%d ts=%d payload=%d",
                        idx,
                        packet_index,
                        p["sequence"],
                        p["timestamp"],
                        len(p["payload"]),
                    )
                continue

            outer_plain, outer_info = outer_result
            used_dave_inner = False
            if use_dave:
                opus_packet, resolved_user_id, dave_reason = self._resolve_dave_audio_payload(
                    user_id=int(user_id),
                    ssrc=int(ssrc),
                    outer_plain=outer_plain,
                    packet_meta=p,
                )
                if resolved_user_id is not None and int(resolved_user_id) != int(user_id):
                    user_id = int(resolved_user_id)
                    self.runtime.bind_dave_ssrc(int(user_id), int(ssrc))
                    log.info("VOICE MAP DAVE REMAP | idx=%d ssrc=%d user_id=%s", idx, ssrc, user_id)

                if opus_packet is None:
                    failed += 1
                    dave_fail += 1
                    if dave_reason.startswith("deferred_") or (dave_success == 0 and dave_fail <= VOICE_DAVE_WARMUP_GRACE_PACKETS):
                        dave_warmup_skips += 1
                        if packet_index <= 5:
                            log.info(
                                "PACKET DAVE defer/skip | idx=%d pkt=%d seq=%d ts=%d reason=%s grace=%d/%d",
                                idx,
                                packet_index,
                                p["sequence"],
                                p["timestamp"],
                                dave_reason,
                                dave_fail,
                                VOICE_DAVE_WARMUP_GRACE_PACKETS,
                            )
                    else:
                        if packet_index <= 3:
                            log.warning(
                                "PACKET DAVE failed | idx=%d pkt=%d seq=%d ts=%d outer_len=%d ext_len=%d reason=%s",
                                idx,
                                packet_index,
                                p["sequence"],
                                p["timestamp"],
                                len(outer_plain),
                                max(0, outer_info["header_len"] - outer_info["unencrypted_header_len"]),
                                dave_reason,
                            )
                    continue

                used_dave_inner = dave_reason.startswith("inner_")
                if used_dave_inner:
                    dave_success += 1
            else:
                opus_packet = outer_plain

            normalized_packets.append({**p, "opus_packet": opus_packet, "used_dave_inner": used_dave_inner})

        normalized_packets.extend(self._drain_pending_inner_packets(ssrc=int(ssrc), user_id=int(user_id)))
        normalized_packets = self._ordered_unique_packets(normalized_packets)
        expanded_packets = self._expand_packets_with_fakes(normalized_packets)
        for packet_index, p in enumerate(expanded_packets, start=1):
            next_packet = expanded_packets[packet_index] if packet_index < len(expanded_packets) else None
            if p.get("fake_packet"):
                decode_result = self._decode_fake_packet_pcm(
                    idx=idx,
                    packet_index=packet_index,
                    packet=p,
                    next_packet=next_packet,
                )
            else:
                decode_result = self._decode_opus_packet_pcm(
                    idx=idx,
                    packet_index=packet_index,
                    packet=p,
                    opus_packet=p.get("opus_packet") or b"",
                    next_packet=next_packet,
                    silence_pcm=SILENCE_PCM,
                )

            failed += int(decode_result.get("failed") or 0)
            opus_fail += int(decode_result.get("opus_fail") or 0)
            plc_packets += int(decode_result.get("plc") or 0)
            fec_packets += int(decode_result.get("fec") or 0)
            real_silence += int(decode_result.get("real_silence") or 0)
            opus_silence_fill += int(decode_result.get("opus_silence_fill") or 0)
            pcm = decode_result.get("pcm") or b""

            if not pcm:
                inserted_silence, started_output, leading_bad_packets = self._maybe_insert_leading_silence(
                    idx=idx,
                    packet_index=packet_index,
                    packet=p,
                    started_output=started_output,
                    leading_bad_packets=leading_bad_packets,
                    pcm_chunks=pcm_chunks,
                    silence_pcm=SILENCE_PCM,
                    label=("LEADING fake packet" if p.get("fake_packet") else "LEADING OPUS fail"),
                )
                if inserted_silence:
                    opus_silence_fill += 1
                    success += 1
                continue

            started_output, stable_voice_packets, leading_good_drop_remaining, appended = self._apply_output_packet_gating(
                pcm=pcm,
                started_output=started_output,
                stable_voice_packets=stable_voice_packets,
                leading_good_drop_remaining=leading_good_drop_remaining,
                pcm_chunks=pcm_chunks,
            )
            if appended:
                success += 1

        decrypt_ms = (asyncio.get_running_loop().time() - processing_started_at) * 1000.0
        utterance_total_ms = self._latency_ms(utterance_started_at)
        queue_wait_ms = self._latency_ms(queued_at)
        first_packet_wait_ms = None
        if packets:
            first_received_at = packets[0].get("received_at")
            if first_received_at is not None:
                first_packet_wait_ms = (processing_started_at - float(first_received_at)) * 1000.0

        if self._should_log_timing(first_packet_wait_ms, queue_wait_ms, decrypt_ms, utterance_total_ms) or dave_warmup_skips > 0 or plc_packets > 0 or fec_packets > 0:
            log.info(
                "DECRYPT SUMMARY | idx=%d packets=%d expanded=%d success=%d failed=%d pcm_chunks=%d dave_ok=%d dave_warmup_skips=%d outer_fail=%d dave_fail=%d opus_fail=%d opus_silence_fill=%d real_silence=%d plc=%d fec=%d started_output=%s first_packet_wait_ms=%s queue_wait_ms=%s decrypt_ms=%.0f utterance_total_ms=%s",
                idx,
                len(packets),
                len(expanded_packets),
                success,
                failed,
                len(pcm_chunks),
                dave_success,
                dave_warmup_skips,
                outer_fail,
                dave_fail,
                opus_fail,
                opus_silence_fill,
                real_silence,
                plc_packets,
                fec_packets,
                started_output,
                f"{first_packet_wait_ms:.0f}" if first_packet_wait_ms is not None else "?",
                f"{queue_wait_ms:.0f}" if queue_wait_ms is not None else "?",
                decrypt_ms,
                f"{utterance_total_ms:.0f}" if utterance_total_ms is not None else "?",
            )

        if not pcm_chunks:
            return

        if dave_success > 0 and user_id is not None:
            self.runtime.bind_dave_ssrc(int(user_id), int(ssrc))

        pcm_bytes = b"".join(pcm_chunks)
        trim_ms, trim_meta = _estimate_leading_trim_ms(pcm_bytes)
        trim_bytes = int(trim_ms * VOICE_PCM_BYTES_PER_MS)
        trim_bytes -= trim_bytes % 4
        min_keep_bytes = VOICE_PCM_BYTES_PER_MS * 120
        if trim_bytes > 0 and len(pcm_bytes) > trim_bytes + min_keep_bytes:
            pcm_bytes = pcm_bytes[trim_bytes:]
            log.info(
                "LEADING PCM TRIM | idx=%d ssrc=%d trim_ms=%.0f stable_ms=%s burst_trim_ms=%.0f early4_rms=%.4f early8_rms=%.4f early4_peak=%.3f early8_peak=%.3f body_rms=%.4f out_bytes=%d",
                idx,
                ssrc,
                trim_ms,
                f"{trim_meta.get('stable_ms'):.0f}" if trim_meta.get("stable_ms") is not None else "?",
                float(trim_meta.get("burst_trim_ms") or 0.0),
                float(trim_meta.get("early4_rms") or 0.0),
                float(trim_meta.get("early8_rms") or 0.0),
                float(trim_meta.get("early4_peak") or 0.0),
                float(trim_meta.get("early8_peak") or 0.0),
                float(trim_meta.get("body_rms") or 0.0),
                len(pcm_bytes),
            )

        voice_debug_meta = _build_voice_receive_debug_meta(
            idx=idx,
            ssrc=ssrc,
            packet_count=len(packets),
            expanded_count=len(expanded_packets),
            success=success,
            failed=failed,
            started_output=started_output,
            dave_success=dave_success,
            dave_warmup_skips=dave_warmup_skips,
            outer_fail=outer_fail,
            dave_fail=dave_fail,
            opus_fail=opus_fail,
            opus_silence_fill=opus_silence_fill,
            real_silence=real_silence,
            plc_packets=plc_packets,
            fec_packets=fec_packets,
            trim_ms=trim_ms,
            trim_meta=trim_meta,
            first_packet_wait_ms=first_packet_wait_ms,
            queue_wait_ms=queue_wait_ms,
            decrypt_ms=decrypt_ms,
            utterance_total_ms=utterance_total_ms,
            pcm_bytes_len=len(pcm_bytes),
        )
        if voice_debug_meta["unstable"]:
            log.warning(
                "VOICE UNSTABLE | idx=%d ssrc=%d reasons=%s packets=%d/%d plc=%d fec=%d outer=%d dave=%d opus=%d trim_ms=%.0f",
                idx,
                ssrc,
                ",".join(voice_debug_meta["reasons"]),
                success,
                len(expanded_packets),
                plc_packets,
                fec_packets,
                outer_fail,
                dave_fail,
                opus_fail,
                trim_ms,
            )

        member = None
        try:
            member = self.channel.guild.get_member(int(user_id))
        except Exception:
            member = None

        if getattr(self, "on_user_audio", None) is not None:
            try:
                callback_started_at = asyncio.get_running_loop().time()
                try:
                    await self.on_user_audio(member, pcm_bytes, debug_meta=voice_debug_meta)
                except TypeError as e:
                    if "debug_meta" not in str(e):
                        raise
                    await self.on_user_audio(member, pcm_bytes)
                callback_ms = (asyncio.get_running_loop().time() - callback_started_at) * 1000.0
                if self._should_log_timing(callback_ms):
                    log.warning("VOICE CALLBACK SLOW | idx=%d pcm_bytes=%d callback_ms=%.0f", idx, len(pcm_bytes), callback_ms)
            except Exception as e:
                log.warning("on_user_audio callback failed | idx=%d err=%r", idx, e)

    def stop_listening(self) -> None:
        if self._receive_task is not None:
            self._receive_task.cancel()
            self._receive_task = None

        if self._decrypt_task is not None:
            self._decrypt_task.cancel()
            self._decrypt_task = None

        if self._utterance_task is not None:
            self._utterance_task.cancel()
            self._utterance_task = None

        for task in list(self._utterance_processing_tasks):
            task.cancel()
        self._utterance_processing_tasks.clear()

        if self.sink is not None:
            self.sink.cleanup()
            self.sink = None

        self.utterance_states.clear()
        self.pending_ssrc_packets.clear()
        self.pending_inner_packets.clear()
        self.pending_inner_log_times.clear()
        self.unknown_ssrc_log_times.clear()

        log.info("Receive loop stopped")

    async def disconnect(self, *, force: bool = False) -> None:
        self.stop_listening()

        if self.gateway is not None:
            await self.gateway.close()

        self.dave.reset()

        if self.udp_transport is not None:
            await self.udp_transport.close()
            self.udp_transport = None

        await super().disconnect(force=force)
        log.info("EvelynVoiceClient disconnected")

    def _find_base_udp_socket(self):
        candidates = {
            "self.socket": getattr(self, "socket", None),
            "self._socket": getattr(self, "_socket", None),
            "self._connection.socket": getattr(getattr(self, "_connection", None), "socket", None),
            "self.ws.socket": getattr(getattr(self, "ws", None), "socket", None),
        }

        if candidates["self._connection.socket"] is not None:
            return candidates["self._connection.socket"]

        if candidates["self.socket"] is not None:
            return candidates["self.socket"]

        return None

    @staticmethod
    def _parse_endpoint(endpoint: str | None) -> tuple[str, int]:
        if not endpoint:
            return "127.0.0.1", 50000

        endpoint = endpoint.replace("wss://", "").replace("ws://", "")
        if ":" in endpoint:
            host, port_text = endpoint.rsplit(":", 1)
            try:
                return host, int(port_text)
            except ValueError:
                return host, 443
        return endpoint, 443