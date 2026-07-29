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

    def test_cuda_128_torch_families_are_version_aligned(self) -> None:
        stt = (REPO_ROOT / "docker" / "Dockerfile.stt").read_text(
            encoding="utf-8"
        )
        vision = (REPO_ROOT / "docker" / "Dockerfile.vision").read_text(
            encoding="utf-8"
        )
        self.assertIn("torch==2.11.0+cu128", stt)
        self.assertIn("torchaudio==2.11.0+cu128", stt)
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
