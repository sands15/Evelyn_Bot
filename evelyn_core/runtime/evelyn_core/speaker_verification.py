from __future__ import annotations

import math
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

try:
    import torch
except Exception:
    torch = None

from .audio import resample_audio_float


EmbeddingFn = Callable[[np.ndarray, int], np.ndarray]


@dataclass(frozen=True)
class SpeakerVerificationConfig:
    enabled: bool
    enroll_dir: Path
    threshold: float = 0.45
    min_audio_sec: float = 0.45
    max_audio_sec: float = 3.0
    model: str = "speechbrain/spkrec-ecapa-voxceleb"
    cache_dir: Path | None = None
    device: str = "auto"


@dataclass(frozen=True)
class SpeakerVerificationResult:
    status: str
    score: float | None = None
    threshold: float | None = None
    sample_count: int = 0
    detail: str = ""

    @property
    def matched(self) -> bool | None:
        if self.status == "verified":
            return True
        if self.status == "rejected":
            return False
        return None

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "score": round(float(self.score), 4) if self.score is not None and math.isfinite(float(self.score)) else None,
            "threshold": round(float(self.threshold), 4) if self.threshold is not None else None,
            "sample_count": int(self.sample_count),
            "detail": self.detail,
        }


class SpeakerVerifier:
    def __init__(
        self,
        config: SpeakerVerificationConfig,
        *,
        embedding_fn: EmbeddingFn | None = None,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self._embedding_fn = embedding_fn
        self._log = log or (lambda _message: None)
        self._classifier = None
        self._enrollment_embedding: np.ndarray | None = None
        self._enrollment_sample_count = 0
        self._last_error = ""

    @property
    def enrollment_sample_count(self) -> int:
        return self._enrollment_sample_count

    def verify(self, audio: np.ndarray, *, sampling_rate: int) -> SpeakerVerificationResult:
        if not self.config.enabled:
            return SpeakerVerificationResult("disabled", threshold=self.config.threshold)

        prepared = self._prepare_audio(audio, sampling_rate=sampling_rate)
        duration_sec = prepared.size / 16000.0
        if duration_sec < max(0.0, float(self.config.min_audio_sec)):
            return SpeakerVerificationResult(
                "too_short",
                threshold=self.config.threshold,
                detail=f"duration_sec={duration_sec:.3f}",
            )

        enrollment = self._ensure_enrollment()
        if enrollment is None:
            status = "not_enrolled" if not self._last_error else "unavailable"
            return SpeakerVerificationResult(
                status,
                threshold=self.config.threshold,
                sample_count=self._enrollment_sample_count,
                detail=self._last_error,
            )

        try:
            probe = self._normalize_embedding(self._embed(prepared, 16000))
        except Exception as exc:
            self._last_error = f"speaker_verification_failed:{type(exc).__name__}"
            return SpeakerVerificationResult(
                "error",
                threshold=self.config.threshold,
                sample_count=self._enrollment_sample_count,
                detail=self._last_error,
            )

        score = float(np.dot(enrollment, probe))
        status = "verified" if score >= float(self.config.threshold) else "rejected"
        return SpeakerVerificationResult(
            status,
            score=score,
            threshold=self.config.threshold,
            sample_count=self._enrollment_sample_count,
        )

    def _prepare_audio(self, audio: np.ndarray, *, sampling_rate: int) -> np.ndarray:
        prepared = np.asarray(audio, dtype=np.float32).reshape(-1)
        if int(sampling_rate) != 16000:
            prepared = resample_audio_float(prepared, int(sampling_rate), 16000)
        max_samples = int(max(0.1, float(self.config.max_audio_sec)) * 16000)
        if prepared.size > max_samples:
            prepared = prepared[:max_samples]
        return np.clip(prepared, -1.0, 1.0).astype(np.float32, copy=False)

    def _ensure_enrollment(self) -> np.ndarray | None:
        if self._enrollment_embedding is not None:
            return self._enrollment_embedding

        self._last_error = ""
        paths = sorted(Path(self.config.enroll_dir).glob("*.wav"))
        if not paths:
            self._enrollment_sample_count = 0
            return None

        embeddings: list[np.ndarray] = []
        for path in paths:
            try:
                audio, rate = self._load_wav(path)
                prepared = self._prepare_audio(audio, sampling_rate=rate)
                if prepared.size / 16000.0 < max(0.0, float(self.config.min_audio_sec)):
                    continue
                embeddings.append(self._normalize_embedding(self._embed(prepared, 16000)))
            except Exception as exc:
                self._log(f"[SPEAKER VERIFY] enrollment_skip path={path} err={exc!r}")

        self._enrollment_sample_count = len(embeddings)
        if not embeddings:
            self._last_error = "no_valid_enrollment_wav"
            return None

        average = np.mean(np.stack(embeddings, axis=0), axis=0)
        self._enrollment_embedding = self._normalize_embedding(average)
        self._log(
            f"[SPEAKER VERIFY] enrolled samples={self._enrollment_sample_count} dir={self.config.enroll_dir}"
        )
        return self._enrollment_embedding

    def _embed(self, audio: np.ndarray, sampling_rate: int) -> np.ndarray:
        if self._embedding_fn is not None:
            return np.asarray(self._embedding_fn(audio, sampling_rate), dtype=np.float32).reshape(-1)
        if torch is None:
            raise RuntimeError("torch_not_available")

        classifier = self._load_speechbrain_classifier()
        wav = torch.as_tensor(audio, dtype=torch.float32).unsqueeze(0)
        with torch.no_grad():
            embedding = classifier.encode_batch(wav)
        return embedding.detach().cpu().numpy().reshape(-1).astype(np.float32)

    def _load_speechbrain_classifier(self):
        if self._classifier is not None:
            return self._classifier

        try:
            try:
                from speechbrain.inference.speaker import EncoderClassifier
            except Exception:
                from speechbrain.pretrained import EncoderClassifier
            from speechbrain.utils.fetching import LocalStrategy
        except Exception as exc:
            raise RuntimeError("speechbrain_not_available") from exc

        device = self._resolve_device()
        run_opts = {"device": device} if device else {}
        savedir = str(self.config.cache_dir) if self.config.cache_dir is not None else None
        kwargs = {"source": self.config.model, "run_opts": run_opts, "local_strategy": LocalStrategy.COPY}
        if savedir:
            kwargs["savedir"] = savedir
        self._classifier = EncoderClassifier.from_hparams(**kwargs)
        return self._classifier

    def _resolve_device(self) -> str | None:
        value = (self.config.device or "auto").strip().lower()
        if value in {"", "auto"}:
            return "cuda:0" if torch is not None and torch.cuda.is_available() else "cpu"
        if value in {"none", "default"}:
            return None
        return value

    @staticmethod
    def _normalize_embedding(embedding: np.ndarray) -> np.ndarray:
        vector = np.asarray(embedding, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vector))
        if not math.isfinite(norm) or norm <= 1e-8:
            raise ValueError("empty_speaker_embedding")
        return vector / norm

    @staticmethod
    def _load_wav(path: Path) -> tuple[np.ndarray, int]:
        with wave.open(str(path), "rb") as wf:
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            sample_rate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())

        if sample_width != 2:
            raise ValueError(f"expected 16-bit PCM wav: {path}")
        audio = np.frombuffer(frames, dtype=np.int16)
        if channels > 1:
            audio = audio.reshape(-1, channels).mean(axis=1)
        return (audio.astype(np.float32) / 32768.0).astype(np.float32), int(sample_rate)


def speaker_verification_applies(*, source: str | None, apply_to: str) -> bool:
    mode = (apply_to or "local_mic").strip().lower()
    if mode in {"0", "false", "off", "none", "disabled"}:
        return False
    if mode in {"1", "true", "on", "all", "always"}:
        return True
    normalized_source = (source or "").strip().lower()
    if mode in {"local", "local_mic"}:
        return normalized_source == "local_mic"
    if mode in {"discord", "discord_voice"}:
        return normalized_source in {"discord", "discord_voice"}
    return normalized_source == mode
