from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
COMPOSE = REPO_ROOT / "docker-compose.fast-control.yml"
GPU1_BENCHMARK_COMPOSE = REPO_ROOT / "docker-compose.gpu1-benchmark.yml"
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
OMNIVOICE_RECIPE_COMPONENTS = (
    DOCKER_DIR / "Dockerfile.omnivoice",
    DOCKER_DIR / "omnivoice_evelyn.patch",
    DOCKER_DIR / "omnivoice_source.sha256",
    DOCKER_DIR / "omnivoice_model.sha256",
    DOCKER_DIR / "run_omnivoice.sh",
    DOCKER_DIR / "omnivoice-server.LICENSE",
)


def _omnivoice_recipe_tag() -> str:
    records = "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  "
        f"{path.relative_to(REPO_ROOT).as_posix()}\n"
        for path in OMNIVOICE_RECIPE_COMPONENTS
    )
    return f"recipe-{hashlib.sha256(records.encode('utf-8')).hexdigest()[:12]}"


class DockerComposeContractTests(unittest.TestCase):
    def test_main_llm_enables_prefill_and_prompt_cache_tuning(self) -> None:
        source = COMPOSE.read_text(encoding="utf-8")
        main_llm = source.split("\n  main_llm:\n", 1)[1].split(
            "\n  minecraft_llm:",
            1,
        )[0]
        launcher = (LAUNCHERS / "run_main_llm.sh").read_text(encoding="utf-8")
        env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

        for expected in (
            'MAIN_LLM_CUDA_GRAPHS_ENABLED: "${MAIN_LLM_CUDA_GRAPHS_ENABLED:-1}"',
            'GGML_CUDA_GRAPH_OPT: "${MAIN_LLM_CUDA_GRAPH_OPT:-1}"',
            'MAIN_LLM_SWA_FULL_ENABLED: "${MAIN_LLM_SWA_FULL_ENABLED:-1}"',
            '1) swa_full_args=(--swa-full) ;;',
            '0) swa_full_args=() ;;',
            '0) export GGML_CUDA_DISABLE_GRAPHS=1; graph_disable_state=present ;;',
            '1) unset GGML_CUDA_DISABLE_GRAPHS; graph_disable_state=absent ;;',
            "GGML_CUDA_DISABLE_GRAPHS=%s\\0",
            '--batch-size "$${MAIN_LLM_BATCH_SIZE}"',
            '--ubatch-size "$${MAIN_LLM_UBATCH_SIZE}"',
            '--cache-ram "$${MAIN_LLM_CACHE_RAM_MIB}"',
            "--cache-prompt",
            '--cache-reuse "$${MAIN_LLM_CACHE_REUSE}"',
        ):
            self.assertIn(expected, main_llm)
        for expected in (
            'MAIN_LLM_CUDA_GRAPHS_ENABLED="${MAIN_LLM_CUDA_GRAPHS_ENABLED:-1}"',
            'export GGML_CUDA_GRAPH_OPT="$MAIN_LLM_CUDA_GRAPH_OPT"',
            'MAIN_LLM_SWA_FULL_ENABLED="${MAIN_LLM_SWA_FULL_ENABLED:-1}"',
            'MAIN_LLM_BUILD_DIR="${MAIN_LLM_BUILD_DIR:-$LLAMA_DIR/build-sm120-v1}"',
            'MAIN_LLM_UBATCH_SIZE="${MAIN_LLM_UBATCH_SIZE:-2048}"',
            'exec "$main_llm_server"',
            '1) swa_full_args=(--swa-full) ;;',
            '"${swa_full_args[@]}"',
            '0) export GGML_CUDA_DISABLE_GRAPHS=1 ;;',
            '1) unset GGML_CUDA_DISABLE_GRAPHS ;;',
            '--batch-size "$MAIN_LLM_BATCH_SIZE"',
            '--ubatch-size "$MAIN_LLM_UBATCH_SIZE"',
            '--cache-ram "$MAIN_LLM_CACHE_RAM_MIB"',
        ):
            self.assertIn(expected, launcher)
        self.assertNotIn('eval "$VENV_ACT"', launcher)
        self.assertIn("MAIN_LLM_UBATCH_SIZE=2048", env_example)
        self.assertIn("MAIN_LLM_SWA_FULL_ENABLED=1", env_example)

    def test_main_and_gateway_are_unprofiled_core_dependencies(self) -> None:
        source = COMPOSE.read_text(encoding="utf-8")
        main_llm = source.split("\n  main_llm:\n", 1)[1].split(
            "\n  router_llm:", 1
        )[0]
        gateway = source.split("\n  main_llm_gateway:\n", 1)[1].split(
            "\n  tts:", 1
        )[0]

        self.assertNotIn("profiles:", main_llm)
        self.assertNotIn("profiles:", gateway)

    def test_realtime_and_ingress_gpu_lanes_have_explicit_defaults(self) -> None:
        source = COMPOSE.read_text(encoding="utf-8")
        main_llm = source.split("\n  main_llm:\n", 1)[1].split("\n  router_llm:", 1)[0]
        tts = source.split("\n  tts:\n", 1)[1].split("\n  voxcpm_fallback:", 1)[0]
        stt = source.split("\n  stt:\n", 1)[1].split("\n  codex_gateway:", 1)[0]

        for service in (main_llm, tts):
            self.assertIn(
                'NVIDIA_VISIBLE_DEVICES: "${EVELYN_REALTIME_GPU_ID:-0}"',
                service,
            )
            self.assertIn(
                'CUDA_VISIBLE_DEVICES: "${EVELYN_REALTIME_GPU_ID:-0}"',
                service,
            )
        self.assertIn(
            'NVIDIA_VISIBLE_DEVICES: "${EVELYN_INGRESS_GPU_ID:-1}"',
            stt,
        )
        self.assertIn(
            'CUDA_VISIBLE_DEVICES: "${EVELYN_INGRESS_GPU_ID:-1}"',
            stt,
        )

    def test_main_warmup_is_bound_to_server_epoch(self) -> None:
        source = COMPOSE.read_text(encoding="utf-8")
        bot_api = source.split("  bot_api:\n", 1)[1].split(
            "\n  control_page:",
            1,
        )[0]
        main_llm = source.split("\n  main_llm:\n", 1)[1].split(
            "\n  router_llm:",
            1,
        )[0]
        self.assertIn('MAIN_LLM_EPOCH_FILE: "/main-llm-epoch/epoch"', bot_api)
        self.assertIn("- main_llm_epoch:/main-llm-epoch:ro", bot_api)
        self.assertIn("- main_llm_epoch:/main-llm-epoch", main_llm)
        self.assertIn("cat /proc/sys/kernel/random/uuid", main_llm)
        self.assertIn("mv -f /main-llm-epoch/epoch.tmp", main_llm)
        self.assertIn("\n  main_llm_epoch:\n", source)

    def test_prompt_abi_is_owned_by_the_exact_main_runtime(self) -> None:
        source = COMPOSE.read_text(encoding="utf-8")
        bot_api = source.split("  bot_api:\n", 1)[1].split(
            "\n  control_page:",
            1,
        )[0]
        control_page = source.split("\n  control_page:\n", 1)[1].split(
            "\n  discord_bot:",
            1,
        )[0]
        discord_bot = source.split("\n  discord_bot:\n", 1)[1].split(
            "\n  main_llm:",
            1,
        )[0]
        main_llm = source.split("\n  main_llm:\n", 1)[1].split(
            "\n  router_llm:",
            1,
        )[0]
        router_llm = source.split("\n  router_llm:\n", 1)[1].split(
            "\n  minecraft_llm:", 1
        )[0]
        minecraft_llm = source.split("\n  minecraft_llm:\n", 1)[1].split(
            "\n  sub_llm:", 1
        )[0]
        sub_llm = source.split("\n  sub_llm:\n", 1)[1].split("\n  tts:", 1)[0]

        for consumer in (bot_api, control_page, discord_bot):
            self.assertIn(
                'MAIN_LLM_SERVER_IDENTITY_FILE: "/main-llm-epoch/server-identity"',
                consumer,
            )
            self.assertIn(
                'MAIN_LLM_RUNTIME_TEMPLATE_IDENTITY_FILE: "/main-llm-epoch/runtime-template-identity"',
                consumer,
            )
            self.assertIn("- main_llm_epoch:/main-llm-epoch:ro", consumer)

        self.assertIn("server_path=/llama/build/bin/llama-server", main_llm)
        self.assertIn(
            "runtime_template_args=(--reasoning off --reasoning-budget 0 "
            "--reasoning-format none --jinja --no-mmproj)",
            main_llm,
        )
        self.assertIn(
            "printf '%s\\0' \"$${server_args[@]}\"",
            main_llm,
        )
        self.assertIn(
            "evelyn.llama-server-runtime.v1",
            main_llm,
        )
        self.assertIn("ldd \"$${server_path}\"", main_llm)
        self.assertIn("-name '*.so*'", main_llm)
        self.assertIn(
            "${EVELYN_LLAMA_CPP_DIR:-${USERPROFILE}/llama.cpp}:/llama:ro",
            main_llm,
        )
        self.assertIn(
            "${EVELYN_MAIN_LLM_BUILD_DIR:-${EVELYN_LLAMA_CPP_DIR:-${USERPROFILE}/llama.cpp}/build-sm120-v1}:/llama/build:ro",
            main_llm,
        )
        self.assertEqual(source.count("EVELYN_MAIN_LLM_BUILD_DIR"), 1)
        self.assertIn(
            'MAIN_LLM_UBATCH_SIZE: "${MAIN_LLM_UBATCH_SIZE:-2048}"',
            main_llm,
        )
        self.assertIn(
            "grep -Eq '^CMAKE_CUDA_ARCHITECTURES:[^=]+=120a-real$$' "
            "/llama/build/CMakeCache.txt",
            main_llm,
        )
        for gpu1_service in (router_llm, minecraft_llm, sub_llm):
            self.assertNotIn("EVELYN_MAIN_LLM_BUILD_DIR", gpu1_service)
        llama_dockerfile = (DOCKER_DIR / "Dockerfile.llama").read_text(
            encoding="utf-8"
        )
        self.assertTrue(
            llama_dockerfile.startswith(
                "FROM nvidia/cuda:12.9.2-runtime-ubuntu24.04@sha256:"
                "6d2a0dabc50c3bf14d27fc66822b6b1f94a325807ace17bd1997762307790587\n"
            )
        )
        self.assertIn('exec "$${server_path}" "$${server_args[@]}"', main_llm)
        self.assertIn('"$${runtime_template_args[@]}"', main_llm)

    def test_workspace_mutation_capability_is_control_page_only(self) -> None:
        source = COMPOSE.read_text(encoding="utf-8")
        bot_api = source.split("  bot_api:\n", 1)[1].split("\n  control_page:", 1)[0]
        control_page = source.split("  control_page:\n", 1)[1].split("\n  discord_bot:", 1)[0]
        discord_bot = source.split("  discord_bot:\n", 1)[1].split("\n  main_llm:", 1)[0]

        self.assertNotIn("EVELYN_WORKSPACE_MUTATION_AUTH_TOKEN", bot_api)
        self.assertIn("EVELYN_WORKSPACE_MUTATION_AUTH_TOKEN", control_page)
        self.assertNotIn("EVELYN_WORKSPACE_MUTATION_AUTH_TOKEN", discord_bot)
        self.assertEqual(source.count("EVELYN_WORKSPACE_MUTATION_AUTH_TOKEN"), 2)

    def test_workspace_sandbox_capability_is_bot_api_only(self) -> None:
        source = COMPOSE.read_text(encoding="utf-8")
        bot_api = source.split("  bot_api:\n", 1)[1].split("\n  control_page:", 1)[0]
        control_page = source.split("  control_page:\n", 1)[1].split("\n  discord_bot:", 1)[0]
        discord_bot = source.split("  discord_bot:\n", 1)[1].split("\n  main_llm:", 1)[0]

        self.assertIn("EVELYN_WORKSPACE_SANDBOX_AUTH_TOKEN", bot_api)
        self.assertNotIn("EVELYN_WORKSPACE_SANDBOX_AUTH_TOKEN", control_page)
        self.assertNotIn("EVELYN_WORKSPACE_SANDBOX_AUTH_TOKEN", discord_bot)
        self.assertEqual(source.count("EVELYN_WORKSPACE_SANDBOX_AUTH_TOKEN"), 2)

    def test_gpu1_benchmark_override_exposes_llms_only_for_diagnostics(self) -> None:
        source = GPU1_BENCHMARK_COMPOSE.read_text(encoding="utf-8")

        self.assertEqual(source.count("  stt:\n"), 1)
        self.assertEqual(source.count("  main_llm:\n"), 1)
        self.assertIn("container_name: evelyn-p04-main-llm", source)
        self.assertIn('"127.0.0.1:9820:9820"', source)
        self.assertIn(
            "networks: !override\n      - default\n      - main_llm_internal",
            source.split("  main_llm:\n", 1)[1].split("\n\n  minecraft_llm:", 1)[0],
        )
        self.assertEqual(source.count("  minecraft_llm:\n"), 1)
        self.assertIn("container_name: evelyn-p04-qwen-llm", source)
        self.assertIn('"127.0.0.1:9823:9823"', source)
        self.assertIn(
            "networks: !override\n      - default\n      - qwen_admission",
            source.split("  minecraft_llm:\n", 1)[1].split("\n\n  stt:", 1)[0],
        )
        self.assertEqual(source.count("networks: !override"), 2)
        self.assertIn(':/llama:ro', source)
        self.assertEqual(source.count("gpus: !override"), 3)
        self.assertEqual(source.count('device_ids: ["0"]'), 1)
        self.assertEqual(source.count('device_ids: ["1"]'), 2)
        self.assertEqual(source.count("capabilities: [gpu]"), 3)
        self.assertIn('NVIDIA_VISIBLE_DEVICES: "1"', source)
        self.assertIn('CUDA_VISIBLE_DEVICES: "1"', source)
        self.assertIn('HF_HUB_OFFLINE: "1"', source)
        self.assertIn('HF_HUB_DISABLE_IMPLICIT_TOKEN: "1"', source)
        self.assertIn('HF_HOME: "/tmp/huggingface-empty"', source)
        self.assertIn('HF_HUB_CACHE: "/root/.cache/huggingface"', source)
        self.assertIn('HF_TOKEN: ""', source)
        self.assertIn('HUGGING_FACE_HUB_TOKEN: ""', source)
        self.assertIn('TRANSFORMERS_OFFLINE: "1"', source)
        self.assertIn('stt_benchmark_logs:/app/logs', source)
        self.assertIn('/hub:/root/.cache/huggingface:ro', source)
        self.assertIn("container_name: evelyn-p04-stt", source)

        production = COMPOSE.read_text(encoding="utf-8")
        stt_block = production.split("\n  stt:\n", 1)[1].split(
            "\n  codex_gateway:", 1
        )[0]
        for build_arg in (
            "EVELYN_SOURCE_REVISION",
            "EVELYN_STT_DOCKERFILE_SHA256",
            "EVELYN_STT_REQUIREMENTS_SHA256",
        ):
            expected = f'        {build_arg}: "${{{build_arg}:-unversioned}}"'
            self.assertEqual(stt_block.count(expected), 1)

    def test_production_python_qwen_access_is_broker_owned(self) -> None:
        runtime_root = REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core"
        direct = []
        for path in runtime_root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "minecraft_llm:9823" in source or "127.0.0.1:9823" in source:
                direct.append(path.name)
        self.assertEqual(direct, ["mindcraft_llm_broker.py"])
        bridge = (runtime_root / "local_io_bridge.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "http://127.0.0.1:8798/internal/mindcraft-llm/health",
            bridge,
        )
        self.assertNotIn("127.0.0.1:9823", bridge)

    def test_bot_and_discord_use_bounded_specialist_timeout(self) -> None:
        source = COMPOSE.read_text(encoding="utf-8")

        self.assertEqual(
            source.count(
                'SPECIALIST_LLM_TIMEOUT_SEC: "${SPECIALIST_LLM_TIMEOUT_SEC:-6}"'
            ),
            2,
        )

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
        discord_bot = source.split("  discord_bot:\n", 1)[1].split(
            "\n  main_llm:",
            1,
        )[0]
        self.assertIn("stop_signal: SIGINT", discord_bot)
        self.assertIn("stop_grace_period: 30s", discord_bot)
        self.assertIn('TTS_WARMUP_GENERATE_ENABLED: "true"', discord_bot)

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

        vision_runtime = source.split("  vision_runtime:\n", 1)[1].split(
            "\n  vision:\n",
            1,
        )[0]
        vision_ingress = source.split("  vision:\n", 1)[1].split("\nnetworks:", 1)[0]
        for section in (vision_runtime, vision_ingress):
            self.assertIn(
                'EVELYN_SOURCE_REVISION: "${EVELYN_SOURCE_REVISION:-unversioned}"',
                section,
            )

    def test_source_revision_arg_follows_expensive_dependency_layers(self) -> None:
        for name, dependency_marker in (
            ("Dockerfile.bot-api", "pip install --no-cache-dir"),
            ("Dockerfile.control-page", "pip install --no-cache-dir"),
            ("Dockerfile.vision", "pip install -r /tmp/requirements.vision.txt"),
        ):
            with self.subTest(dockerfile=name):
                dockerfile = (DOCKER_DIR / name).read_text(encoding="utf-8")
                self.assertGreater(
                    dockerfile.index("ARG EVELYN_SOURCE_REVISION=unversioned"),
                    dockerfile.index(dependency_marker),
                )
                self.assertIn(
                    "ENV EVELYN_IMAGE_SOURCE_REVISION=${EVELYN_SOURCE_REVISION}",
                    dockerfile,
                )

        vision_ingress = (DOCKER_DIR / "Dockerfile.vision-ingress").read_text(
            encoding="utf-8"
        )
        self.assertIn("ARG EVELYN_SOURCE_REVISION=unversioned", vision_ingress)
        self.assertIn(
            "ENV EVELYN_IMAGE_SOURCE_REVISION=${EVELYN_SOURCE_REVISION}",
            vision_ingress,
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

        self.assertIn(
            'LLM_SERVER_URL: "http://main_llm_gateway:9819/v1/chat/completions"',
            source,
        )
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
        service_manifest = json.loads(
            (
                REPO_ROOT
                / "evelyn_core"
                / "runtime"
                / "service_manifest.json"
            ).read_text(encoding="utf-8")
        )
        tts_service = next(
            item for item in service_manifest["services"] if item["id"] == "tts"
        )
        tts_health_contract = next(
            check["expect_json"]
            for check in tts_service["checks"]
            if check["kind"] == "http"
        )

        self.assertIn('profiles: ["tts"]', tts)
        recipe_tag = _omnivoice_recipe_tag()
        self.assertEqual(recipe_tag, "recipe-e8151492550b")
        self.assertIn(f"image: evelyn-omnivoice-tts:{recipe_tag}", tts)
        self.assertIn("pull_policy: never", tts)
        self.assertIn("dockerfile: docker/Dockerfile.omnivoice", tts)
        self.assertIn("omnivoice_source:", tts)
        self.assertIn('OMNIVOICE_MODEL_ID: "k2-fsa/OmniVoice"', tts)
        self.assertIn(
            'OMNIVOICE_MODEL_REVISION: "c5fdb5ccb189668d56333f77ba2629f4cd7535f4"',
            tts,
        )
        self.assertIn(
            'OMNIVOICE_RUNTIME_REVISION: "omnivoice-0.1.5"',
            tts,
        )
        self.assertIn(
            'OMNIVOICE_FLASHINFER_REVISION: "28bc0889d92110491d726a9c79f26a895db5a074"',
            tts,
        )
        self.assertIn('OMNIVOICE_NUM_STEP: "12"', tts)
        self.assertIn('OMNIVOICE_MAX_CONCURRENT: "1"', tts)
        self.assertIn('OMNIVOICE_FLASHINFER_ENABLED: "true"', tts)
        self.assertIn('OMNIVOICE_FLASHINFER_CUDA_GRAPH: "true"', tts)
        self.assertIn(
            'OMNIVOICE_FLASHINFER_CUDA_GRAPH_BUCKETS: "[2,4,8]"',
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
        self.assertIn(
            "payload.get('runtime_revision') == 'omnivoice-0.1.5'",
            tts,
        )
        self.assertIn(
            "payload.get('flashinfer_revision') == '28bc0889d92110491d726a9c79f26a895db5a074'",
            tts,
        )
        self.assertIn(
            "payload.get('inference_backend') == 'flashinfer_cuda_graph'",
            tts,
        )
        self.assertIn(
            "payload.get('flashinfer_python_version') == '0.6.15.post1'",
            tts,
        )
        self.assertIn(
            "payload.get('flashinfer_jit_cache_version') == '0.6.15.post1+cu129'",
            tts,
        )
        self.assertIn("payload.get('torch_version') == '2.8.0+cu129'", tts)
        self.assertIn("payload.get('torch_cuda_version') == '12.9'", tts)
        self.assertIn("payload.get('flashinfer_jit_disabled') is True", tts)
        self.assertIn(
            "all(type(value) is float for value in payload.get('flashinfer_cuda_graph_buckets'))",
            tts,
        )
        self.assertIn(
            "payload.get('flashinfer_cuda_graph_buckets') == [2.0, 4.0, 8.0]",
            tts,
        )
        self.assertIn("type(payload.get('max_concurrent')) is int", tts)
        self.assertIn("payload.get('max_concurrent') == 1", tts)
        self.assertIn("type(payload.get('num_step')) is int", tts)
        self.assertIn("payload.get('num_step') == 12", tts)
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
        self.assertIn(
            "OMNIVOICE_RUNTIME_REVISION=omnivoice-0.1.5",
            dockerfile,
        )
        self.assertIn(
            "OMNIVOICE_FLASHINFER_REVISION=28bc0889d92110491d726a9c79f26a895db5a074",
            dockerfile,
        )
        self.assertNotIn("ARG OMNIVOICE_FLASHINFER_REVISION", dockerfile)
        self.assertIn('"omnivoice==0.1.5"', dockerfile)
        self.assertIn('"transformers==5.8.1"', dockerfile)
        self.assertIn('"accelerate==1.13.0"', dockerfile)
        self.assertIn("FLASHINFER_DISABLE_JIT=1", dockerfile)
        self.assertIn(
            "FROM nvidia/cuda:12.9.2-base-ubuntu22.04@sha256:8cd34c18c70fcb862f9829e7a2a04597feeb5f5d221904c77610b60c78c00ba4",
            dockerfile,
        )
        self.assertIn("libnpp-12-9=12.4.1.87-1", dockerfile)
        self.assertIn("libpython3.10", dockerfile)
        self.assertIn("torch==2.8.0+cu129", dockerfile)
        self.assertIn("torchaudio==2.8.0+cu129", dockerfile)
        self.assertIn("torchcodec==0.7.0+cu129", dockerfile)
        self.assertIn(
            "28bc0889d92110491d726a9c79f26a895db5a074/omnivoice/models/omnivoice_flashinfer.py",
            dockerfile,
        )
        self.assertIn(
            "7568e042e614b890b2e3fffa8296a2c9a44fdc1a95bc748063facf307cd3cdb1",
            dockerfile,
        )
        self.assertIn("/licenses/OmniVoice.LICENSE", dockerfile)
        self.assertIn("flashinfer_python-0.6.15.post1", dockerfile)
        self.assertIn(
            "f2419fd2b77c2705816e8d0a31c784c6456b17f373f8494b5cfc3bdf434d5c44",
            dockerfile,
        )
        self.assertIn("flashinfer_cubin-0.6.15.post1", dockerfile)
        self.assertIn(
            "25cfd305afa1f34baa2f419bca35db58c369c534ee1961662e83d9fe858ce021",
            dockerfile,
        )
        self.assertIn("flashinfer_jit_cache-0.6.15.post1%2Bcu129", dockerfile)
        self.assertIn(
            "d8f8c4c42945bb687d176e8e05dc732b5eb72ce610acb2b219c7f2c03fcfaa51",
            dockerfile,
        )
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
        self.assertIn("OMNIVOICE_RUNTIME_REVISION", entrypoint)
        self.assertIn("OMNIVOICE_FLASHINFER_REVISION", entrypoint)
        self.assertIn(
            'expected_model_revision="c5fdb5ccb189668d56333f77ba2629f4cd7535f4"',
            entrypoint,
        )
        self.assertIn(
            '"${OMNIVOICE_MODEL_REVISION:-}" != "${expected_model_revision}"',
            entrypoint,
        )
        self.assertIn(
            '"${OMNIVOICE_RUNTIME_REVISION:-}" != "${expected_runtime_revision}"',
            entrypoint,
        )
        self.assertIn(
            '"${OMNIVOICE_FLASHINFER_REVISION:-}" != "${expected_flashinfer_revision}"',
            entrypoint,
        )
        for expected in (
            '"${FLASHINFER_DISABLE_JIT:-}" != "1"',
            '"${OMNIVOICE_MAX_CONCURRENT:-}" != "1"',
            '"${OMNIVOICE_NUM_STEP:-}" != "12"',
            '"${OMNIVOICE_FLASHINFER_ENABLED:-}" != "true"',
            '"${OMNIVOICE_FLASHINFER_CUDA_GRAPH:-}" != "true"',
            '"${OMNIVOICE_FLASHINFER_CUDA_GRAPH_BUCKETS:-}" != "[2,4,8]"',
            '"${OMNIVOICE_FLASHINFER_CUDA_GRAPH_OVERHEAD_BUDGET:-}" != "512"',
        ):
            self.assertIn(expected, entrypoint)
        self.assertIn(
            "sha256sum --check --strict /opt/omnivoice-server/omnivoice_model.sha256",
            entrypoint,
        )
        self.assertIn("exit 78", entrypoint)
        self.assertEqual(tts_service["launcher"], "../start_tts.bat")
        self.assertEqual(
            tts_health_contract,
            {
                "status": "healthy",
                "ready": True,
                "model_loaded": True,
                "model_id": "k2-fsa/OmniVoice",
                "model_revision": "c5fdb5ccb189668d56333f77ba2629f4cd7535f4",
                "runtime_revision": "omnivoice-0.1.5",
                "flashinfer_revision": "28bc0889d92110491d726a9c79f26a895db5a074",
                "inference_backend": "flashinfer_cuda_graph",
                "flashinfer_python_version": "0.6.15.post1",
                "flashinfer_jit_cache_version": "0.6.15.post1+cu129",
                "torch_version": "2.8.0+cu129",
                "torch_cuda_version": "12.9",
                "flashinfer_jit_disabled": True,
                "flashinfer_cuda_graph_buckets": [2.0, 4.0, 8.0],
                "max_concurrent": 1,
                "num_step": 12,
            },
        )
        self.assertIs(type(tts_health_contract["flashinfer_jit_disabled"]), bool)
        self.assertIs(type(tts_health_contract["max_concurrent"]), int)
        self.assertIs(type(tts_health_contract["num_step"]), int)
        self.assertTrue(
            all(
                type(value) is float
                for value in tts_health_contract["flashinfer_cuda_graph_buckets"]
            )
        )

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
        discord_bot = source.split("  discord_bot:\n", 1)[1].split(
            "\n  main_llm:",
            1,
        )[0]

        self.assertNotIn("restart: unless-stopped", source)
        self.assertNotIn("restart: always", source)
        self.assertIn('restart: "on-failure:3"', discord_bot)
        self.assertEqual(source.count('restart: "on-failure:3"'), 1)
        self.assertEqual(source.count('restart: "no"'), 14)

    def test_bot_api_stop_budget_exceeds_artifact_fence_grace(self) -> None:
        source = COMPOSE.read_text(encoding="utf-8")
        bot_api = source.split("  bot_api:\n", 1)[1].split(
            "\n  control_page:",
            1,
        )[0]

        match = re.search(r"stop_grace_period: (\d+)s", bot_api)
        self.assertIsNotNone(match)
        self.assertGreater(int(match.group(1)), 120)
        self.assertEqual(int(match.group(1)), 130)

    def test_mindcraft_persists_microsoft_account_profile_cache(self) -> None:
        source = COMPOSE.read_text(encoding="utf-8")
        voyager = source.split("  voyager:\n", 1)[1]

        self.assertIn("- ./bot_profiles:/app/bot_profiles", voyager)
        self.assertNotIn("bot_memory/mindcraft", voyager)
        self.assertIn('MINEFLAYER_PROFILES_FOLDER: "/app/bot_profiles"', voyager)
        self.assertIn('MINEFLAYER_AUTH: "microsoft"', voyager)
        self.assertIn('MINECRAFT_VERSION: "1.21.11"', voyager)
        self.assertIn('MINEFLAYER_AUTH", "microsoft"', (REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "config.py").read_text(encoding="utf-8"))
        self.assertIn('MINEFLAYER_USERNAME", "Evelyn_0428"', (REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "config.py").read_text(encoding="utf-8"))
        self.assertIn('MINEFLAYER_USERNAME=Evelyn_0428', (REPO_ROOT / "evelyn_core" / "start_env.bat").read_text(encoding="utf-8"))

    def test_mindcraft_defaults_to_local_planner_without_codex_gateway(self) -> None:
        source = COMPOSE.read_text(encoding="utf-8")
        voyager = source.split("  voyager:\n", 1)[1].split("\nvolumes:", 1)[0]

        self.assertIn('MINDCRAFT_CODEX_ENABLED: "false"', voyager)
        self.assertNotIn("codex_gateway:", voyager)
        self.assertNotIn("codex_gateway_token:/gateway-token", voyager)
        self.assertIn('MINDCRAFT_ENABLE_SKIN_COMMANDS: "false"', voyager)
        self.assertIn('dockerfile: docker/Dockerfile.mindcraft', voyager)
        self.assertIn('evelyn_core.mindcraft_service', voyager)
        self.assertIn('["CMD", "python3", "-c"', voyager)

    def test_mindcraft_uses_fixed_bot_api_broker_for_local_llms(self) -> None:
        source = COMPOSE.read_text(encoding="utf-8")
        bot_api = source.split("  bot_api:\n", 1)[1].split(
            "\n  control_page:",
            1,
        )[0]
        voyager = source.split("  voyager:\n", 1)[1].split("\nvolumes:", 1)[0]
        minecraft_llm = source.split("\n  minecraft_llm:\n", 1)[1].split("\n  sub_llm:", 1)[0]

        self.assertIn('profiles: ["llm", "voyager"]', source)
        self.assertIn('container_name: evelyn-minecraft-llm', minecraft_llm)
        self.assertIn('profiles: ["llm", "voyager"]', minecraft_llm)
        self.assertIn('/llama/models/qwen3-14b/Qwen3-14B-Q4_K_M.gguf', minecraft_llm)
        self.assertIn('-c 6144', minecraft_llm)
        self.assertIn('-np 1', minecraft_llm)
        self.assertIn('--cache-type-k q8_0', minecraft_llm)
        self.assertIn('--cache-type-v q8_0', minecraft_llm)
        self.assertIn('NVIDIA_VISIBLE_DEVICES: "1"', minecraft_llm)
        self.assertIn('CUDA_VISIBLE_DEVICES: "1"', minecraft_llm)
        self.assertIn('LLAMA_CHAT_TEMPLATE_KWARGS: \'{"enable_thinking":false}\'', minecraft_llm)
        self.assertIn('MINDCRAFT_LOCAL_LLM_URL: "http://minecraft_llm:9823/v1/chat/completions"', bot_api)
        self.assertIn('MINDCRAFT_LOCAL_MODEL: "Qwen3-14B-Q4_K_M.gguf"', bot_api)
        self.assertIn(
            'MINECRAFT_CONNECT_READY_TIMEOUT_SEC: "${MINECRAFT_CONNECT_READY_TIMEOUT_SEC:-60}"',
            bot_api,
        )
        discord_bot = source.split("  discord_bot:\n", 1)[1].split("\n  main_llm:", 1)[0]
        self.assertNotIn("MINDCRAFT_LOCAL_LLM_URL", discord_bot)
        self.assertIn(
            'MINDCRAFT_LLM_BROKER_URL: "http://bot_api:8798/internal/mindcraft-llm"',
            discord_bot,
        )
        self.assertIn(
            'MINDCRAFT_LLM_BROKER_TOKEN_FILE: "/mindcraft-llm-broker/token"',
            discord_bot,
        )
        self.assertIn(
            '- mindcraft_llm_broker_token:/mindcraft-llm-broker:ro',
            discord_bot,
        )
        self.assertIn('MINDCRAFT_LOCAL_MODEL: "Qwen3-14B-Q4_K_M.gguf"', discord_bot)
        self.assertIn('minecraft_llm:\n        condition: service_healthy', discord_bot)
        self.assertIn('MINDCRAFT_ROUTER_URL: "http://router_llm:9822/v1/chat/completions"', bot_api)
        self.assertIn('MINDCRAFT_ROUTER_MODEL: "gemma-4-E2B-it-Q4_K_M.gguf"', bot_api)
        self.assertIn('MINDCRAFT_LLM_BROKER_TOKEN_FILE: "/mindcraft-llm-broker/token"', bot_api)
        self.assertIn('MINDCRAFT_QWEN_EPOCH_FILE: "/qwen-admission/epoch"', bot_api)
        self.assertIn(
            'MINDCRAFT_LLM_BROKER_URL: "http://127.0.0.1:8798/internal/mindcraft-llm"',
            bot_api,
        )
        self.assertIn('- mindcraft_llm_broker_token:/mindcraft-llm-broker', bot_api)
        self.assertNotIn('mindcraft_llm_broker_token:/mindcraft-llm-broker:ro', bot_api)
        self.assertIn('- qwen_admission_epoch:/qwen-admission:ro', bot_api)
        self.assertIn('MINDCRAFT_LLM_BROKER_URL: "http://bot_api:8798/internal/mindcraft-llm"', voyager)
        self.assertIn('MINDCRAFT_LLM_BROKER_TOKEN_FILE: "/mindcraft-llm-broker/token"', voyager)
        self.assertIn('- mindcraft_llm_broker_token:/mindcraft-llm-broker:ro', voyager)
        self.assertNotIn("MINDCRAFT_LOCAL_", voyager)
        self.assertNotIn("MINDCRAFT_ROUTER_", voyager)
        self.assertNotIn("VOYAGER_CODEX_GATEWAY_TOKEN_FILE", voyager)
        self.assertNotIn("codex_gateway_token:/gateway-token", voyager)
        self.assertIn('MINDCRAFT_CODEX_ENABLED: "false"', voyager)
        self.assertNotIn("MINDCRAFT_PLANNER_STATE_PATH", voyager)
        self.assertIn('MINDCRAFT_DETERMINISTIC_TOOL_BOOTSTRAP: "false"', voyager)
        self.assertIn('bot_api:\n        condition: service_healthy', voyager)
        self.assertIn('minecraft_llm:', voyager)
        self.assertIn('router_llm:', voyager)
        self.assertEqual(
            source.count(
                'MINDCRAFT_LOCAL_LLM_URL: "http://minecraft_llm:9823/v1/chat/completions"'
            ),
            1,
        )
        self.assertIn(
            "networks:\n      - default\n      - main_llm_admission\n      - qwen_admission",
            bot_api,
        )
        self.assertIn("expose:\n      - \"9823\"", minecraft_llm)
        self.assertIn("networks:\n      - qwen_admission", minecraft_llm)
        self.assertIn('- qwen_admission_epoch:/qwen-admission', minecraft_llm)
        self.assertIn(
            "depends_on:\n      bot_api:\n        condition: service_started\n        restart: true",
            minecraft_llm,
        )
        self.assertNotIn("condition: service_healthy", minecraft_llm.split("command:", 1)[0])
        self.assertIn("set -e;", minecraft_llm)
        self.assertIn("cat /proc/sys/kernel/random/uuid > /qwen-admission/epoch.tmp", minecraft_llm)
        self.assertIn("sync /qwen-admission/epoch.tmp", minecraft_llm)
        self.assertIn("mv -f /qwen-admission/epoch.tmp /qwen-admission/epoch", minecraft_llm)
        self.assertNotIn('"127.0.0.1:9823:9823"', minecraft_llm)
        self.assertIn("qwen_admission:\n    internal: true", source)
        self.assertIn('\n  mindcraft_llm_broker_token:\n', source)
        self.assertIn('\n  qwen_admission_epoch:\n', source)
        self.assertIn("GET /health HTTP/1.0", minecraft_llm)
        router_llm = source.split("  router_llm:\n", 1)[1].split("\n  minecraft_llm:", 1)[0]
        self.assertIn("GET /health HTTP/1.0", router_llm)

    def test_mindcraft_image_applies_pinned_evelyn_overlay(self) -> None:
        dockerfile = (DOCKER_DIR / "Dockerfile.mindcraft").read_text(encoding="utf-8")
        package = (REPO_ROOT / "external" / "mindcraft_evelyn" / "package.json").read_text(encoding="utf-8")
        patch = (REPO_ROOT / "external" / "mindcraft_evelyn" / "evelyn.patch").read_text(encoding="utf-8")

        self.assertIn("FROM node:22-bookworm-slim", dockerfile)
        self.assertIn("sed -i 's/\\r$//'", dockerfile)
        self.assertIn("src/agent/commands/index.js", dockerfile)
        self.assertIn("external/mindcraft", dockerfile)
        self.assertIn("patch -p1", dockerfile)
        self.assertIn("      main.js \\", dockerfile)
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

    def test_codex_gateway_stays_unavailable_until_tool_access_is_verified(self) -> None:
        source = CODEX_GATEWAY.read_text(encoding="utf-8")
        compose = COMPOSE.read_text(encoding="utf-8")
        service = compose.split("  codex_gateway:\n", 1)[1].split("\n  voyager:\n", 1)[0]
        check_script = CHECK_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("npm install -g @openai/codex@0.128.0", (DOCKER_DIR / "Dockerfile.voyager").read_text(encoding="utf-8"))
        self.assertIn(
            "${EVELYN_CODEX_CREDENTIALS_DIR:-./runtime_artifacts/secrets/codex_device_home}"
            "/auth.json:/run/secrets/evelyn-codex/auth.json:ro",
            service,
        )
        self.assertNotIn("${USERPROFILE}/.codex/auth.json", service)
        self.assertIn('VOYAGER_CODEX_GATEWAY_WORKDIR: "/workspace"', service)
        self.assertIn('EVELYN_CODEX_GATEWAY_ISOLATED_RUNTIME: "true"', service)
        self.assertIn('EVELYN_CODEX_GATEWAY_TOOLLESS_RUNTIME_VERIFIED: "false"', service)
        self.assertIn('profiles: ["codex-gateway"]', service)
        self.assertIn('EVELYN_RUNTIME_ARTIFACTS_DIR: "/gateway-state"', service)
        self.assertIn('CODEX_HOME: "/tmp/evelyn-codex-home"', service)
        self.assertIn('HOME: "/tmp/evelyn-codex-home"', service)
        self.assertIn("read_only: true", service)
        self.assertIn("no-new-privileges:true", service)
        self.assertIn("/workspace:mode=0700", service)
        self.assertIn("/gateway-state:mode=0700", service)
        self.assertNotIn("./runtime_artifacts:/app/runtime_artifacts", service)
        self.assertNotIn("./logs:/app/logs", service)
        self.assertNotIn("bot_memory", service)
        self.assertIn("codex_gateway_token:/gateway-token", service)
        self.assertIn("backendReady", source)
        self.assertIn("isolatedRuntime", source)
        self.assertIn("toolAccessVerified", source)
        self.assertIn("codex_toolless_runtime_unverified", source)
        self.assertIn("lastActionReady", source)
        self.assertIn("actionAuthRequired", source)
        self.assertIn("gateway_request_authorized", source)
        self.assertIn("gateway_auth_headers", (REPO_ROOT / "third_party" / "Voyager" / "voyager" / "agents" / "codex_gateway_llm.py").read_text(encoding="utf-8"))
        self.assertIn("Authorization = \"Bearer $token\"", check_script)
        self.assertIn("docker exec evelyn-codex-gateway", check_script)
        self.assertNotIn('cwd = "/app"', check_script)
        self.assertIn("_backend_readiness", source)
        self.assertIn('"exec",\n        "-m",\n        model,', source)
        self.assertIn('"--ephemeral"', source)
        self.assertIn('"features.shell_tool=false"', source)
        self.assertIn('"features.unified_exec=false"', source)
        self.assertIn('"features.apps=false"', source)
        self.assertIn('"features.multi_agent=false"', source)
        self.assertIn("$json.backendReady -eq $true", check_script)
        self.assertIn("$json.isolatedRuntime -eq $true", check_script)
        self.assertIn("IncludeCodexAction", check_script)
        for client_path in (
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "codex_gateway_client.py",
            REPO_ROOT / "evelyn_core" / "runtime" / "evelyn_core" / "minecraft_autonomy_client.py",
        ):
            client_source = client_path.read_text(encoding="utf-8")
            self.assertNotIn('"evelyn_core.codex_gateway_server"', client_source)
            self.assertIn("codex_gateway_isolated_runtime_required", client_source)
            self.assertIn('get("isolatedRuntime")', client_source)
            self.assertIn('get("toolAccessVerified")', client_source)

    def test_runtime_checker_matches_deferred_minecraft_contract(self) -> None:
        check_script = CHECK_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("[switch]$IncludeMinecraftStack", check_script)
        self.assertIn(
            'if ($IncludeMinecraftStack)',
            check_script,
        )
        self.assertIn(
            'if ($IncludeCodexAction)',
            check_script,
        )
        self.assertIn('$script:ComposeProfiles += "voyager"', check_script)
        self.assertIn('$script:ComposeProfiles += "codex-gateway"', check_script)
        self.assertIn(
            '$env:DISCORD_BOT_TOKEN = "runtime-check-disabled"',
            check_script,
        )
        self.assertIn(
            "Voyager health check skipped; use -IncludeMinecraftStack",
            check_script,
        )
        self.assertIn(
            "Codex Gateway health check skipped; use -IncludeCodexAction",
            check_script,
        )
        self.assertIn(
            'Add-Failure "$flag is not true for the requested Minecraft stack"',
            check_script,
        )
        self.assertIn('foreach ($flag in @("voyagerReady"))', check_script)
        self.assertEqual(
            check_script.count(
                'foreach ($flag in @("voyagerReady", "codexReady"))'
            ),
            1,
        )
        self.assertIn(
            'codexReady=false or unavailable (not required for the local Minecraft stack)',
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
            "EVELYN_MAIN_LLM_BUILD_DIR",
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
            "  vision_isolated:\n    internal: true",
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
        self.assertIn(f"evelyn-omnivoice-tts:{_omnivoice_recipe_tag()}", docker_helper)
        self.assertIn("if ($buildEnabled -or $ttsImageMissing)", docker_helper)
        self.assertIn("build_local_docker_images.ps1", docker_helper)
        self.assertIn("-Services $pathSafeBuildServices", docker_helper)
        self.assertIn("$serviceArgs | Where-Object", docker_helper)
        self.assertIn("$normalizedServices -contains 'codex_gateway'", docker_helper)
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
        self.assertIn("start_docker_compose_services.ps1", native_gateway)
        self.assertIn("-Services codex_gateway", native_gateway)
        self.assertNotIn("evelyn_core.codex_gateway_server", native_gateway)

    def test_individual_service_batches_do_not_start_host_processes_by_default(self) -> None:
        service_batches = {
            "start_main_llm.bat": ("-Profiles llm", "-Services main_llm"),
            "start_router_llm.bat": ("-Profiles llm", "-Services router_llm"),
            "start_sub_llm.bat": ("-Profiles llm", "-Services sub_llm"),
            "start_tts.bat": ("-Profiles tts", "-Services tts"),
            "start_vision.bat": ("-Profiles vision", "-Services vision"),
            "start_codex_gateway.bat": ("-Profiles codex-gateway", "-Services codex_gateway"),
            "start_voyager_service.bat": ("-Profiles voyager", "-Services voyager"),
            "start_voyager.bat": ("-Profiles voyager", "-Services router_llm,minecraft_llm,voyager"),
            "start_bot.bat": ("-Profiles llm,tts,stt,vision,voyager,discord", "-Services discord_bot"),
        }

        for name, expected in service_batches.items():
            with self.subTest(batch=name):
                source = (REPO_ROOT / "evelyn_core" / name).read_text(encoding="utf-8")
                self.assertIn("start_docker_compose_services.ps1", source)
                self.assertIn(expected[0], source)
                self.assertIn(expected[1], source)
                if name == "start_codex_gateway.bat":
                    self.assertNotIn("evelyn_core.codex_gateway_server", source)
                else:
                    self.assertIn("EVELYN_ALLOW_LEGACY_HOST_START", source)


if __name__ == "__main__":
    unittest.main()
