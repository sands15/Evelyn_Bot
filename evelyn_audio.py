import numpy as np
import torch

from evelyn_config import (
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
