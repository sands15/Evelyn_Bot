"""Exercise the installed security backports without downloading models or code."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class TransformersSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from transformers import PreTrainedTokenizerFast, ProcessorMixin
            from transformers.generation import utils
            from tokenizers import Tokenizer
            from tokenizers.models import WordLevel
        except ImportError:
            if os.environ.get("EVELYN_REQUIRE_PATCHED_DEPS") == "1":
                raise
            raise unittest.SkipTest("Transformers runtime is not installed")
        cls.tokenizer_type = PreTrainedTokenizerFast
        cls.tokenizer_backend = Tokenizer
        cls.word_level = WordLevel
        cls.generation = utils

        class OwnedProcessor(ProcessorMixin):
            attributes = []

        cls.processor_type = OwnedProcessor

    def tokenizer(self, templates):
        return self.tokenizer_type(
            tokenizer_object=self.tokenizer_backend(self.word_level({"[UNK]": 0}, unk_token="[UNK]")),
            unk_token="[UNK]", chat_template=templates,
        )

    def test_both_template_writers_reject_escape_without_outside_write(self):
        for kind in ("tokenizer", "processor"):
            for form in ("relative", "absolute"):
                with self.subTest(kind=kind, form=form), tempfile.TemporaryDirectory() as folder:
                    root = Path(folder)
                    destination = root / "save"
                    escaped = root / "outside.jinja"
                    name = "../../outside" if form == "relative" else str(root / "outside")
                    templates = {"default": "safe", name: "untrusted"}
                    target = self.tokenizer(templates) if kind == "tokenizer" else self.processor_type(chat_template=templates)
                    with self.assertRaises(ValueError):
                        target.save_pretrained(destination)
                    self.assertFalse(escaped.exists())

    def test_both_template_writers_preserve_normal_templates(self):
        for kind in ("tokenizer", "processor"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                target = self.tokenizer({"default": "safe", "named": "named-safe"}) if kind == "tokenizer" else self.processor_type(chat_template={"default": "safe", "named": "named-safe"})
                target.save_pretrained(folder)
                self.assertEqual((Path(folder) / "chat_template.jinja").read_text(), "safe")
                self.assertEqual((Path(folder) / "additional_chat_templates" / "named.jinja").read_text(), "named-safe")

    def test_declined_remote_code_does_not_create_cache_file(self):
        for consent in (False, None):
            with self.subTest(consent=consent), tempfile.TemporaryDirectory() as folder:
                marker = Path(folder) / "downloaded.py"

                def download(*args, **kwargs):
                    marker.write_text("# synthetic remote boundary\n")
                    return "owned/module.py"

                with patch.object(self.generation, "has_file", return_value=True, create=True), patch.object(
                    self.generation, "get_cached_module_file", side_effect=download
                ), patch("transformers.dynamic_module_utils.TIME_OUT_REMOTE_CODE", 0):
                    with self.assertRaises(ValueError):
                        self.generation.GenerationMixin().load_custom_generate("evelyn-owned/security-probe", trust_remote_code=consent)
                self.assertFalse(marker.exists(), "Consent refusal must precede cache mutation")

    def test_approved_remote_boundary_and_missing_file_keep_contract(self):
        sentinel = lambda: "owned"
        with patch.object(self.generation, "has_file", return_value=True, create=True), patch.object(
            self.generation, "get_cached_module_file", return_value="owned/module.py"
        ), patch.object(self.generation, "check_python_requirements"), patch.object(
            self.generation, "get_class_in_module", return_value=sentinel
        ):
            result = self.generation.GenerationMixin().load_custom_generate("evelyn-owned/security-probe", trust_remote_code=True)
            self.assertIs(result, sentinel)
            self.assertEqual(result(), "owned")
        with patch.object(self.generation, "has_file", return_value=False, create=True), patch.object(
            self.generation, "get_cached_module_file", side_effect=AssertionError("missing file must not download")
        ):
            with self.assertRaises(OSError):
                self.generation.GenerationMixin().load_custom_generate("evelyn-owned/security-probe", trust_remote_code=True)


if __name__ == "__main__":
    unittest.main()
