from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
COMPOSE = REPO_ROOT / "docker-compose.fast-control.yml"
CONTINUITY_AUTH_COMPOSE = (
    REPO_ROOT / "docker-compose.continuity-auth.yml"
)
MEMORY_INTEGRITY_COMPOSE = (
    REPO_ROOT / "docker-compose.memory-integrity.yml"
)
DOCKER_DIR = REPO_ROOT / "docker"
CODEX_GATEWAY = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "codex_gateway_server.py"
MAIN = REPO_ROOT / "main.py"
RUNTIME_LIFECYCLE_COMPOSITION = (
    REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "runtime_lifecycle_composition.py"
)
CHECK_SCRIPT = REPO_ROOT / "tools" / "check_docker_runtime.ps1"
LAUNCHERS = REPO_ROOT / "evelyn_core" / "runtime" / "launchers"


class DockerComposeContractTests(unittest.TestCase):
    def test_memory_integrity_override_is_bot_api_only_and_external(
        self,
    ) -> None:
        source = MEMORY_INTEGRITY_COMPOSE.read_text(
            encoding="utf-8"
        )

        self.assertIn("  bot_api:\n", source)
        self.assertNotIn("discord_bot:", source)
        self.assertIn(
            "${EVELYN_MEMORY_INTEGRITY_KEY_FILE:?",
            source,
        )
        self.assertIn(
            "EVELYN_MEMORY_INTEGRITY_KEY_FILE: "
            "/run/secrets/evelyn_memory_integrity.key",
            source,
        )
        self.assertIn(
            "${EVELYN_MEMORY_INTEGRITY_ANCHOR_DIR:?",
            source,
        )
        self.assertIn(
            "target: /var/lib/evelyn-memory-integrity-anchor",
            source,
        )
        self.assertNotIn("bot_memory", source)
        self.assertNotIn("runtime_artifacts/secrets", source)

    def test_continuity_auth_override_shares_key_and_external_anchor(
        self,
    ) -> None:
        source = CONTINUITY_AUTH_COMPOSE.read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "${EVELYN_CONTINUITY_AUTH_KEY_FILE:?",
            source,
        )
        self.assertEqual(
            source.count(
                "EVELYN_CONTINUITY_AUTH_KEY_FILE: "
                "/run/secrets/evelyn_continuity_auth.key"
            ),
            2,
        )
        self.assertEqual(
            source.count("- evelyn_continuity_auth_key"),
            2,
        )
        self.assertIn(
            "${EVELYN_CONTINUITY_AUTH_ANCHOR_DIR:?",
            source,
        )
        self.assertEqual(
            source.count(
                "EVELYN_CONTINUITY_AUTH_ANCHOR_DIR: "
                "/var/lib/evelyn-continuity-anchor"
            ),
            2,
        )
        self.assertEqual(
            source.count(
                "target: /var/lib/evelyn-continuity-anchor"
            ),
            2,
        )
        self.assertNotIn("runtime_artifacts/secrets", source)

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

    def test_app_images_are_built_and_started_with_one_source_revision(self) -> None:
        source = COMPOSE.read_text(encoding="utf-8")

        for service, next_service in (
            ("bot_api", "control_page"),
            ("control_page", "discord_bot"),
            ("discord_bot", "main_llm"),
        ):
            section = source.split(f"  {service}:\n", 1)[1].split(
                f"\n  {next_service}:",
                1,
            )[0]
            self.assertIn(
                'EVELYN_SOURCE_REVISION: "${EVELYN_SOURCE_REVISION:-unversioned}"',
                section,
            )
            self.assertIn(
                'EVELYN_EXPECTED_SOURCE_REVISION: "${EVELYN_SOURCE_REVISION:-unversioned}"',
                section,
            )

        for name, role in (
            ("Dockerfile.bot-api", "bot_api"),
            ("Dockerfile.control-page", "control_page"),
            ("Dockerfile.discord-bot", "discord_bot"),
        ):
            dockerfile = (DOCKER_DIR / name).read_text(encoding="utf-8")
            self.assertIn("ARG EVELYN_SOURCE_REVISION=unversioned", dockerfile)
            self.assertIn(f"ENV EVELYN_RUNTIME_ROLE={role}", dockerfile)
            self.assertIn(
                "ENV EVELYN_IMAGE_SOURCE_REVISION=${EVELYN_SOURCE_REVISION}",
                dockerfile,
            )

    def test_cross_surface_scope_is_wired_to_both_checkpoint_readers(
        self,
    ) -> None:
        source = COMPOSE.read_text(encoding="utf-8")
        bot_api = source.split("  bot_api:\n", 1)[1].split(
            "\n  control_page:",
            1,
        )[0]
        discord_bot = source.split(
            "  discord_bot:\n",
            1,
        )[1].split("\n  main_llm:", 1)[0]

        for service in (bot_api, discord_bot):
            self.assertIn(
                "CROSS_SURFACE_CONTINUITY_ENABLED:",
                service,
            )
            self.assertIn(
                "CROSS_SURFACE_CONTINUITY_GUILD_ID:",
                service,
            )
            self.assertIn(
                "CROSS_SURFACE_CONTINUITY_USER_ID:",
                service,
            )

    def test_docker_services_use_internal_service_urls_for_core_dependencies(self) -> None:
        source = COMPOSE.read_text(encoding="utf-8")

        self.assertIn("LLM_SERVER_URL: \"http://main_llm:9820/v1/chat/completions\"", source)
        self.assertIn("ROUTER_LLM_URL: \"http://router_llm:9822/v1/chat/completions\"", source)
        self.assertIn("SUMMARY_LLM_URL: \"http://sub_llm:9821/v1/chat/completions\"", source)
        self.assertIn("OMNIVOICE_SERVER_URL: \"http://tts:8880\"", source)
        self.assertIn("STT_SERVICE_URL: \"http://stt:8892\"", source)
        self.assertIn("VISION_SERVICE_URL: \"http://vision:8891\"", source)
        self.assertIn("VOYAGER_CODEX_GATEWAY_URL: \"http://codex_gateway:8787/codex/action\"", source)

    def test_default_tts_is_source_and_revision_gated_omnivoice(self) -> None:
        source = COMPOSE.read_text(encoding="utf-8")
        tts = source.split("  tts:\n", 1)[1].split(
            "\n  voxcpm_fallback:",
            1,
        )[0]
        fallback = source.split("  voxcpm_fallback:\n", 1)[1].split(
            "\n  vision_runtime:",
            1,
        )[0]
        checker = CHECK_SCRIPT.read_text(encoding="utf-8")
        dockerfile = (DOCKER_DIR / "Dockerfile.omnivoice").read_text(
            encoding="utf-8"
        )
        source_manifest = (DOCKER_DIR / "omnivoice_source.sha256").read_text(
            encoding="utf-8"
        )
        model_manifest = (DOCKER_DIR / "omnivoice_model.sha256").read_text(
            encoding="utf-8"
        )
        privacy_patch = (DOCKER_DIR / "omnivoice_evelyn.patch").read_text(
            encoding="utf-8"
        )
        entrypoint = (DOCKER_DIR / "run_omnivoice.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn('profiles: ["tts"]', tts)
        self.assertIn("image: evelyn-omnivoice-tts:recipe-7cfc51e96088", tts)
        self.assertIn("pull_policy: never", tts)
        self.assertIn("dockerfile: docker/Dockerfile.omnivoice", tts)
        self.assertIn("omnivoice_source:", tts)
        self.assertIn('OMNIVOICE_MODEL_ID: "k2-fsa/OmniVoice"', tts)
        self.assertIn(
            'OMNIVOICE_MODEL_REVISION: "c5fdb5ccb189668d56333f77ba2629f4cd7535f4"',
            tts,
        )
        self.assertIn('OMNIVOICE_STREAM_STRATEGY: "sentence"', tts)
        self.assertIn('HF_HUB_OFFLINE: "1"', tts)
        self.assertIn("/home/ubuntu/app/profiles:ro", tts)
        self.assertIn("/hub:/home/ubuntu/.cache/huggingface/hub:ro", tts)
        self.assertIn("payload.get('status') == 'healthy'", tts)
        self.assertIn("payload.get('model_loaded') is True", tts)
        self.assertIn("payload.get('model_id') == 'k2-fsa/OmniVoice'", tts)
        self.assertIn(
            "payload.get('model_revision') == 'c5fdb5ccb189668d56333f77ba2629f4cd7535f4'",
            tts,
        )
        self.assertIn('max-size: "10m"', tts)
        self.assertIn('max-file: "3"', tts)
        self.assertNotIn("VOXCPM_", tts)

        self.assertIn('profiles: ["tts-fallback"]', fallback)
        self.assertIn("pull_policy: never", fallback)
        self.assertIn("docker/Dockerfile.voxcpm", fallback)
        self.assertIn('VOXCPM_MODEL_ID: "openbmb/VoxCPM2"', fallback)
        self.assertIn('"127.0.0.1:8881:8880"', fallback)

        self.assertIn("COPY --from=omnivoice_source", dockerfile)
        self.assertNotIn("COPY --from=omnivoice_source .", dockerfile)
        self.assertIn(
            "COPY --from=omnivoice_source services/*.py",
            dockerfile,
        )
        self.assertIn("sha256sum --check /tmp/omnivoice_source.sha256", dockerfile)
        self.assertIn(
            "COPY docker/omnivoice_model.sha256",
            dockerfile,
        )
        self.assertIn("patch --batch --forward -p1", dockerfile)
        self.assertIn("ENTRYPOINT", dockerfile)
        self.assertIn("${PATH}", dockerfile)
        self.assertIn("${LD_LIBRARY_PATH}", dockerfile)
        self.assertNotIn("git+https://", dockerfile)
        self.assertEqual(len(source_manifest.splitlines()), 20)
        self.assertEqual(len(model_manifest.splitlines()), 13)
        self.assertTrue((DOCKER_DIR / "omnivoice-server.LICENSE").is_file())
        self.assertIn("_PRIVATE_LOG_FIELDS", privacy_patch)
        self.assertIn('+    "speaker",', privacy_patch)
        self.assertIn('+    "voice",', privacy_patch)
        self.assertIn("disconnect cancellation is safe", privacy_patch)
        self.assertIn('"Streaming chunk failed; chars=%d error_type=%s"', privacy_patch)
        self.assertIn('if key != "ref_text"', privacy_patch)
        self.assertIn('"Synthesis failed; error_type=%s"', privacy_patch)
        self.assertIn('"Clone synthesis failed; error_type=%s"', privacy_patch)
        self.assertEqual(privacy_patch.count('detail="Synthesis failed"'), 2)
        self.assertIn('-                    "detail": exc.errors(),', privacy_patch)
        self.assertIn("OMNIVOICE_MODEL_REVISION", entrypoint)
        self.assertIn(
            'expected_model_revision="c5fdb5ccb189668d56333f77ba2629f4cd7535f4"',
            entrypoint,
        )
        self.assertIn(
            '"${OMNIVOICE_MODEL_REVISION:-}" != "${expected_model_revision}"',
            entrypoint,
        )
        self.assertIn(
            "sha256sum --check --strict /opt/omnivoice-server/omnivoice_model.sha256",
            entrypoint,
        )
        self.assertIn("exit 78", entrypoint)

        local_bridge = (
            REPO_ROOT
            / "evelyn_core"
            / "runtime"
            / "evelyn_core"
            / "local_io_bridge.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            '"LOCAL_BRIDGE_VOXCPM_INPUT_STREAMING_ENABLED",\n    "false",',
            local_bridge,
        )

        for field in (
            "status",
            "ready",
            "model_loaded",
            "model_id",
            "model_revision",
        ):
            self.assertIn(f"$json.{field}", checker)
        self.assertIn('$json.model_id -eq "k2-fsa/OmniVoice"', checker)
        self.assertIn(
            '$json.model_revision -eq "c5fdb5ccb189668d56333f77ba2629f4cd7535f4"',
            checker,
        )

    def test_evelyn_containers_do_not_auto_start_with_docker_desktop(self) -> None:
        source = COMPOSE.read_text(encoding="utf-8")

        self.assertNotIn("restart: unless-stopped", source)
        self.assertNotIn("restart: always", source)
        self.assertEqual(source.count('restart: "no"'), 14)

    def test_bot_api_stop_budget_exceeds_artifact_fence_grace(self) -> None:
        source = COMPOSE.read_text(encoding="utf-8")
        bot_api = source.split("  bot_api:\n", 1)[1].split(
            "\n  control_page:",
            1,
        )[0]

        match = re.search(r"stop_grace_period: (\d+)s", bot_api)
        self.assertIsNotNone(match)
        self.assertGreater(int(match.group(1)), 31 + 10)
        self.assertEqual(int(match.group(1)), 60)

    def test_mindcraft_persists_microsoft_account_profile_cache(self) -> None:
        source = COMPOSE.read_text(encoding="utf-8")
        voyager = source.split("  voyager:\n", 1)[1]

        self.assertIn("- ./bot_profiles:/app/bot_profiles", voyager)
        self.assertIn("- ./bot_memory/mindcraft:/app/mindcraft/bots/Evelyn_0428", voyager)
        self.assertIn('MINEFLAYER_PROFILES_FOLDER: "/app/bot_profiles"', voyager)
        self.assertIn('MINEFLAYER_AUTH: "microsoft"', voyager)
        self.assertIn('MINECRAFT_VERSION: "1.21.11"', voyager)
        self.assertIn('MINEFLAYER_AUTH", "microsoft"', (REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "config.py").read_text(encoding="utf-8"))
        self.assertIn('MINEFLAYER_USERNAME", "Evelyn_0428"', (REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "config.py").read_text(encoding="utf-8"))
        self.assertIn('MINEFLAYER_USERNAME=Evelyn_0428', (REPO_ROOT / "evelyn_core" / "start_env.bat").read_text(encoding="utf-8"))

    def test_mindcraft_uses_authenticated_codex_gateway(self) -> None:
        source = COMPOSE.read_text(encoding="utf-8")
        voyager = source.split("  voyager:\n", 1)[1]

        self.assertIn('MINDCRAFT_CODEX_GATEWAY_URL: "http://codex_gateway:8787/codex/action"', voyager)
        self.assertIn('MINDCRAFT_CODEX_MODEL: "gpt-5.5"', voyager)
        self.assertIn('VOYAGER_CODEX_GATEWAY_TOKEN_FILE: "/app/runtime_artifacts/secrets/codex_gateway.token"', voyager)
        self.assertIn('MINDCRAFT_ENABLE_SKIN_COMMANDS: "false"', voyager)
        self.assertIn('dockerfile: docker/Dockerfile.mindcraft', voyager)
        self.assertIn('evelyn_core.mindcraft_service', voyager)
        self.assertIn('["CMD", "python3", "-c"', voyager)

    def test_mindcraft_uses_qwen14b_local_planner_with_shared_router(self) -> None:
        source = COMPOSE.read_text(encoding="utf-8")
        voyager = source.split("  voyager:\n", 1)[1]
        minecraft_llm = source.split("  minecraft_llm:\n", 1)[1].split("\n  sub_llm:", 1)[0]

        self.assertIn('profiles: ["llm", "voyager"]', source)
        self.assertIn('container_name: evelyn-minecraft-llm', minecraft_llm)
        self.assertIn('/llama/models/qwen3-14b/Qwen3-14B-Q4_K_M.gguf', minecraft_llm)
        self.assertIn('-c 6144', minecraft_llm)
        self.assertIn('-np 1', minecraft_llm)
        self.assertIn('--cache-type-k q8_0', minecraft_llm)
        self.assertIn('--cache-type-v q8_0', minecraft_llm)
        self.assertIn('NVIDIA_VISIBLE_DEVICES: "1"', minecraft_llm)
        self.assertIn('CUDA_VISIBLE_DEVICES: "1"', minecraft_llm)
        self.assertIn('LLAMA_CHAT_TEMPLATE_KWARGS: \'{"enable_thinking":false}\'', minecraft_llm)
        self.assertIn('MINDCRAFT_LOCAL_LLM_URL: "http://minecraft_llm:9823/v1/chat/completions"', voyager)
        self.assertIn('MINDCRAFT_ROUTER_URL: "http://router_llm:9822/v1/chat/completions"', voyager)
        self.assertIn('MINDCRAFT_CODEX_COOLDOWN_SEC: "30"', voyager)
        self.assertIn(
            'MINDCRAFT_PLANNER_STATE_PATH: "/app/runtime_artifacts/mindcraft/planner_state.json"',
            voyager,
        )
        self.assertIn('MINDCRAFT_DETERMINISTIC_TOOL_BOOTSTRAP: "false"', voyager)
        self.assertIn('minecraft_llm:', voyager)
        self.assertIn('router_llm:', voyager)
        self.assertIn("GET /health HTTP/1.0", minecraft_llm)
        router_llm = source.split("  router_llm:\n", 1)[1].split("\n  minecraft_llm:", 1)[0]
        self.assertIn("GET /health HTTP/1.0", router_llm)

    def test_mindcraft_image_applies_pinned_evelyn_overlay(self) -> None:
        dockerfile = (DOCKER_DIR / "Dockerfile.mindcraft").read_text(encoding="utf-8")
        package = (REPO_ROOT / "external" / "mindcraft_evelyn" / "package.json").read_text(encoding="utf-8")
        patch = (REPO_ROOT / "external" / "mindcraft_evelyn" / "evelyn.patch").read_text(encoding="utf-8")

        self.assertIn("FROM node:22-bookworm-slim", dockerfile)
        self.assertIn("sed -i 's/\\r$//'", dockerfile)
        self.assertIn("external/mindcraft", dockerfile)
        self.assertIn("patch -p1", dockerfile)
        self.assertIn('"mineflayer": "4.37.1"', package)
        self.assertIn("profilesFolder", patch)
        self.assertIn("MINDCRAFT_ENABLE_SKIN_COMMANDS", patch)

    def test_discord_bot_image_contains_runtime_voice_dependencies(self) -> None:
        requirements = (DOCKER_DIR / "requirements.discord-bot.txt").read_text(encoding="utf-8")
        dockerfile = (DOCKER_DIR / "Dockerfile.discord-bot").read_text(encoding="utf-8")

        for dependency in ("discord.py", "PyNaCl", "psutil", "Pillow", "sounddevice", "silero-vad"):
            self.assertIn(dependency, requirements)
        for package in ("libopus0", "ffmpeg", "libportaudio2"):
            self.assertIn(package, dockerfile)

    def test_voyager_image_contains_upstream_runtime_dependencies(self) -> None:
        requirements = (DOCKER_DIR / "requirements.voyager.txt").read_text(encoding="utf-8")
        dockerfile = (DOCKER_DIR / "Dockerfile.voyager").read_text(encoding="utf-8")

        for dependency in (
            "requests",
            "javascript",
            "langchain",
            "langchain-community",
            "openai",
            "tiktoken",
            "gymnasium",
            "psutil",
            "minecraft-launcher-lib",
        ):
            self.assertIn(dependency, requirements)
        self.assertIn("FROM node:22-slim AS node_runtime", dockerfile)
        self.assertNotIn("chromadb", requirements)
        self.assertNotIn("build-essential", dockerfile)
        self.assertIn("COPY package.json package-lock.json /app/", dockerfile)
        self.assertIn("npm ci --omit=dev", dockerfile)

    def test_remote_stt_warmup_does_not_force_local_qwen_model_load(self) -> None:
        main_source = MAIN.read_text(encoding="utf-8")
        lifecycle_source = RUNTIME_LIFECYCLE_COMPOSITION.read_text(encoding="utf-8")

        self.assertIn("stt_service_url=STT_SERVICE_URL", main_source)
        self.assertIn("if not deps.stt_service_url:", lifecycle_source)
        self.assertIn("await deps.to_thread(deps.get_stt_model)", lifecycle_source)
        self.assertIn("await deps.to_thread(deps.warmup_stt_sync)", lifecycle_source)

    def test_codex_gateway_reports_backend_readiness(self) -> None:
        source = CODEX_GATEWAY.read_text(encoding="utf-8")
        compose = COMPOSE.read_text(encoding="utf-8")
        check_script = CHECK_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("npm install -g @openai/codex@0.128.0", (DOCKER_DIR / "Dockerfile.voyager").read_text(encoding="utf-8"))
        self.assertIn(
            "${EVELYN_CODEX_CREDENTIALS_DIR:-./runtime_artifacts/secrets/codex_device_home}"
            ":/run/secrets/evelyn-codex:ro",
            compose,
        )
        self.assertNotIn("${USERPROFILE}/.codex/auth.json", compose)
        self.assertIn("CODEX_HOME: \"/tmp/evelyn-codex-home\"", compose)
        self.assertIn("read_only: true", compose)
        self.assertIn("no-new-privileges:true", compose)
        self.assertIn("/tmp/evelyn-codex-home:mode=0700", compose)
        self.assertIn("backendReady", source)
        self.assertIn("lastActionReady", source)
        self.assertIn("actionAuthRequired", source)
        self.assertIn("gateway_request_authorized", source)
        self.assertIn("gateway_auth_headers", (REPO_ROOT / "third_party" / "Voyager" / "voyager" / "agents" / "codex_gateway_llm.py").read_text(encoding="utf-8"))
        self.assertIn("Authorization = \"Bearer $token\"", check_script)
        self.assertIn("_backend_readiness", source)
        self.assertIn('"exec",\n            "-m",\n            model,', source)
        self.assertIn("$json.backendReady -eq $true", check_script)
        self.assertIn("IncludeCodexAction", check_script)

    def test_runtime_checker_matches_deferred_minecraft_contract(self) -> None:
        check_script = CHECK_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("[switch]$IncludeMinecraftStack", check_script)
        self.assertIn(
            'if ($IncludeMinecraftStack -or $IncludeCodexAction)',
            check_script,
        )
        self.assertIn(
            '$env:DISCORD_BOT_TOKEN = "runtime-check-disabled"',
            check_script,
        )
        self.assertIn(
            "Voyager health check skipped; use -IncludeMinecraftStack",
            check_script,
        )
        self.assertIn(
            'Add-Failure "$flag is not true for the requested Minecraft stack"',
            check_script,
        )
        self.assertNotIn(
            '$script:ComposeProfiles = @("llm", "tts", "vision", "stt", "voyager")',
            check_script,
        )

    def test_runtime_checker_uses_public_state_for_local_bridge(self) -> None:
        check_script = CHECK_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("$controlState.voice.localBridge", check_script)
        self.assertIn("$bridge.stale -ne $true", check_script)
        self.assertNotIn(
            'Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8798/api/local-bridge/status"',
            check_script,
        )

    def test_host_specific_compose_paths_are_configurable(self) -> None:
        source = COMPOSE.read_text(encoding="utf-8")

        self.assertNotIn("C:/Users/Admin", source)
        for variable in (
            "EVELYN_LLAMA_CPP_DIR",
            "EVELYN_OMNIVOICE_SERVER_DIR",
            "EVELYN_OMNIVOICE_PROFILES_DIR",
            "EVELYN_HUGGINGFACE_CACHE_DIR",
            "EVELYN_CODEX_CREDENTIALS_DIR",
        ):
            self.assertIn(variable, source)

    def test_vision_remote_code_runs_from_pinned_read_only_snapshot(
        self,
    ) -> None:
        source = COMPOSE.read_text(encoding="utf-8")
        vision_runtime = source.split("  vision_runtime:\n", 1)[1].split(
            "\n  vision:",
            1,
        )[0]
        vision_ingress = source.split("  vision:\n", 1)[1].split(
            "\n  stt:",
            1,
        )[0]

        self.assertIn(
            'VISION_OCR_REVISION: "42ec56b72a23984ac059e7c8a6d397a8529423fe"',
            vision_runtime,
        )
        self.assertIn(
            'VISION_OCR_LOCAL_FILES_ONLY: "true"',
            vision_runtime,
        )
        self.assertIn('HF_HOME: "/model-cache"', vision_runtime)
        self.assertIn('HF_HUB_CACHE: "/model-cache/hub"', vision_runtime)
        self.assertIn('HF_HUB_OFFLINE: "1"', vision_runtime)
        self.assertIn('HF_MODULES_CACHE: "/tmp/hf-modules"', vision_runtime)
        self.assertIn("read_only: true", vision_runtime)
        self.assertIn("cap_drop:\n      - ALL", vision_runtime)
        self.assertIn("- no-new-privileges:true", vision_runtime)
        self.assertIn("pids_limit: 512", vision_runtime)
        self.assertIn("/tmp:rw,nosuid,nodev,size=4g", vision_runtime)
        self.assertIn(
            "./runtime_artifacts:/app/runtime_artifacts:ro",
            vision_runtime,
        )
        self.assertIn("./logs:/app/logs:ro", vision_runtime)
        self.assertIn(":/model-cache:ro", vision_runtime)
        self.assertNotIn(":/root/.cache/huggingface", vision_runtime)
        self.assertIn("networks:\n      - vision_isolated", vision_runtime)
        self.assertNotIn("ports:", vision_runtime)

        self.assertIn(
            "dockerfile: docker/Dockerfile.vision-ingress",
            vision_ingress,
        )
        self.assertIn("vision_runtime:\n        condition: service_healthy", vision_ingress)
        self.assertIn("read_only: true", vision_ingress)
        self.assertIn("cap_drop:\n      - ALL", vision_ingress)
        self.assertIn("- no-new-privileges:true", vision_ingress)
        self.assertIn("pids_limit: 128", vision_ingress)
        self.assertIn("- default\n      - vision_isolated", vision_ingress)
        self.assertIn('"127.0.0.1:8891:8891"', vision_ingress)
        self.assertNotIn("volumes:", vision_ingress)
        self.assertIn(
            "networks:\n  vision_isolated:\n    internal: true",
            source,
        )

    def test_fast_control_api_supports_local_bridge_chat_contract(self) -> None:
        source = (REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "fast_control_api.py").read_text(encoding="utf-8")

        self.assertIn('app.router.add_post("/api/control-page/chat", chat_handler)', source)
        self.assertIn('app.router.add_post("/api/control-page/chat-stream", chat_stream_handler)', source)
        self.assertIn('app.router.add_get("/api/control-page/action-events", action_events_handler)', source)
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
        self.assertIn(
            "@('compose') + $composeArgs + @('up', '-d', '--no-build') + $serviceArgs",
            docker_helper,
        )
        self.assertIn("$normalizedServices -contains 'vision'", docker_helper)
        self.assertIn("$normalizedServices -contains 'tts'", docker_helper)
        self.assertIn(
            "& docker image inspect $ttsImage *> $null",
            docker_helper,
        )
        self.assertIn("evelyn-omnivoice-tts:recipe-7cfc51e96088", docker_helper)
        self.assertIn("if ($buildEnabled -or $ttsImageMissing)", docker_helper)
        self.assertIn("build_local_docker_images.ps1", docker_helper)
        self.assertIn("-Services $pathSafeBuildServices", docker_helper)
        self.assertIn("$serviceArgs | Where-Object", docker_helper)
        self.assertIn("$normalizedServices -contains 'voyager'", docker_helper)
        self.assertIn("$credentialDirectory.StartsWith(", docker_helper)
        self.assertIn("$liveCodexPrefix", docker_helper)
        self.assertIn("EVELYN_ALLOW_LEGACY_HOST_START", core_start + local_start)
        self.assertIn('call "%~dp0start_main_llm.bat" --legacy-host', local_start)

        provisioner = (
            LAUNCHERS / "provision_codex_credentials.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "Remove-Item -LiteralPath $staleConfig -Force",
            provisioner,
        )

        native_gateway = (
            LAUNCHERS / "start_codex_gateway.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("evelyn-codex-gateway-$PID", native_gateway)
        self.assertIn("$env:CODEX_HOME = $codexEphemeralHome", native_gateway)
        self.assertIn(".evelyn-ephemeral-codex-home", native_gateway)

    def test_individual_service_batches_do_not_start_host_processes_by_default(self) -> None:
        service_batches = {
            "start_main_llm.bat": ("-Profiles llm", "-Services main_llm"),
            "start_router_llm.bat": ("-Profiles llm", "-Services router_llm"),
            "start_sub_llm.bat": ("-Profiles llm", "-Services sub_llm"),
            "start_tts.bat": ("-Profiles tts", "-Services tts"),
            "start_vision.bat": ("-Profiles vision", "-Services vision"),
            "start_codex_gateway.bat": ("-Profiles voyager", "-Services codex_gateway"),
            "start_voyager_service.bat": ("-Profiles voyager", "-Services voyager"),
            "start_voyager.bat": ("-Profiles voyager", "-Services router_llm,minecraft_llm,codex_gateway,voyager"),
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
