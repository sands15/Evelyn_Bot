from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from urllib.request import urlopen


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.runtime_health import collect_runtime_health  # noqa: E402
from evelyn_core.runtime_services import HealthProbeSpec, ServiceManifest, ServiceSpec  # noqa: E402
from evelyn_core.session_continuity import SessionContinuityCheckpoint  # noqa: E402
from evelyn_core.continuity_authenticity import (  # noqa: E402
    CONTINUITY_HEAD_SCHEMA_V2,
    ContinuityAuthenticity,
)
from evelyn_core.session_memory_state import SessionStateStore  # noqa: E402


def unused_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_port(port: int, *, timeout_sec: float = 5.0) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.1)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.05)
    return False


def fetch_json(url: str, *, timeout_sec: float = 1.5) -> dict[str, object]:
    with urlopen(url, timeout=timeout_sec) as response:
        payload = response.read().decode("utf-8")
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise AssertionError(f"expected JSON object from {url}")
    return data


def manifest_for_ports(*, control_page_port: int, bot_api_port: int) -> ServiceManifest:
    return ServiceManifest(
        schema_version="test",
        runtime_name="integration-test",
        loaded_at=time.time(),
        path=Path("integration-test.json"),
        services=(
            ServiceSpec(
                id="control_page",
                label="Control-Page",
                kind="control",
                required=True,
                host="127.0.0.1",
                port=control_page_port,
                checks=(
                    HealthProbeSpec(kind="tcp", host="127.0.0.1", port=control_page_port, timeout_ms=500),
                    HealthProbeSpec(
                        kind="http",
                        host="127.0.0.1",
                        port=control_page_port,
                        path="/health",
                        timeout_ms=800,
                        expect_status=200,
                        expect_json={"ok": True},
                    ),
                ),
            ),
            ServiceSpec(
                id="bot_api",
                label="Bot API",
                kind="api",
                required=True,
                host="127.0.0.1",
                port=bot_api_port,
                checks=(
                    HealthProbeSpec(kind="tcp", host="127.0.0.1", port=bot_api_port, timeout_ms=500),
                    HealthProbeSpec(
                        kind="http",
                        host="127.0.0.1",
                        port=bot_api_port,
                        path="/health",
                        timeout_ms=800,
                        expect_status=200,
                        expect_json={"ok": True},
                    ),
                ),
            ),
        ),
    )


class RuntimeStartupIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.processes: list[subprocess.Popen[str]] = []

    def tearDown(self) -> None:
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)
            if process.stdout is not None and not process.stdout.closed:
                process.stdout.close()
            if process.stderr is not None and not process.stderr.closed:
                process.stderr.close()

    def start_health_process(self, port: int, *, ok: bool = True) -> subprocess.Popen[str]:
        script = textwrap.dedent(
            f"""
            import json
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    if self.path != "/health":
                        self.send_response(404)
                        self.end_headers()
                        return
                    body = json.dumps({{"ok": {str(ok)}}}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, format, *args):
                    pass

            ThreadingHTTPServer(("127.0.0.1", {port}), Handler).serve_forever()
            """
        )
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".py", delete=False)
        try:
            handle.write(script)
            handle.close()
            process = subprocess.Popen(
                [sys.executable, handle.name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        finally:
            handle.close()
        self.processes.append(process)
        if not wait_for_port(port):
            raise AssertionError(f"health process port {port} did not open")
        return process

    async def test_real_process_matrix_control_page_up_bot_api_down(self) -> None:
        control_port = unused_tcp_port()
        bot_port = unused_tcp_port()
        self.start_health_process(control_port)

        health = await collect_runtime_health(manifest=manifest_for_ports(control_page_port=control_port, bot_api_port=bot_port))
        services = {service["id"]: service for service in health["services"]}
        codes = {diagnostic["code"] for diagnostic in health["diagnostics"]}

        self.assertEqual(services["control_page"]["state"], "up")
        self.assertEqual(services["bot_api"]["state"], "down")
        self.assertIn("CP_UP_BOT_DOWN", codes)
        self.assertIn("BOT_API_DOWN_WITH_CONTROL_PAGE_UP", codes)

    async def test_real_process_matrix_full_ready(self) -> None:
        control_port = unused_tcp_port()
        bot_port = unused_tcp_port()
        self.start_health_process(control_port)
        self.start_health_process(bot_port)

        health = await collect_runtime_health(manifest=manifest_for_ports(control_page_port=control_port, bot_api_port=bot_port))
        services = {service["id"]: service for service in health["services"]}

        self.assertTrue(health["ok"])
        self.assertEqual(health["overallState"], "up")
        self.assertEqual(services["control_page"]["state"], "up")
        self.assertEqual(services["bot_api"]["state"], "up")


class RealMainProcessStartupSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.processes: list[subprocess.Popen[str]] = []
        self.temp_dirs: list[tempfile.TemporaryDirectory[str]] = []
        self.process_logs: dict[int, tuple[Path, Path]] = {}

    def tearDown(self) -> None:
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for temp_dir in self.temp_dirs:
            deadline = time.time() + 5.0
            while True:
                try:
                    temp_dir.cleanup()
                    break
                except PermissionError:
                    if time.time() >= deadline:
                        raise
                    time.sleep(0.1)

    def test_real_main_process_smoke_contract_is_opt_in(self) -> None:
        source = (REPO_ROOT / "tests" / "runtime" / "test_runtime_startup_integration.py").read_text(encoding="utf-8")
        self.assertIn("EVELYN_RUN_REAL_MAIN_INTEGRATION", source)
        self.assertIn("def start_main_process", source)
        self.assertIn('fetch_json(f"http://127.0.0.1:{port}/health")', source)
        self.assertIn('fetch_json(f"http://127.0.0.1:{port}/api/control-page/state")', source)

        main_source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("EVELYN_INSTANCE_LOCK_PATH", main_source)
        self.assertNotIn('/ "runtime_artifacts"', main_source)
        self.assertGreaterEqual(
            main_source.count("RUNTIME_ARTIFACTS_ROOT"),
            7,
        )
        local_bridge_source = (
            REPO_ROOT
            / "evelyn_core"
            / "runtime"
            / "evelyn_core"
            / "local_io_bridge.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn(
            'PROJECT_ROOT / "runtime_artifacts"',
            local_bridge_source,
        )
        self.assertIn(
            "get_runtime_artifacts_root()",
            local_bridge_source,
        )
        composition_source = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_composition_runtime.py"
        ).read_text(encoding="utf-8")
        self.assertIn("async def health(", composition_source)
        self.assertIn('("GET", "/health", self.health)', composition_source)
        server_runtime_source = (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "control_page_server_start_runtime.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"GET": app.router.add_get', server_runtime_source)

    def allocate_temp_root(self) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.temp_dirs.append(temp_dir)
        return Path(temp_dir.name)

    def start_main_process(
        self,
        port: int,
        *,
        temp_root: Path | None = None,
        continuity_auth_key_path: Path | None = None,
        continuity_auth_anchor_path: Path | None = None,
    ) -> subprocess.Popen[str]:
        temp_root = temp_root or self.allocate_temp_root()
        env = os.environ.copy()
        env.update(
            {
                "DISCORD_ENABLED": "false",
                "LOCAL_ONLY": "true",
                "CONTROL_PAGE_ENABLED": "true",
                "CONTROL_PAGE_HOST": "127.0.0.1",
                "CONTROL_PAGE_PORT": str(port),
                "LOCAL_MIC_ENABLED": "false",
                "VISION_WATCH_ENABLED": "false",
                "TTS_WARMUP_GENERATE_ENABLED": "false",
                "CONTROL_PAGE_WELCOME_LLM_TIMEOUT_SEC": "0.2",
                "CONTROL_PAGE_RUNTIME_CACHE_REFRESH_SEC": "0.2",
                "EVELYN_RUNTIME_ARTIFACTS_DIR": str(temp_root / "artifacts"),
                "EVELYN_INSTANCE_LOCK_PATH": str(temp_root / "evelyn-test.lock"),
                "BOT_MEMORY_DIR": str(temp_root / "bot_memory"),
                "TURN_TRACE_LOG_DIR": str(
                    temp_root / "logs" / "turn_trace"
                ),
                "EVELYN_CACHED_AUDIO_DIR": str(
                    temp_root / "audio_cache"
                ),
            }
        )
        if continuity_auth_key_path is not None:
            env["EVELYN_CONTINUITY_AUTH_KEY_FILE"] = str(
                continuity_auth_key_path
            )
        if continuity_auth_anchor_path is not None:
            env["EVELYN_CONTINUITY_AUTH_ANCHOR_DIR"] = str(
                continuity_auth_anchor_path
            )
        stdout_path = temp_root / "main.stdout.log"
        stderr_path = temp_root / "main.stderr.log"
        with stdout_path.open("w", encoding="utf-8") as stdout_handle, stderr_path.open(
            "w", encoding="utf-8"
        ) as stderr_handle:
            process = subprocess.Popen(
                [sys.executable, str(REPO_ROOT / "main.py")],
                cwd=str(REPO_ROOT),
                env=env,
                stdout=stdout_handle,
                stderr=stderr_handle,
                text=True,
            )
        self.processes.append(process)
        self.process_logs[id(process)] = (stdout_path, stderr_path)
        if not wait_for_port(port, timeout_sec=90.0):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            stdout = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.exists() else ""
            stderr = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""
            raise AssertionError(
                f"real main.py control page port {port} did not open; "
                f"returncode={process.returncode}; stdout={stdout[-1200:]!r}; stderr={stderr[-1200:]!r}"
            )
        return process

    @staticmethod
    def wait_for_continuity_restore(
        status_path: Path,
        *,
        newer_than: float = 0.0,
        timeout_sec: float = 15.0,
    ) -> dict[str, object]:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            try:
                payload = json.loads(
                    status_path.read_text(encoding="utf-8")
                )
            except (FileNotFoundError, json.JSONDecodeError):
                time.sleep(0.05)
                continue
            restored_at = float(payload.get("lastRestoredAt") or 0.0)
            if (
                payload.get("state") == "restored"
                and payload.get("restoredSessionCount") == 1
                and restored_at > newer_than
            ):
                return payload
            time.sleep(0.05)
        raise AssertionError(
            f"continuity restore status not observed at {status_path}"
        )

    @unittest.skipUnless(
        os.getenv("EVELYN_RUN_REAL_MAIN_INTEGRATION", "").lower() in {"1", "true", "yes", "on"},
        "set EVELYN_RUN_REAL_MAIN_INTEGRATION=1 to spawn the real main.py process",
    )
    def test_real_main_process_control_page_smoke(self) -> None:
        port = unused_tcp_port()
        process = self.start_main_process(port)
        self.assertIsNone(process.poll())

        health = fetch_json(f"http://127.0.0.1:{port}/health")
        state = fetch_json(f"http://127.0.0.1:{port}/api/control-page/state")

        self.assertTrue(health["ok"])
        self.assertEqual(health["role"], "bot-api")
        self.assertEqual(health["port"], port)
        self.assertTrue(health["localOnly"])
        self.assertIn("runtime", state)
        self.assertIn("services", state["runtime"])
        self.assertIn("bootProgress", state)
        self.assertTrue(state["ok"])

    @unittest.skipUnless(
        os.getenv(
            "EVELYN_RUN_REAL_MAIN_INTEGRATION",
            "",
        ).lower()
        in {"1", "true", "yes", "on"},
        "set EVELYN_RUN_REAL_MAIN_INTEGRATION=1 to spawn the real main.py process",
    )
    def test_real_main_crash_restart_restores_isolated_continuity(
        self,
    ) -> None:
        temp_root = self.allocate_temp_root()
        artifacts_root = temp_root / "artifacts"
        continuity_root = artifacts_root / "conversation_continuity"
        checkpoint_path = continuity_root / "active.json"
        status_path = continuity_root / "status.json"
        head_path = continuity_root / "checkpoint_head.json"
        auth_key_path = temp_root / "continuity-auth.key"
        auth_key_path.write_bytes(
            b"real-main-continuity-auth-key-32-bytes"
        )
        auth_anchor_path = temp_root / "continuity-anchor"
        auth_anchor_path.mkdir()
        authenticity = ContinuityAuthenticity(
            key=auth_key_path.read_bytes(),
            allow_unsigned_bootstrap=True,
            anchor_root=auth_anchor_path,
        )
        shared_checkpoint_path = (
            REPO_ROOT
            / "runtime_artifacts"
            / "conversation_continuity"
            / "active.json"
        )
        shared_checkpoint_before = (
            shared_checkpoint_path.read_bytes()
            if shared_checkpoint_path.is_file()
            else None
        )

        session_key = "guild:7:text:8:user:42"
        store = SessionStateStore.create_empty()
        store.append_history(
            session_key,
            "main process continuity canary",
            "I will survive the process restart",
            system_prompt="seed prompt",
            max_history_items=12,
        )
        store.update_session_state(
            session_key,
            user_id=42,
            speaker="assistant",
            awaiting_user_reply=True,
            topic_id="main-restart-topic",
            active_conversation_awaiting_reply_sec=300.0,
        )
        SessionContinuityCheckpoint(
            store=store,
            checkpoint_path=checkpoint_path,
            status_path=status_path,
            system_prompt="seed prompt",
            authenticity=authenticity,
        ).flush(force=True)

        first = self.start_main_process(
            unused_tcp_port(),
            temp_root=temp_root,
            continuity_auth_key_path=auth_key_path,
            continuity_auth_anchor_path=auth_anchor_path,
        )
        first_status = self.wait_for_continuity_restore(status_path)
        first_restored_at = float(
            first_status.get("lastRestoredAt") or 0.0
        )

        first.kill()
        first.wait(timeout=10)
        self.assertNotEqual(first.returncode, 0)
        self.assertTrue(checkpoint_path.is_file())

        second = self.start_main_process(
            unused_tcp_port(),
            temp_root=temp_root,
            continuity_auth_key_path=auth_key_path,
            continuity_auth_anchor_path=auth_anchor_path,
        )
        second_status = self.wait_for_continuity_restore(
            status_path,
            newer_than=first_restored_at,
        )
        self.assertIsNone(second.poll())
        self.assertEqual(second_status["restoredSessionCount"], 1)
        self.assertTrue(second_status["keyedAuthenticity"])
        self.assertTrue(second_status["tamperEvident"])
        self.assertTrue(second_status["externalReplayProtected"])
        self.assertTrue(
            second_status["guildRevocationsReplayProtected"]
        )
        self.assertEqual(
            json.loads(head_path.read_text(encoding="utf-8"))[
                "schema"
            ],
            CONTINUITY_HEAD_SCHEMA_V2,
        )
        self.assertGreater(
            float(second_status.get("lastRestoredAt") or 0.0),
            first_restored_at,
        )

        shared_checkpoint_after = (
            shared_checkpoint_path.read_bytes()
            if shared_checkpoint_path.is_file()
            else None
        )
        self.assertEqual(
            shared_checkpoint_after,
            shared_checkpoint_before,
        )


if __name__ == "__main__":
    unittest.main()
