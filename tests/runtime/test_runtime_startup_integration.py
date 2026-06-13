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
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

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
            temp_dir.cleanup()

    def test_real_main_process_smoke_contract_is_opt_in(self) -> None:
        source = (REPO_ROOT / "tests" / "runtime" / "test_runtime_startup_integration.py").read_text(encoding="utf-8")
        self.assertIn("EVELYN_RUN_REAL_MAIN_INTEGRATION", source)
        self.assertIn("def start_main_process", source)
        self.assertIn('fetch_json(f"http://127.0.0.1:{port}/health")', source)
        self.assertIn('fetch_json(f"http://127.0.0.1:{port}/api/control-page/state")', source)

        main_source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("EVELYN_INSTANCE_LOCK_PATH", main_source)
        self.assertIn("async def control_page_health_handler", main_source)
        self.assertIn('app.router.add_get("/health", control_page_health_handler)', main_source)

    def start_main_process(self, port: int) -> subprocess.Popen[str]:
        temp_dir = tempfile.TemporaryDirectory()
        self.temp_dirs.append(temp_dir)
        temp_root = Path(temp_dir.name)
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
            }
        )
        process = subprocess.Popen(
            [sys.executable, str(REPO_ROOT / "main.py")],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.processes.append(process)
        if not wait_for_port(port, timeout_sec=90.0):
            stdout = ""
            stderr = ""
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            try:
                stdout, stderr = process.communicate(timeout=3)
            except Exception:
                pass
            raise AssertionError(
                f"real main.py control page port {port} did not open; "
                f"returncode={process.returncode}; stdout={stdout[-1200:]!r}; stderr={stderr[-1200:]!r}"
            )
        return process

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


if __name__ == "__main__":
    unittest.main()
