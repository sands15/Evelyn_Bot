from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

try:
    import torch
except Exception:  # noqa: BLE001 - optional runtime in Python 3.14 limited environments.
    torch = None

try:
    from qwen_asr import Qwen3ASRModel
except Exception as exc:  # noqa: BLE001
    Qwen3ASRModel = None
    QWEN_ASR_IMPORT_ERROR = exc
else:
    QWEN_ASR_IMPORT_ERROR = None


_stt_model: Any | None = None
_stt_processor: Any | None = None
_stt_backend: str | None = None


@dataclass(frozen=True)
class SttModelRuntimeDeps:
    stt_compute_type: str
    stt_model_name: str
    stt_language: str
    stt_force_language: bool
    get_env_token: Callable[[], str | None]
    torch_device: Callable[[], str]
    stt_max_new_tokens: int
    log: Callable[[str], None]
    qwen_asr_model: Any | None = Qwen3ASRModel
    qwen_asr_import_error: Exception | None = QWEN_ASR_IMPORT_ERROR


def build_stt_model_runtime_deps(
    *,
    stt_compute_type: str,
    stt_model_name: str,
    stt_language: str,
    stt_force_language: bool,
    stt_max_new_tokens: int,
    get_env_token: Callable[[], str | None],
    torch_device: Callable[[], str],
    log: Callable[[str], None],
) -> SttModelRuntimeDeps:
    return SttModelRuntimeDeps(
        stt_compute_type=stt_compute_type,
        stt_model_name=stt_model_name,
        stt_language=stt_language,
        stt_force_language=stt_force_language,
        get_env_token=get_env_token,
        torch_device=torch_device,
        stt_max_new_tokens=stt_max_new_tokens,
        log=log,
    )


def resolve_stt_torch_dtype_from_runtime(stt_compute_type: str) -> torch.dtype:
    value = str(stt_compute_type).strip().lower()
    mapping = {
        "float16": torch.float16 if torch else "float16",
        "fp16": torch.float16 if torch else "float16",
        "half": torch.float16 if torch else "float16",
        "bfloat16": torch.bfloat16 if torch else "bfloat16",
        "bf16": torch.bfloat16 if torch else "bfloat16",
        "float32": torch.float32 if torch else "float32",
        "fp32": torch.float32 if torch else "float32",
        "float": torch.float32 if torch else "float32",
    }
    return mapping.get(value, torch.float32 if torch else "float32")


def normalize_stt_language_from_runtime(language: str | None = None, *, default_language: str) -> str | None:
    value = str(language if language is not None else default_language).strip()
    if not value:
        return None

    lowered = value.lower()
    aliases = {
        "korean": "Korean",
        "kor": "Korean",
        "kr": "Korean",
        "ko": "Korean",
        "ko-kr": "Korean",
        "ko_kr": "Korean",
        "english": "English",
        "en": "English",
        "chinese": "Chinese",
        "zh": "Chinese",
        "japanese": "Japanese",
        "ja": "Japanese",
    }
    return aliases.get(lowered, value)


def get_stt_model_from_runtime(*, deps: SttModelRuntimeDeps) -> tuple[str, Any, Any]:
    global _stt_backend, _stt_model, _stt_processor

    if torch is None:
        raise RuntimeError("torch runtime is required for STT model loading.")

    if _stt_backend == "qwen_asr" and _stt_model is not None:
        return _stt_backend, _stt_processor, _stt_model

    if deps.qwen_asr_model is None:
        detail = f" ({type(deps.qwen_asr_import_error).__name__}: {deps.qwen_asr_import_error})" if deps.qwen_asr_import_error else ""
        raise RuntimeError(f"qwen-asr를 불러오지 못했습니다{detail}. STT 의존성을 확인한 뒤 다시 실행하세요.")

    device = deps.torch_device()
    token = deps.get_env_token()
    torch_dtype = resolve_stt_torch_dtype_from_runtime(deps.stt_compute_type)

    deps.log(f"[STT LOAD] start model={deps.stt_model_name} device={device} dtype={torch_dtype}")

    _stt_backend = "qwen_asr"
    _stt_processor = None
    load_kwargs: dict[str, Any] = {
        "dtype": torch_dtype,
        "device_map": device,
        "max_inference_batch_size": 1,
        "max_new_tokens": deps.stt_max_new_tokens,
    }
    if token:
        load_kwargs["token"] = token

    _stt_model = deps.qwen_asr_model.from_pretrained(deps.stt_model_name, **load_kwargs)
    deps.log("[STT LOAD] done backend=Qwen3-ASR")
    return _stt_backend, _stt_processor, _stt_model


__all__ = [
    "SttModelRuntimeDeps",
    "build_stt_model_runtime_deps",
    "resolve_stt_torch_dtype_from_runtime",
    "normalize_stt_language_from_runtime",
    "get_stt_model_from_runtime",
]
