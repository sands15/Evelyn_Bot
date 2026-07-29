from __future__ import annotations

from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]


class VoxCPMExactLatentContractTests(unittest.TestCase):
    def test_image_applies_version_pinned_exact_latent_patch(self) -> None:
        dockerfile = (REPO_ROOT / "docker" / "Dockerfile.voxcpm").read_text(encoding="utf-8")
        self.assertIn("ARG NANOVLLM_VOXCPM_REF=v2.0.3", dockerfile)
        self.assertIn("nanovllm_voxcpm_exact_latent.patch", dockerfile)
        self.assertIn("git -C /opt/nanovllm-voxcpm apply --check", dockerfile)

    def test_upstream_patch_preserves_default_api_and_exposes_opt_in_latents(self) -> None:
        patch = (REPO_ROOT / "docker" / "nanovllm_voxcpm_exact_latent.patch").read_text(
            encoding="utf-8"
        )
        self.assertIn("include_latents: bool = False", patch)
        self.assertIn('\"waveform\": latest_waveform, \"latents\": latest_latents', patch)
        self.assertIn('yield data[\"waveform\"], data[\"latents\"]', patch)
        self.assertIn('yield data[\"waveform\"]', patch)

    def test_upstream_patch_exposes_whole_latent_decode(self) -> None:
        patch = (REPO_ROOT / "docker" / "nanovllm_voxcpm_exact_latent.patch").read_text(
            encoding="utf-8"
        )
        self.assertIn("def decode_latents(self, latents: np.ndarray)", patch)
        self.assertIn("self.vae.decode(latent_tensor)[0, 0]", patch)
        self.assertIn("def decode_latents(self, latent_bytes: bytes)", patch)
        self.assertIn('await self.submit("decode_latents", latent_bytes)', patch)

    def test_service_uses_exact_latents_without_waveform_reencoding(self) -> None:
        service = (REPO_ROOT / "docker" / "voxcpm_nanovllm_service.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"include_latents": True', service)
        self.assertIn("_join_exact_latents(runtime, exact_latent_patches)", service)
        self.assertIn('"model_exact"', service)
        self.assertIn('"model_exact_no_terminal"', service)
        self.assertIn('"waveform_reencode"', service)
        self.assertIn('"continuation_latent_source": continuation_source', service)
        self.assertIn('DEFAULT_CONTINUATION_SOURCE = os.getenv(', service)
        self.assertIn('"model_exact_no_terminal",', service)
        self.assertIn("if len(exact_latent_patches) > 1", service)
        self.assertIn("SHORT_FULL_DECODE_MAX_CHARS", service)
        self.assertIn("_generate_buffered_segment(", service)
        self.assertIn("await runtime.server.decode_latents(", service)
        self.assertIn('"short_decode_mode": "full_latent"', service)
        self.assertIn('"X-TTS-Decode-Mode"', service)
        self.assertIn('DEFAULT_CONTROL_INSTRUCTION = os.getenv(', service)
        self.assertIn(
            "target_text = _apply_default_control_instruction(text) if history is None else text",
            service,
        )
        self.assertIn('"default_control_instruction": DEFAULT_CONTROL_INSTRUCTION', service)
        self.assertIn('if message_type == "commit":', service)
        self.assertIn('"Cannot commit after flush"', service)
        self.assertIn("for segment in committer.flush():", service)

        compose = (REPO_ROOT / "docker-compose.fast-control.yml").read_text(encoding="utf-8")
        self.assertIn('VOXCPM_CONTINUATION_SOURCE: "model_exact_no_terminal"', compose)
        self.assertIn('VOXCPM_SHORT_FULL_DECODE_MAX_CHARS: "32"', compose)
        self.assertIn('VOXCPM_SHORT_QUALITY_RETRIES: "1"', compose)
        self.assertIn(
            'VOXCPM_DEFAULT_CONTROL_INSTRUCTION: "warm, natural and expressive tone, clear pronunciation, stable pace"',
            compose,
        )

    def test_service_validates_each_exact_patch_shape(self) -> None:
        service = (REPO_ROOT / "docker" / "voxcpm_nanovllm_service.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("runtime.patch_size * runtime.feat_dim", service)
        self.assertIn("Invalid exact latent patch size", service)


if __name__ == "__main__":
    unittest.main()
