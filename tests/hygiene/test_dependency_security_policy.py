from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)


def locked_version(path: Path, package: str) -> str:
    pattern = re.compile(rf"^{re.escape(package)}==([^\s]+)$", re.MULTILINE)
    match = pattern.search(path.read_text(encoding="utf-8"))
    if match is None:
        raise AssertionError(f"{package} is not pinned in {path}")
    return match.group(1)


class DependencySecurityPolicyTests(unittest.TestCase):
    def test_stt_image_context_is_source_only(self) -> None:
        lines = (
            REPO_ROOT / "docker" / "Dockerfile.stt.dockerignore"
        ).read_text(encoding="utf-8").splitlines()

        self.assertEqual(
            [line for line in lines if line],
            [
                "*",
                "!docker/",
                "docker/*",
                "!docker/Dockerfile.stt",
                "!docker/requirements.stt.txt",
                "!evelyn_core/",
                "evelyn_core/*",
                "!evelyn_core/runtime/",
                "evelyn_core/runtime/*",
                "!evelyn_core/runtime/evelyn_core/",
                "!evelyn_core/runtime/evelyn_core/**",
                "**/__pycache__/",
                "**/*.pyc",
                "**/*.pyo",
            ],
        )

        self.assertLess(lines.index("docker/*"), lines.index("!docker/Dockerfile.stt"))
        self.assertLess(lines.index("evelyn_core/*"), lines.index("!evelyn_core/runtime/"))
        self.assertLess(
            lines.index("evelyn_core/runtime/*"),
            lines.index("!evelyn_core/runtime/evelyn_core/"),
        )
        self.assertIn(
            "COPY evelyn_core/runtime/evelyn_core /app/evelyn_core/runtime/evelyn_core",
            (REPO_ROOT / "docker" / "Dockerfile.stt").read_text(encoding="utf-8"),
        )

    def test_docker_context_excludes_python_bytecode_after_reincludes(self) -> None:
        lines = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        last_include = max(index for index, line in enumerate(lines) if line.startswith("!"))

        self.assertGreater(
            lines.index("evelyn_core/evelin/"),
            lines.index("!evelyn_core/**"),
        )
        for pattern in ("**/__pycache__/", "**/*.pyc", "**/*.pyo"):
            self.assertGreater(lines.index(pattern), last_include)

    def test_microsoft_auth_caches_are_excluded_from_git_and_docker(self) -> None:
        git_required = {
            "external/mindcraft/_tmp_ms_profiles/",
            "external/mindcraft_evelyn/tmp-ms-profile-*/",
            "tmp-ms-profile-*/",
        }
        docker_required = {
            "external/mindcraft/keys.json",
            "external/mindcraft/_tmp_ms_profiles/",
            "external/mindcraft/bots/",
            "external/mindcraft/code_records/",
            "external/mindcraft/experiments/",
            "external/mindcraft/node_modules*/",
            "external/mindcraft/results/",
            "external/mindcraft/server_data*/",
            "external/mindcraft/services/viaproxy/jars/",
            "external/mindcraft/services/viaproxy/logs/",
            "external/mindcraft/services/viaproxy/plugins/",
            "external/mindcraft/services/viaproxy/ViaLoader/",
            "external/mindcraft/services/viaproxy/saves.json",
            "external/mindcraft/services/viaproxy/viaproxy.yml",
            "external/mindcraft/wandb/",
            "external/mindcraft/andy_*.json",
            "external/mindcraft/jill_*.json",
            "external/mindcraft/temp_*",
            "external/mindcraft_evelyn/node_modules/",
            "external/mindcraft_evelyn/tmp-ms-profile-*/",
            "external/mindcraft_evelyn/*-ms-code.mjs",
            "tmp-ms-profile-*/",
        }
        gitignore = set(
            (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        )
        dockerignore_lines = (
            (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        )
        dockerignore = set(dockerignore_lines)
        self.assertTrue(git_required.issubset(gitignore))
        self.assertTrue(docker_required.issubset(dockerignore))
        last_external_include = max(
            index
            for index, line in enumerate(dockerignore_lines)
            if line.startswith("!external/")
        )
        for pattern in docker_required:
            if pattern.startswith("external/"):
                self.assertGreater(
                    dockerignore_lines.index(pattern),
                    last_external_include,
                )
        mindcraft_overlay_patch = set(
            (REPO_ROOT / "external" / "mindcraft_evelyn" / "evelyn.patch")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        self.assertIn("+_tmp_ms_profiles/", mindcraft_overlay_patch)
        mindcraft_base_ignore = set(
            (REPO_ROOT / "external" / "mindcraft" / ".gitignore")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        self.assertIn("keys.json", mindcraft_base_ignore)

    def test_root_torch_is_on_patched_release(self) -> None:
        lock = REPO_ROOT / "requirements.lock"
        self.assertEqual(locked_version(lock, "torch"), "2.13.0")
        workflow = (
            REPO_ROOT / ".github" / "workflows" / "verify-evelyn.yml"
        ).read_text(encoding="utf-8")
        self.assertNotIn("--ignore-vuln PYSEC-2025-194", workflow)

    def test_qwen_asr_transformers_constraint_is_explicit(self) -> None:
        requirements = (REPO_ROOT / "requirements.txt").read_text(
            encoding="utf-8"
        )
        stt_requirements = (
            REPO_ROOT / "docker" / "requirements.stt.txt"
        ).read_text(encoding="utf-8")
        self.assertIn("qwen-asr==0.0.6", requirements)
        self.assertIn("transformers==4.57.6", requirements)
        self.assertIn("qwen-asr==0.0.6", stt_requirements)

        expected_direct = {
            "fastapi==0.139.0",
            "numpy==2.2.6",
            "pydantic==2.13.4",
            "qwen-asr==0.0.6",
            "vllm==0.14.0",
            "soxr==1.1.0",
            "uvicorn==0.51.0",
        }
        self.assertEqual(set(stt_requirements.splitlines()), expected_direct)
        self.assertFalse(any(">=" in line for line in stt_requirements.splitlines()))

    def test_cuda_128_torch_families_are_version_aligned(self) -> None:
        stt = (REPO_ROOT / "docker" / "Dockerfile.stt").read_text(
            encoding="utf-8"
        )
        stt_requirements = (
            REPO_ROOT / "docker" / "requirements.stt.txt"
        ).read_text(encoding="utf-8")
        vision = (REPO_ROOT / "docker" / "Dockerfile.vision").read_text(
            encoding="utf-8"
        )
        self.assertIn("torch==2.9.1+cu128", stt)
        self.assertIn("pip==26.2.1", stt)
        self.assertIn("torchvision==0.24.1+cu128", stt)
        self.assertIn("pip check", stt)
        self.assertIn("pip freeze --all", stt)
        self.assertEqual(stt.count(" AS stt-builder"), 1)
        self.assertIn("COPY --from=stt-builder /opt/venv /opt/venv", stt)
        for build_arg in (
            "EVELYN_SOURCE_REVISION",
            "EVELYN_STT_DOCKERFILE_SHA256",
            "EVELYN_STT_REQUIREMENTS_SHA256",
        ):
            self.assertIn(f"ARG {build_arg}=unversioned", stt)
        for label in (
            "org.opencontainers.image.revision",
            "org.opencontainers.image.base.digest",
            "io.evelyn.stt.dockerfile-sha256",
            "io.evelyn.stt.requirements-sha256",
        ):
            self.assertIn(label, stt)
        runtime_stage = stt.split("\nFROM nvidia/cuda:", 2)[-1]
        self.assertNotIn("build-essential", runtime_stage)
        self.assertNotIn(" git ", runtime_stage)
        self.assertNotIn(" curl ", runtime_stage)
        self.assertIn(
            "nvidia/cuda:12.8.0-cudnn-runtime-ubuntu22.04@sha256:"
            "05de765c12d993316f770e8e4396b9516afe38b7c52189bce2d5b64ef812db58",
            stt,
        )
        self.assertIn("vllm==0.14.0", stt_requirements)
        self.assertIn("torchaudio==2.9.1+cu128", stt)
        self.assertIn("torch==2.11.0+cu128", vision)
        self.assertIn("torchvision==0.26.0+cu128", vision)
        self.assertIn(
            "transformers==5.14.1",
            (
                REPO_ROOT / "docker" / "requirements.vision.txt"
            ).read_text(encoding="utf-8"),
        )

    def test_mineflayer_direct_runtime_is_current_and_not_overridden(self) -> None:
        root_manifest = json.loads(
            (REPO_ROOT / "package.json").read_text(encoding="utf-8")
        )
        mindcraft_manifest = json.loads(
            (
                REPO_ROOT / "external" / "mindcraft_evelyn" / "package.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            root_manifest["dependencies"]["mineflayer"],
            "4.37.1",
        )
        self.assertEqual(
            mindcraft_manifest["dependencies"]["mineflayer"],
            "4.37.1",
        )
        self.assertNotIn("overrides", root_manifest)
        self.assertNotIn("overrides", mindcraft_manifest)


if __name__ == "__main__":
    unittest.main()
