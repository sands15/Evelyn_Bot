from __future__ import annotations

import importlib.util
import json
import sys
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_PACKAGE_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
STT_SERVICE = RUNTIME_PACKAGE_ROOT / "evelyn_core" / "stt_service.py"
if str(RUNTIME_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_PACKAGE_ROOT))

from evelyn_core.stt_client import (  # noqa: E402
    cancel_stt_stream_via_service,
    finish_stt_stream_via_service,
    push_stt_stream_chunk_via_service,
    start_stt_stream_via_service,
)


class _HttpException(Exception):
    def __init__(self, *, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class _App:
    def __init__(self, **_kwargs):
        pass

    def _route(self, *_args, **_kwargs):
        return lambda function: function

    get = post = delete = on_event = _route


class _BaseModel:
    pass


class _RuntimeSettings(dict):
    def public_summary(self):
        return {}


class _RuntimeErrorCounter:
    def record(self, *_args):
        return None

    def snapshot(self):
        return {}


def _load_service_module():
    package_name = "_evelyn_stt_stream_test"
    package = types.ModuleType(package_name)
    package.__path__ = []
    settings = _RuntimeSettings(
        STT_MODEL_NAME="Qwen/Qwen3-ASR-1.7B",
        STT_LANGUAGE="ko",
        STT_FORCE_LANGUAGE=True,
        STT_COMPUTE_TYPE="float16",
        STT_HOST="127.0.0.1",
        STT_PORT=8892,
        STT_LOAD_ON_START=False,
        STT_MAX_AUDIO_SEC=30.0,
        HF_TOKEN="",
    )
    sibling_modules = {
        f"{package_name}.audio": SimpleNamespace(resample_audio_float=lambda audio, *_args: audio),
        f"{package_name}.runtime_config_schema": SimpleNamespace(
            STT_SERVICE_SETTINGS=(),
            load_runtime_settings=lambda *_args: settings,
        ),
        f"{package_name}.runtime_error_observability": SimpleNamespace(
            RuntimeErrorCounter=_RuntimeErrorCounter,
        ),
        f"{package_name}.text": SimpleNamespace(clean_text=lambda value: " ".join(str(value or "").split())),
    }
    torch = SimpleNamespace(
        dtype=object,
        float16="float16",
        bfloat16="bfloat16",
        float32="float32",
        cuda=SimpleNamespace(is_available=lambda: False),
    )
    fastapi = SimpleNamespace(
        Body=lambda default=..., **_kwargs: default,
        FastAPI=_App,
        Header=lambda default=..., **_kwargs: default,
        HTTPException=_HttpException,
    )
    pydantic = SimpleNamespace(
        BaseModel=_BaseModel,
        ConfigDict=lambda **kwargs: kwargs,
        Field=lambda *, default_factory, **_kwargs: default_factory(),
    )
    qwen_asr = SimpleNamespace(Qwen3ASRModel=object)
    uvicorn = SimpleNamespace(run=lambda *_args, **_kwargs: None)

    module_name = f"{package_name}.stt_service"
    spec = importlib.util.spec_from_file_location(module_name, STT_SERVICE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {
            package_name: package,
            module_name: module,
            **sibling_modules,
            "fastapi": fastapi,
            "pydantic": pydantic,
            "qwen_asr": qwen_asr,
            "torch": torch,
            "uvicorn": uvicorn,
        },
    ):
        spec.loader.exec_module(module)
    return module


class _FakeStreamingModel:
    def __init__(self):
        self.streaming_calls = 0
        self.finish_calls = 0

    def init_streaming_state(self, **_kwargs):
        return SimpleNamespace(text="")

    def streaming_transcribe(self, audio, state):
        self.streaming_calls += 1
        if audio.dtype != np.dtype("int16"):
            raise AssertionError("stream input was not PCM16")
        state.text = "이블린 부분"
        return state

    def finish_streaming_transcribe(self, state):
        self.finish_calls += 1
        state.text = "이블린 완료"
        return state


class SttStreamServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = _load_service_module()
        self.model = _FakeStreamingModel()
        self.service.get_model = lambda: self.model
        self.service._renew_stream_locked = lambda _stream_id, session: setattr(
            session,
            "expires_at",
            time.monotonic() + self.service.STREAM_TTL_SEC,
        )

    def start_stream(self):
        return self.service.start_stream(
            SimpleNamespace(
                sampling_rate=16000,
                language="Korean",
                decoder_profile="realtime-ko",
                context_terms=[],
            )
        )

    def test_sequence_fence_and_finish_revision(self):
        started = self.start_stream()
        stream_id = started["streamId"]
        partial = self.service.push_stream_chunk(
            stream_id,
            np.array([0, 32767], dtype="<i2").tobytes(),
            0,
        )
        self.assertEqual(partial, {"revision": 1, "text": "이블린 부분", "isFinal": False})

        with self.assertRaises(_HttpException) as raised:
            self.service.push_stream_chunk(stream_id, b"\x00\x00", 0)
        self.assertEqual((raised.exception.status_code, raised.exception.detail), (409, "stream_sequence_mismatch"))

        final = self.service.finish_stream(stream_id)
        self.assertEqual(final, {"revision": 2, "text": "이블린 완료", "isFinal": True})
        self.assertNotIn(stream_id, self.service._streams)

    def test_audio_cap_and_expiry_release_session(self):
        self.service.STT_MAX_AUDIO_SEC = 1.0
        stream_id = self.start_stream()["streamId"]
        with self.assertRaises(_HttpException) as raised:
            self.service.push_stream_chunk(stream_id, bytes((16000 + 1) * 2), 0)
        self.assertEqual(raised.exception.status_code, 413)
        self.assertNotIn(stream_id, self.service._streams)

        stream_id = self.start_stream()["streamId"]
        self.service._streams[stream_id].expires_at = 1.0
        self.service._purge_expired_streams(now=2.0)
        self.assertNotIn(stream_id, self.service._streams)

    def test_cancel_releases_session(self):
        stream_id = self.start_stream()["streamId"]
        self.assertEqual(self.service.cancel_stream(stream_id), {"cancelled": True})
        self.assertNotIn(stream_id, self.service._streams)
        with self.assertRaises(_HttpException) as raised:
            self.service.cancel_stream(stream_id)
        self.assertEqual(raised.exception.status_code, 404)

    def test_cancelled_chunk_waiter_does_not_run_inference(self):
        stream_id = self.start_stream()["streamId"]
        service = self.service

        class CancelOnEnter:
            def __enter__(self):
                service.cancel_stream(stream_id)

            def __exit__(self, *_args):
                return False

        service._inference_lock = CancelOnEnter()
        with self.assertRaises(_HttpException) as raised:
            service.push_stream_chunk(stream_id, b"\x00\x00", 0)

        self.assertEqual((raised.exception.status_code, raised.exception.detail), (410, "stream_cancelled"))
        self.assertEqual(self.model.streaming_calls, 0)

    def test_finish_keeps_capacity_until_inference_completes(self):
        self.service.STREAM_MAX_SESSIONS = 1
        stream_id = self.start_stream()["streamId"]
        service = self.service
        test = self

        class InspectOnEnter:
            def __enter__(self):
                test.assertIn(stream_id, service._streams)
                with test.assertRaises(_HttpException) as raised:
                    test.start_stream()
                test.assertEqual(raised.exception.status_code, 503)

            def __exit__(self, *_args):
                return False

        service._inference_lock = InspectOnEnter()
        final = service.finish_stream(stream_id)

        self.assertTrue(final["isFinal"])
        self.assertEqual(self.model.finish_calls, 1)
        self.assertNotIn(stream_id, service._streams)

    def test_cancelled_finish_waiter_does_not_run_inference(self):
        stream_id = self.start_stream()["streamId"]
        service = self.service

        class CancelOnEnter:
            def __enter__(self):
                service.cancel_stream(stream_id)

            def __exit__(self, *_args):
                return False

        service._inference_lock = CancelOnEnter()
        with self.assertRaises(_HttpException) as raised:
            service.finish_stream(stream_id)

        self.assertEqual((raised.exception.status_code, raised.exception.detail), (410, "stream_cancelled"))
        self.assertEqual(self.model.finish_calls, 0)


class SttStreamClientTests(unittest.TestCase):
    def test_client_sends_json_start_raw_pcm_sequence_and_terminal_calls(self):
        requests = []
        responses = iter(
            (
                b'{"streamId":"stream-token","nextSequence":0}',
                b'{"revision":1,"text":"partial","isFinal":false}',
                b'{"revision":2,"text":"final","isFinal":true}',
                b'{"cancelled":true}',
            )
        )

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return next(responses)

        def open_request(req, **_kwargs):
            requests.append(req)
            return Response()

        with patch("evelyn_core.stt_client.request.urlopen", side_effect=open_request):
            started = start_stt_stream_via_service(service_url="http://stt/", timeout_sec=3.0)
            partial = push_stt_stream_chunk_via_service(
                np.array([1, -2], dtype=np.int16),
                service_url="http://stt",
                stream_id=started["streamId"],
                sequence=0,
                timeout_sec=3.0,
            )
            final = finish_stt_stream_via_service(
                service_url="http://stt",
                stream_id=started["streamId"],
                timeout_sec=3.0,
            )
            cancelled = cancel_stt_stream_via_service(
                service_url="http://stt",
                stream_id=started["streamId"],
                timeout_sec=3.0,
            )

        start_payload = json.loads(requests[0].data.decode("utf-8"))
        self.assertEqual(start_payload["context_terms"], [])
        self.assertEqual(requests[1].data, np.array([1, -2], dtype="<i2").tobytes())
        self.assertEqual(dict(requests[1].header_items())["X-audio-sequence"], "0")
        self.assertEqual([req.method for req in requests], ["POST", "POST", "POST", "DELETE"])
        self.assertFalse(partial["isFinal"])
        self.assertTrue(final["isFinal"])
        self.assertTrue(cancelled["cancelled"])


if __name__ == "__main__":
    unittest.main()
