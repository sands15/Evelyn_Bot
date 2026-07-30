from __future__ import annotations

import base64
import gc
import io
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from PIL import Image
from huggingface_hub import hf_hub_download
from transformers import AutoModelForCausalLM, AutoModelForImageTextToText, AutoProcessor, PreTrainedTokenizerFast

from .runtime_config_schema import VISION_SERVICE_SETTINGS, load_runtime_settings
from .runtime_error_observability import RuntimeErrorCounter
from .vision_ocr_tiles import build_screen_ocr_tiles, merge_screen_ocr_texts
from .vision_quality import build_vision_quality


_VISION_CONFIG = load_runtime_settings("vision", VISION_SERVICE_SETTINGS)
_RUNTIME_ERRORS = RuntimeErrorCounter()
SMOL_MODEL_ID = str(_VISION_CONFIG["VISION_SMOL_MODEL"])
OCR_MODEL_ID = str(_VISION_CONFIG["VISION_OCR_MODEL"])
_configured_device = str(_VISION_CONFIG["VISION_DEVICE"])
VISION_DEVICE = (
    "cuda:0" if torch.cuda.is_available() else "cpu"
) if _configured_device == "auto" else _configured_device
VISION_DTYPE = str(_VISION_CONFIG["VISION_DTYPE"]).lower()
VISION_OCR_DTYPE = str(_VISION_CONFIG["VISION_OCR_DTYPE"]).lower()
VISION_MAX_NEW_TOKENS = int(_VISION_CONFIG["VISION_MAX_NEW_TOKENS"])
VISION_TRUST_REMOTE_CODE = bool(_VISION_CONFIG["VISION_TRUST_REMOTE_CODE"])
VISION_LOAD_SMOL = bool(_VISION_CONFIG["VISION_LOAD_SMOL"])
VISION_LOAD_OCR = bool(_VISION_CONFIG["VISION_LOAD_OCR"])
VISION_OCR_LAZY_LOAD = bool(_VISION_CONFIG["VISION_OCR_LAZY_LOAD"])
VISION_OCR_IDLE_UNLOAD_SEC = float(_VISION_CONFIG["VISION_OCR_IDLE_UNLOAD_SEC"])
VISION_OCR_UNLOAD_AFTER_REQUEST = bool(
    _VISION_CONFIG["VISION_OCR_UNLOAD_AFTER_REQUEST"]
)
VISION_OCR_EMPTY_CACHE_ON_UNLOAD = bool(
    _VISION_CONFIG["VISION_OCR_EMPTY_CACHE_ON_UNLOAD"]
)
VISION_OCR_COMPILE = bool(_VISION_CONFIG["VISION_OCR_COMPILE"])
EVELYN_HOST_PROJECT_ROOT = str(_VISION_CONFIG["EVELYN_HOST_PROJECT_ROOT"] or "")
EVELYN_CONTAINER_PROJECT_ROOT = str(
    _VISION_CONFIG["EVELYN_CONTAINER_PROJECT_ROOT"] or ""
)
VISION_HOST = str(_VISION_CONFIG["VISION_HOST"])
VISION_PORT = int(_VISION_CONFIG["VISION_PORT"])
WINDOWS_DRIVE_RE = re.compile(r"^([A-Za-z]):[\\/](.*)$")


def resolve_dtype() -> torch.dtype:
    if VISION_DEVICE == "cpu":
        return torch.float32
    if VISION_DTYPE in {"bf16", "bfloat16"}:
        return torch.bfloat16
    if VISION_DTYPE in {"fp16", "float16", "half"}:
        return torch.float16
    return torch.float32


def resolve_ocr_dtype() -> torch.dtype | str:
    if VISION_OCR_DTYPE == "auto":
        return "auto"
    if VISION_OCR_DTYPE in {"bf16", "bfloat16"}:
        return torch.bfloat16
    if VISION_OCR_DTYPE in {"fp16", "float16", "half"}:
        return torch.float16
    return torch.float32


def gpu_snapshot() -> dict[str, Any]:
    if not torch.cuda.is_available():
        return {"cuda": False}
    device = torch.device(VISION_DEVICE)
    index = device.index if device.index is not None else torch.cuda.current_device()
    props = torch.cuda.get_device_properties(index)
    free, total = torch.cuda.mem_get_info(index)
    return {
        "cuda": True,
        "device": str(device),
        "name": props.name,
        "total_mb": round(total / 1024 / 1024),
        "free_mb": round(free / 1024 / 1024),
        "used_mb": round((total - free) / 1024 / 1024),
        "allocated_mb": round(torch.cuda.memory_allocated(index) / 1024 / 1024),
        "reserved_mb": round(torch.cuda.memory_reserved(index) / 1024 / 1024),
    }


def normalize_image_path(image_path: str) -> Path:
    mapped = map_host_project_path(image_path)
    if mapped is not None:
        return mapped
    match = WINDOWS_DRIVE_RE.match(image_path)
    if match and os.name != "nt":
        drive = match.group(1).lower()
        rest = match.group(2).replace("\\", "/")
        return Path(f"/mnt/{drive}/{rest}")
    return Path(image_path).expanduser()


def normalize_path_text(value: str) -> str:
    return value.replace("\\", "/").rstrip("/")


def map_host_project_path(image_path: str) -> Path | None:
    if not EVELYN_HOST_PROJECT_ROOT or not EVELYN_CONTAINER_PROJECT_ROOT:
        return None
    raw = normalize_path_text(image_path)
    host_root = normalize_path_text(EVELYN_HOST_PROJECT_ROOT)
    raw_lower = raw.lower()
    host_lower = host_root.lower()
    if raw_lower == host_lower:
        relative = ""
    elif raw_lower.startswith(host_lower + "/"):
        relative = raw[len(host_root) + 1 :]
    else:
        return None
    return Path(EVELYN_CONTAINER_PROJECT_ROOT, *[part for part in relative.split("/") if part])


def load_image(*, image_path: str | None = None, image_base64: str | None = None) -> Image.Image:
    if image_path:
        path = normalize_image_path(image_path)
        if not path.exists():
            raise HTTPException(status_code=400, detail=f"image_path not found: {image_path}")
        return Image.open(path).convert("RGB")
    if image_base64:
        try:
            raw = base64.b64decode(image_base64, validate=True)
            return Image.open(io.BytesIO(raw)).convert("RGB")
        except Exception as exc:  # noqa: BLE001 - convert image errors into API detail.
            raise HTTPException(status_code=400, detail=f"invalid image_base64: {exc}") from exc
    raise HTTPException(status_code=400, detail="provide image_path or image_base64")


def _falcon_ocr_file(filename: str, *, revision: str | None) -> Path:
    download_kwargs: dict[str, Any] = {}
    if revision:
        download_kwargs["revision"] = revision
    try:
        return Path(
            hf_hub_download(
                OCR_MODEL_ID,
                filename,
                local_files_only=True,
                **download_kwargs,
            )
        )
    except Exception:
        return Path(
            hf_hub_download(
                OCR_MODEL_ID,
                filename,
                **download_kwargs,
            )
        )


def load_falcon_ocr_tokenizer(
    *,
    revision: str | None = None,
) -> PreTrainedTokenizerFast:
    """Falcon-OCR ships tokenizer_class=TokenizersBackend, which AutoTokenizer cannot import."""
    tokenizer_config_path = _falcon_ocr_file(
        "tokenizer_config.json",
        revision=revision,
    )
    tokenizer_file = _falcon_ocr_file(
        "tokenizer.json",
        revision=revision,
    )
    tokenizer_config = json.loads(
        tokenizer_config_path.read_text(encoding="utf-8")
    )
    tokenizer_kwargs: dict[str, Any] = {}
    for key, value in tokenizer_config.items():
        if key.endswith("_token") and isinstance(value, str):
            tokenizer_kwargs[key] = value
    model_tokens = tokenizer_config.get("model_specific_special_tokens", {})
    if isinstance(model_tokens, dict):
        tokenizer_kwargs["additional_special_tokens"] = list(dict.fromkeys(model_tokens.values()))
    tokenizer_kwargs.setdefault("pad_token", "<|pad|>")
    if "eos_token" in tokenizer_config:
        tokenizer_kwargs["eos_token"] = tokenizer_config["eos_token"]

    tokenizer = PreTrainedTokenizerFast(
        tokenizer_file=str(tokenizer_file),
        **tokenizer_kwargs,
    )
    for token_name, token in tokenizer.special_tokens_map.items():
        if isinstance(token, str):
            setattr(tokenizer, token_name, token)
            setattr(tokenizer, token_name + "_id", tokenizer.convert_tokens_to_ids(token))
    return tokenizer


class VisionRequest(BaseModel):
    image_path: str | None = None
    image_base64: str | None = None
    prompt: str = "Describe this image in Korean. Be concise and mention visible UI text only if it is clear."
    max_new_tokens: int | None = None


class OcrRequest(BaseModel):
    image_path: str | None = None
    image_base64: str | None = None
    category: str = "plain"
    max_new_tokens: int | None = None


class AnalyzeRequest(BaseModel):
    image_path: str | None = None
    image_base64: str | None = None
    prompt: str = "Summarize the visible scene in Korean for Evelyn."
    run_ocr: bool = True
    ocr_category: str = "plain"
    max_new_tokens: int | None = None


app = FastAPI(title="Evelyn Vision Service", version="0.1")
_dtype = resolve_dtype()
_smol_processor: Any | None = None
_smol_model: Any | None = None
_ocr_model: Any | None = None
_ocr_loaded_at: float | None = None
_ocr_last_used_at: float | None = None
_ocr_lock = threading.RLock()
_ocr_idle_reaper_started = False


def load_falcon_ocr_model() -> Any:
    model = AutoModelForCausalLM.from_pretrained(
        OCR_MODEL_ID,
        trust_remote_code=True,
        torch_dtype=resolve_ocr_dtype(),
        device_map="auto" if VISION_DEVICE.startswith("cuda") else None,
    )
    model._tokenizer = load_falcon_ocr_tokenizer(
        revision=getattr(model.config, "_commit_hash", None),
    )
    model.eval()
    return model


def unload_ocr(reason: str = "manual") -> bool:
    global _ocr_model, _ocr_loaded_at
    with _ocr_lock:
        if _ocr_model is None:
            return False
        print(f"[VISION OCR] unload reason={reason} gpu_before={gpu_snapshot()}", flush=True)
        model = _ocr_model
        _ocr_model = None
        _ocr_loaded_at = None
        del model
        gc.collect()
        if VISION_OCR_EMPTY_CACHE_ON_UNLOAD and torch.cuda.is_available():
            torch.cuda.empty_cache()
        print(f"[VISION OCR] unloaded gpu_after={gpu_snapshot()}", flush=True)
        return True


def maybe_unload_idle_ocr(now: float | None = None) -> bool:
    if VISION_OCR_IDLE_UNLOAD_SEC <= 0:
        return False
    now = time.time() if now is None else now
    with _ocr_lock:
        if _ocr_model is None:
            return False
        reference = _ocr_last_used_at or _ocr_loaded_at or now
        if (now - reference) < VISION_OCR_IDLE_UNLOAD_SEC:
            return False
    return unload_ocr("idle")


def ensure_ocr_loaded() -> Any:
    global _ocr_model, _ocr_loaded_at, _ocr_last_used_at
    with _ocr_lock:
        if _ocr_model is not None:
            return _ocr_model
        if not VISION_OCR_LAZY_LOAD:
            raise HTTPException(status_code=503, detail="Falcon-OCR is not loaded")
        print(f"[VISION OCR] lazy load start model={OCR_MODEL_ID} gpu={gpu_snapshot()}", flush=True)
        try:
            _ocr_model = load_falcon_ocr_model()
        except Exception as exc:
            _RUNTIME_ERRORS.record("vision_ocr_load_failed", exc)
            raise
        _ocr_loaded_at = time.time()
        _ocr_last_used_at = None
        print(f"[VISION OCR] lazy load done gpu={gpu_snapshot()}", flush=True)
        return _ocr_model


def mark_ocr_used() -> None:
    global _ocr_last_used_at
    _ocr_last_used_at = time.time()


def cleanup_ocr_after_request() -> None:
    if VISION_OCR_UNLOAD_AFTER_REQUEST:
        unload_ocr("after_request")


def start_ocr_idle_reaper() -> None:
    global _ocr_idle_reaper_started
    if _ocr_idle_reaper_started or VISION_OCR_IDLE_UNLOAD_SEC <= 0:
        return
    _ocr_idle_reaper_started = True
    interval = max(5.0, min(30.0, VISION_OCR_IDLE_UNLOAD_SEC / 4.0))

    def run() -> None:
        while True:
            time.sleep(interval)
            try:
                maybe_unload_idle_ocr()
            except Exception as exc:
                _RUNTIME_ERRORS.record("vision_reaper_failed", exc)

    threading.Thread(target=run, name="vision-ocr-idle-reaper", daemon=True).start()


def ocr_status() -> dict[str, Any]:
    return {
        "id": OCR_MODEL_ID,
        "loaded": _ocr_model is not None,
        "dtype": VISION_OCR_DTYPE,
        "lazyLoad": VISION_OCR_LAZY_LOAD,
        "loadedAt": _ocr_loaded_at,
        "lastUsedAt": _ocr_last_used_at,
        "idleUnloadSec": VISION_OCR_IDLE_UNLOAD_SEC,
        "unloadAfterRequest": VISION_OCR_UNLOAD_AFTER_REQUEST,
    }


def load_models() -> None:
    global _smol_processor, _smol_model, _ocr_model, _ocr_loaded_at
    print(f"[VISION LOAD] device={VISION_DEVICE} dtype={_dtype} gpu={gpu_snapshot()}", flush=True)
    if VISION_LOAD_SMOL:
        print(f"[VISION LOAD] SmolVLM2 start model={SMOL_MODEL_ID}", flush=True)
        _smol_processor = AutoProcessor.from_pretrained(SMOL_MODEL_ID, trust_remote_code=VISION_TRUST_REMOTE_CODE)
        _smol_model = AutoModelForImageTextToText.from_pretrained(
            SMOL_MODEL_ID,
            torch_dtype=_dtype,
            trust_remote_code=VISION_TRUST_REMOTE_CODE,
        ).to(VISION_DEVICE)
        _smol_model.eval()
        print(f"[VISION LOAD] SmolVLM2 done gpu={gpu_snapshot()}", flush=True)
    if VISION_LOAD_OCR:
        print(f"[VISION LOAD] Falcon-OCR start model={OCR_MODEL_ID}", flush=True)
        _ocr_model = load_falcon_ocr_model()
        _ocr_loaded_at = time.time()
        print(f"[VISION LOAD] Falcon-OCR done gpu={gpu_snapshot()}", flush=True)


@app.on_event("startup")
def startup() -> None:
    try:
        load_models()
    except Exception as exc:
        _RUNTIME_ERRORS.record("vision_model_load_failed", exc)
        raise
    start_ocr_idle_reaper()


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "models": {
            "smol": {"id": SMOL_MODEL_ID, "loaded": _smol_model is not None, "dtype": VISION_DTYPE},
            "ocr": ocr_status(),
        },
        "gpu": gpu_snapshot(),
        "configuration": _VISION_CONFIG.public_summary(),
        **_RUNTIME_ERRORS.snapshot(),
    }


@app.post("/v1/vision/ocr/unload")
def unload_ocr_endpoint() -> dict[str, Any]:
    unloaded = unload_ocr("api")
    return {
        "ok": True,
        "unloaded": unloaded,
        "models": {"ocr": ocr_status()},
        "gpu": gpu_snapshot(),
    }


@app.post("/v1/vision/describe")
def describe(request: VisionRequest) -> dict[str, Any]:
    if _smol_model is None or _smol_processor is None:
        raise HTTPException(status_code=503, detail="SmolVLM2 is not loaded")
    image = load_image(image_path=request.image_path, image_base64=request.image_base64)
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": image},
                {"type": "text", "text": request.prompt},
            ],
        }
    ]
    try:
        inputs = _smol_processor.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        ).to(_smol_model.device, dtype=_dtype)
        with torch.inference_mode():
            output_ids = _smol_model.generate(
                **inputs,
                do_sample=False,
                max_new_tokens=request.max_new_tokens or VISION_MAX_NEW_TOKENS,
            )
        generated = output_ids[0][inputs["input_ids"].shape[-1] :]
        text = _smol_processor.decode(generated, skip_special_tokens=True).strip()
    except Exception as exc:
        _RUNTIME_ERRORS.record("vision_describe_failed", exc)
        raise
    return {"text": text, "gpu": gpu_snapshot()}


@app.post("/v1/vision/ocr")
def ocr(request: OcrRequest) -> dict[str, Any]:
    ocr_model = ensure_ocr_loaded()
    image = load_image(image_path=request.image_path, image_base64=request.image_base64)
    kwargs: dict[str, Any] = {}
    if request.max_new_tokens:
        kwargs["max_new_tokens"] = request.max_new_tokens
    kwargs["compile"] = VISION_OCR_COMPILE
    try:
        with _ocr_lock, torch.inference_mode():
            texts = ocr_model.generate(image, category=request.category, **kwargs)
            mark_ocr_used()
    except Exception as exc:  # noqa: BLE001 - third-party model code should surface through the API.
        _RUNTIME_ERRORS.record("vision_ocr_generation_failed", exc)
        ocr_model = None
        cleanup_ocr_after_request()
        raise HTTPException(status_code=500, detail="vision_ocr_generation_failed") from exc
    text = texts[0] if texts else ""
    ocr_model = None
    cleanup_ocr_after_request()
    return {"text": str(text).strip(), "category": request.category, "ocr": ocr_status(), "gpu": gpu_snapshot()}


@app.post("/v1/vision/analyze")
def analyze(request: AnalyzeRequest) -> dict[str, Any]:
    image = load_image(image_path=request.image_path, image_base64=request.image_base64)
    result: dict[str, Any] = {}
    if _smol_model is not None and _smol_processor is not None:
        try:
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": image},
                        {"type": "text", "text": request.prompt},
                    ],
                }
            ]
            inputs = _smol_processor.apply_chat_template(
                messages,
                add_generation_prompt=True,
                tokenize=True,
                return_dict=True,
                return_tensors="pt",
            ).to(_smol_model.device, dtype=_dtype)
            with torch.inference_mode():
                output_ids = _smol_model.generate(
                    **inputs,
                    do_sample=False,
                    max_new_tokens=request.max_new_tokens or VISION_MAX_NEW_TOKENS,
                )
            generated = output_ids[0][inputs["input_ids"].shape[-1] :]
            result["scene"] = _smol_processor.decode(
                generated,
                skip_special_tokens=True,
            ).strip()
        except Exception as exc:
            _RUNTIME_ERRORS.record("vision_analyze_failed", exc)
            raise
    if request.run_ocr:
        ocr_model = None
        try:
            ocr_model = ensure_ocr_loaded()
            ocr_tiles = build_screen_ocr_tiles(image)
            with _ocr_lock, torch.inference_mode():
                texts = ocr_model.generate(
                    ocr_tiles,
                    category=[request.ocr_category] * len(ocr_tiles),
                    max_new_tokens=request.max_new_tokens or VISION_MAX_NEW_TOKENS,
                    compile=VISION_OCR_COMPILE,
                )
                mark_ocr_used()
            result["ocr"] = merge_screen_ocr_texts(texts)
            result["ocr_region_count"] = len(ocr_tiles)
            result["ocr_status"] = ocr_status()
        except Exception as exc:  # noqa: BLE001 - keep scene output available when OCR fails.
            _RUNTIME_ERRORS.record("vision_ocr_generation_failed", exc)
            result["ocr_error"] = "vision_ocr_generation_failed"
        finally:
            ocr_model = None
            cleanup_ocr_after_request()
            result["ocr_status"] = ocr_status()
    result["quality"] = build_vision_quality(result)
    result["gpu"] = gpu_snapshot()
    return result


def main() -> None:
    uvicorn.run(app, host=VISION_HOST, port=VISION_PORT)


if __name__ == "__main__":
    main()
