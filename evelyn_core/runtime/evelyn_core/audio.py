import numpy as np

try:
    import torch
except Exception:
    torch = None

try:
    import soxr
except Exception:
    soxr = None

from .config import (
    CHANNELS,
    DENOISE_ENABLED,
    DENOISE_GATE_MULT,
    DENOISE_HIGHPASS_HZ,
    DENOISE_NOISE_FLOOR_SEC,
    RATE,
    SILERO_MIN_SILENCE_MS,
    SILERO_MIN_SPEECH_MS,
    SILERO_SPEECH_PAD_MS,
    SILERO_VAD_ONNX,
    SILERO_VAD_THRESHOLD,
    TARGET_RATE,
    VAD_CHUNK_MS,
    VAD_ENABLED,
    VAD_MIN_VOICED_RATIO,
    VAD_PEAK_THRESHOLD,
    VAD_PROVIDER,
    VAD_RMS_THRESHOLD,
    VAD_START_CONSECUTIVE,
    VOICE_ENV_FLATNESS_MAX,
    VOICE_ENV_RMS_MAX,
    VOICE_HUMAN_BAND_RATIO_MIN,
    VOICE_WAVEFORM_BODY_PEAK_MIN,
    VOICE_WAVEFORM_BODY_RMS_MIN,
)

try:
    from silero_vad import get_speech_timestamps, load_silero_vad
except Exception:
    get_speech_timestamps = None
    load_silero_vad = None

try:
    import torchaudio.functional as torchaudio_F
except Exception:
    torchaudio_F = None

silero_vad_model = None
silero_vad_warned = False
soxr_warned = False


def compute_voice_band_metrics(audio: np.ndarray, sampling_rate: int = TARGET_RATE) -> tuple[float, float, float]:
    """주파수 대역 분포와 flatness, RMS를 계산해 사람 목소리/환경음 구분에 쓴다."""
    if audio.size == 0:
        return 0.0, 1.0, 0.0

    audio = np.asarray(audio, dtype=np.float32)
    spectrum = np.abs(np.fft.rfft(audio))
    if spectrum.size == 0:
        return 0.0, 1.0, 0.0

    effective_rate = max(1, int(sampling_rate))
    freqs = np.fft.rfftfreq(len(audio), d=1.0 / effective_rate)
    total_energy = float(np.sum(spectrum)) + 1e-8
    human_hi = min(3400.0, (effective_rate / 2.0) - 1.0)
    human_mask = (freqs >= 85.0) & (freqs <= max(85.0, human_hi))
    human_energy = float(np.sum(spectrum[human_mask]))
    band_ratio = human_energy / total_energy

    geometric = float(np.exp(np.mean(np.log(spectrum + 1e-8))))
    arithmetic = float(np.mean(spectrum + 1e-8))
    flatness = geometric / arithmetic if arithmetic > 0 else 1.0

    rms = float(np.sqrt(np.mean(np.square(audio))))
    return band_ratio, flatness, rms


def is_likely_environment_noise(audio: np.ndarray, sampling_rate: int = TARGET_RATE) -> bool:
    """저레벨 환경음처럼 보이는 오디오인지 스펙트럼 특성으로 판정한다."""
    band_ratio, flatness, rms = compute_voice_band_metrics(audio, sampling_rate=sampling_rate)
    return (
        rms <= VOICE_ENV_RMS_MAX
        and band_ratio < VOICE_HUMAN_BAND_RATIO_MIN
        and flatness > VOICE_ENV_FLATNESS_MAX
    )


def downmix_and_resample_int16_stereo_to_mono16k(pcm_bytes: bytes) -> np.ndarray:
    audio = np.frombuffer(pcm_bytes, dtype=np.int16)
    if audio.size == 0:
        return np.zeros(0, dtype=np.float32)

    if CHANNELS == 2:
        audio = audio.reshape(-1, 2).mean(axis=1)

    audio = audio.astype(np.float32) / 32768.0

    if RATE != TARGET_RATE:
        audio = resample_audio_float(audio, RATE, TARGET_RATE)

    return audio.astype(np.float32, copy=False)


def apply_light_denoise(audio_in: np.ndarray, sampling_rate: int = TARGET_RATE) -> np.ndarray:
    if not DENOISE_ENABLED or audio_in.size == 0:
        return audio_in

    audio = np.asarray(audio_in, dtype=np.float32).copy()
    effective_rate = max(1, int(sampling_rate))

    if torchaudio_F is not None and torch is not None:
        try:
            tensor = torch.from_numpy(audio)
            tensor = torchaudio_F.highpass_biquad(tensor, effective_rate, DENOISE_HIGHPASS_HZ)
            audio = tensor.cpu().numpy().astype(np.float32)
        except Exception:
            pass

    noise_len = min(len(audio), max(1, int(effective_rate * DENOISE_NOISE_FLOOR_SEC)))
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


def downmix_int16_stereo_to_mono_float(pcm_bytes: bytes) -> np.ndarray:
    audio = np.frombuffer(pcm_bytes, dtype=np.int16)
    if audio.size == 0:
        return np.zeros(0, dtype=np.float32)

    if CHANNELS == 2:
        audio = audio.reshape(-1, 2).mean(axis=1)

    return (audio.astype(np.float32) / 32768.0).astype(np.float32)


def resample_audio_float(audio: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    global soxr_warned

    audio = np.asarray(audio, dtype=np.float32)
    if audio.size == 0:
        return np.zeros(0, dtype=np.float32)

    src_rate = max(1, int(from_rate))
    dst_rate = max(1, int(to_rate))
    if src_rate == dst_rate:
        return audio.astype(np.float32, copy=True)

    if soxr is not None:
        try:
            return np.asarray(soxr.resample(audio, src_rate, dst_rate, quality="HQ"), dtype=np.float32)
        except Exception:
            if not soxr_warned:
                soxr_warned = True
                print(f"[AUDIO RESAMPLE] soxr fallback engaged src={src_rate} dst={dst_rate}")

    new_len = max(1, int(round(len(audio) * (dst_rate / float(src_rate)))))
    x_old = np.linspace(0, 1, len(audio), endpoint=False)
    x_new = np.linspace(0, 1, new_len, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.float32)


def prepare_stt_audio(pcm_bytes: bytes) -> np.ndarray:
    """디스코드 PCM을 mono 16kHz로 바꾸고 경량 denoise까지 적용한다."""
    audio16k = downmix_and_resample_int16_stereo_to_mono16k(pcm_bytes)
    if audio16k.size == 0:
        return audio16k
    return apply_light_denoise(audio16k)


def slice_audio_window(audio: np.ndarray, max_sec: float, sampling_rate: int = TARGET_RATE) -> np.ndarray:
    if audio.size == 0 or max_sec <= 0:
        return audio
    sample_len = max(1, int(max(1, int(sampling_rate)) * max_sec))
    return audio[:sample_len].copy()


def get_silero_vad_model():
    """Silero VAD 모델을 lazy-load하고 한 번만 재사용한다."""
    global silero_vad_model

    if silero_vad_model is not None:
        return silero_vad_model

    if torch is None or load_silero_vad is None or get_speech_timestamps is None:
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


def is_probably_silent_energy(audio: np.ndarray, sampling_rate: int = TARGET_RATE) -> bool:
    """경량 energy 기반 규칙으로 음성 시작이 없는 구간인지 판정한다."""
    if audio.size == 0:
        return True

    chunk_samples = max(1, int(max(1, int(sampling_rate)) * (VAD_CHUNK_MS / 1000.0)))
    required_streak = max(1, VAD_START_CONSECUTIVE)
    voiced_streak = 0

    for start in range(0, len(audio), chunk_samples):
        chunk = audio[start:start + chunk_samples]
        if _is_voiced_vad_chunk_energy(chunk):
            voiced_streak += 1
            if voiced_streak >= required_streak:
                return False
        else:
            voiced_streak = 0

    return True


def is_probably_silent_silero(audio: np.ndarray, sampling_rate: int = TARGET_RATE) -> bool:
    """Silero VAD 타임스탬프가 비어 있으면 무음으로 본다."""
    if torch is None:
        raise RuntimeError("torch is not available")
    if audio.size == 0:
        return True

    effective_rate = max(1, int(sampling_rate))
    vad_audio = np.asarray(audio, dtype=np.float32)
    if effective_rate != TARGET_RATE:
        vad_audio = downmix_and_resample_int16_stereo_to_mono16k((np.clip(vad_audio, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes())
        effective_rate = TARGET_RATE

    model = get_silero_vad_model()
    audio_tensor = torch.from_numpy(np.asarray(vad_audio, dtype=np.float32))
    speech_timestamps = get_speech_timestamps(
        audio_tensor,
        model,
        threshold=SILERO_VAD_THRESHOLD,
        sampling_rate=effective_rate,
        min_speech_duration_ms=SILERO_MIN_SPEECH_MS,
        min_silence_duration_ms=SILERO_MIN_SILENCE_MS,
        speech_pad_ms=SILERO_SPEECH_PAD_MS,
        return_seconds=False,
    )
    return len(speech_timestamps) == 0


def is_probably_silent(audio: np.ndarray, sampling_rate: int = TARGET_RATE) -> bool:
    """Silero 결과를 우선 쓰고, Silero 자체가 실패한 경우에만 energy fallback으로 간다."""
    global silero_vad_warned

    if audio.size == 0:
        return True

    if not VAD_ENABLED:
        return False

    if VAD_PROVIDER == "silero":
        try:
            return is_probably_silent_silero(audio, sampling_rate=sampling_rate)
        except Exception as e:
            if not silero_vad_warned:
                print(f"[VAD FALLBACK] Silero VAD 실패 -> energy 사용 | err={e}")
                silero_vad_warned = True
            return is_probably_silent_energy(audio, sampling_rate=sampling_rate)


    return is_probably_silent_energy(audio, sampling_rate=sampling_rate)
def compute_waveform_activity_stats(audio: np.ndarray, sampling_rate: int = TARGET_RATE) -> dict[str, float]:
    if audio.size == 0:
        return {
            "voiced_ms": 0.0,
            "longest_voiced_ms": 0.0,
            "body_rms": 0.0,
            "body_peak": 0.0,
        }

    effective_rate = max(1, int(sampling_rate))
    chunk_samples = max(1, int(effective_rate * 0.02))
    body_start = min(len(audio), int(effective_rate * 0.12))
    body = audio[body_start:] if body_start < len(audio) else audio
    body_rms = float(np.sqrt(np.mean(np.square(body)))) if body.size else 0.0
    body_peak = float(np.max(np.abs(body))) if body.size else 0.0
    rms_gate = max(VAD_RMS_THRESHOLD * 1.1, VOICE_WAVEFORM_BODY_RMS_MIN * 0.55)
    peak_gate = max(VAD_PEAK_THRESHOLD * 2.0, VOICE_WAVEFORM_BODY_PEAK_MIN * 0.65)

    voiced_samples = 0
    longest_samples = 0
    current_samples = 0
    for start in range(0, len(audio), chunk_samples):
        chunk = audio[start:start + chunk_samples]
        if chunk.size == 0:
            continue
        chunk_rms = float(np.sqrt(np.mean(np.square(chunk))))
        chunk_peak = float(np.max(np.abs(chunk)))
        voiced = chunk_rms >= rms_gate or chunk_peak >= peak_gate
        if voiced:
            voiced_samples += len(chunk)
            current_samples += len(chunk)
            longest_samples = max(longest_samples, current_samples)
        else:
            current_samples = 0

    return {
        "voiced_ms": (voiced_samples / float(effective_rate)) * 1000.0,
        "longest_voiced_ms": (longest_samples / float(effective_rate)) * 1000.0,
        "body_rms": body_rms,
        "body_peak": body_peak,
    }

