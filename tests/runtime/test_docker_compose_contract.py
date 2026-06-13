from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
COMPOSE = REPO_ROOT / "docker-compose.fast-control.yml"
DOCKER_DIR = REPO_ROOT / "docker"
CODEX_GATEWAY = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "codex_gateway_server.py"
MAIN = REPO_ROOT / "main.py"
CHECK_SCRIPT = REPO_ROOT / "tools" / "check_docker_runtime.ps1"
LAUNCHERS = REPO_ROOT / "evelyn_core" / "runtime" / "launchers"


class DockerComposeContractTests(unittest.TestCase):
    def test_discord_bot_worker_is_declared_as_separate_profile(self) -> None:
        source = COMPOSE.read_text(encoding="utf-8")

        self.assertIn("discord_bot:", source)
        self.assertIn("container_name: evelyn-discord-bot", source)
        self.assertIn('profiles: ["discord"]', source)
        self.assertIn("docker/Dockerfile.discord-bot", source)
        self.assertIn("DISCORD_BOT_TOKEN: \"${DISCORD_BOT_TOKEN:?", source)
        self.assertIn("CONTROL_PAGE_ENABLED: \"false\"", source)
        self.assertIn("STT_SERVICE_FALLBACK_LOCAL: \"false\"", source)
        self.assertIn("VISION_WATCH_ENABLED: \"false\"", source)

    def test_docker_services_use_internal_service_urls_for_core_dependencies(self) -> None:
        source = COMPOSE.read_text(encoding="utf-8")

        self.assertIn("LLM_SERVER_URL: \"http://main_llm:9820/v1/chat/completions\"", source)
        self.assertIn("ROUTER_LLM_URL: \"http://router_llm:9822/v1/chat/completions\"", source)
        self.assertIn("SUMMARY_LLM_URL: \"http://sub_llm:9821/v1/chat/completions\"", source)
        self.assertIn("OMNIVOICE_SERVER_URL: \"http://tts:8880\"", source)
        self.assertIn("STT_SERVICE_URL: \"http://stt:8892\"", source)
        self.assertIn("VISION_SERVICE_URL: \"http://vision:8891\"", source)
        self.assertIn("VOYAGER_CODEX_GATEWAY_URL: \"http://codex_gateway:8787/codex/action\"", source)

    def test_evelyn_containers_do_not_auto_start_with_docker_desktop(self) -> None:
        source = COMPOSE.read_text(encoding="utf-8")

        self.assertNotIn("restart: unless-stopped", source)
        self.assertNotIn("restart: always", source)
        self.assertEqual(source.count('restart: "no"'), 11)

    def test_discord_bot_image_contains_runtime_voice_dependencies(self) -> None:
        requirements = (DOCKER_DIR / "requirements.discord-bot.txt").read_text(encoding="utf-8")
        dockerfile = (DOCKER_DIR / "Dockerfile.discord-bot").read_text(encoding="utf-8")

        for dependency in ("discord.py", "PyNaCl", "psutil", "Pillow", "sounddevice", "silero-vad"):
            self.assertIn(dependency, requirements)
        for package in ("libopus0", "ffmpeg", "libportaudio2"):
            self.assertIn(package, dockerfile)

    def test_remote_stt_warmup_does_not_force_local_qwen_model_load(self) -> None:
        source = MAIN.read_text(encoding="utf-8")

        self.assertIn("if not STT_SERVICE_URL:", source)
        self.assertIn("await asyncio.to_thread(get_stt_model)", source)
        self.assertIn("await asyncio.to_thread(warmup_stt_sync)", source)

    def test_codex_gateway_reports_backend_readiness(self) -> None:
        source = CODEX_GATEWAY.read_text(encoding="utf-8")
        compose = COMPOSE.read_text(encoding="utf-8")
        check_script = CHECK_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("npm install -g @openai/codex@0.128.0", (DOCKER_DIR / "Dockerfile.voyager").read_text(encoding="utf-8"))
        self.assertIn("C:/Users/Admin/.codex/auth.json:/root/.codex/auth.json:ro", compose)
        self.assertIn("backendReady", source)
        self.assertIn("lastActionReady", source)
        self.assertIn("_backend_readiness", source)
        self.assertIn('"exec",\n            "-m",\n            model,', source)
        self.assertIn("$json.backendReady -eq $true", check_script)
        self.assertIn("IncludeCodexAction", check_script)

    def test_fast_control_api_supports_local_bridge_chat_contract(self) -> None:
        source = (REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "fast_control_api.py").read_text(encoding="utf-8")

        self.assertIn('app.router.add_post("/api/control-page/chat", chat_handler)', source)
        self.assertIn('app.router.add_post("/api/control-page/chat-stream", chat_stream_handler)', source)
        self.assertIn('app.router.add_get("/api/local-bridge/status", local_bridge_status_handler)', source)
        self.assertIn('app.router.add_post("/api/local-bridge/status", local_bridge_status_handler)', source)
        self.assertIn('"stream": True', source)
        self.assertIn("windows_local_bridge", source)

    def test_batch_entrypoints_default_to_docker_compose(self) -> None:
        root_start = (REPO_ROOT / "start.bat").read_text(encoding="utf-8")
        core_start = (REPO_ROOT / "evelyn_core" / "start.bat").read_text(encoding="utf-8")
        local_start = (REPO_ROOT / "evelyn_core" / "start_local.bat").read_text(encoding="utf-8")
        docker_helper = (LAUNCHERS / "start_docker_compose_services.ps1").read_text(encoding="utf-8")

        self.assertIn("evelyn_core\\start.bat", root_start)
        self.assertIn('call "%~dp0start_local.bat" %*', core_start)
        self.assertIn("start_local_background.ps1", local_start)
        self.assertIn("docker-compose.fast-control.yml", docker_helper)
        self.assertIn("@('compose') + $composeArgs + @('up', '-d') + $serviceArgs", docker_helper)
        self.assertIn("EVELYN_ALLOW_LEGACY_HOST_START", core_start + local_start)
        self.assertIn('call "%~dp0start_main_llm.bat" --legacy-host', local_start)

    def test_individual_service_batches_do_not_start_host_processes_by_default(self) -> None:
        service_batches = {
            "start_main_llm.bat": ("-Profiles llm", "-Services main_llm"),
            "start_router_llm.bat": ("-Profiles llm", "-Services router_llm"),
            "start_sub_llm.bat": ("-Profiles llm", "-Services sub_llm"),
            "start_tts.bat": ("-Profiles tts", "-Services tts"),
            "start_vision.bat": ("-Profiles vision", "-Services vision"),
            "start_codex_gateway.bat": ("-Profiles voyager", "-Services codex_gateway"),
            "start_voyager_service.bat": ("-Profiles voyager", "-Services voyager"),
            "start_voyager.bat": ("-Profiles voyager", "-Services codex_gateway,voyager"),
            "start_bot.bat": ("-Profiles llm,tts,stt,vision,voyager,discord", "-Services discord_bot"),
        }

        for name, expected in service_batches.items():
            with self.subTest(batch=name):
                source = (REPO_ROOT / "evelyn_core" / name).read_text(encoding="utf-8")
                self.assertIn("start_docker_compose_services.ps1", source)
                self.assertIn(expected[0], source)
                self.assertIn(expected[1], source)
                self.assertIn("EVELYN_ALLOW_LEGACY_HOST_START", source)


if __name__ == "__main__":
    unittest.main()
