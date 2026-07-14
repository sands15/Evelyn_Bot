from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

import evelyn_core.stt_model_runtime as stt_model_runtime
from evelyn_core.stt_model_runtime import (
    SttModelRuntimeDeps,
    build_stt_model_runtime_deps,
    get_stt_model_from_runtime,
    normalize_stt_language_from_runtime,
    resolve_stt_torch_dtype_from_runtime,
)


class FakeQwen3ASRModel:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    @classmethod
    def from_pretrained(cls, *args: object, **kwargs: object) -> dict[str, object]:
        cls.calls.append((args, kwargs))
        return {"name": args[0], **kwargs}


def _build_deps(*, qwen_model=FakeQwen3ASRModel, import_error: Exception | None = None) -> SttModelRuntimeDeps:
    return SttModelRuntimeDeps(
        stt_compute_type="fp16",
        stt_model_name="qwen/mock",
        stt_language="ko",
        stt_force_language=True,
        get_env_token=lambda: "HF_TOKEN",
        torch_device=lambda: "cpu",
        stt_max_new_tokens=256,
        log=lambda _msg: None,
        qwen_asr_model=qwen_model,
        qwen_asr_import_error=import_error,
    )


@unittest.skipIf(stt_model_runtime.torch is None, "torch unavailable")
class SttModelRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        stt_model_runtime._stt_model = None
        stt_model_runtime._stt_backend = None
        stt_model_runtime._stt_processor = None

    def test_resolve_stt_torch_dtype_alias(self) -> None:
        self.assertEqual(resolve_stt_torch_dtype_from_runtime("fp16"), stt_model_runtime.torch.float16)
        self.assertEqual(resolve_stt_torch_dtype_from_runtime("bf16"), stt_model_runtime.torch.bfloat16)
        self.assertEqual(resolve_stt_torch_dtype_from_runtime("unknown"), stt_model_runtime.torch.float32)

    def test_normalize_stt_language(self) -> None:
        self.assertEqual(normalize_stt_language_from_runtime("ko-kr", default_language="en"), "Korean")
        self.assertEqual(normalize_stt_language_from_runtime(None, default_language="en"), "English")

    def test_get_stt_model_from_runtime_caches_model(self) -> None:
        FakeQwen3ASRModel.calls.clear()
        deps = _build_deps()
        backend, _, model = get_stt_model_from_runtime(deps=deps)
        backend2, _, model2 = get_stt_model_from_runtime(deps=deps)
        self.assertEqual(backend, "qwen_asr")
        self.assertEqual(backend2, "qwen_asr")
        self.assertIs(model, model2)
        self.assertEqual(len(FakeQwen3ASRModel.calls), 1)

    def test_get_stt_model_from_runtime_requires_model(self) -> None:
        deps = _build_deps(qwen_model=None, import_error=RuntimeError("missing"))
        with self.assertRaisesRegex(RuntimeError, "qwen-asr"):
            get_stt_model_from_runtime(deps=deps)

    def test_build_stt_model_runtime_deps(self) -> None:
        deps = build_stt_model_runtime_deps(
            stt_compute_type="fp16",
            stt_model_name="qwen/mock",
            stt_language="ko",
            stt_force_language=True,
            stt_max_new_tokens=333,
            get_env_token=lambda: "TOKEN",
            torch_device=lambda: "cpu",
            log=lambda _msg: None,
        )
        self.assertEqual(deps.stt_compute_type, "fp16")
        self.assertEqual(deps.stt_model_name, "qwen/mock")
        self.assertEqual(deps.stt_max_new_tokens, 333)


if __name__ == "__main__":
    unittest.main()
