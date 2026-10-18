"""Exercise the installed, source-verified Accelerate checkpoint security patch.

Only owned tiny CPU tensors are loaded; no Hub requests or user models are used.
The same tests must fail against pristine 1.12.0 before the patch is accepted.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class AccelerateCheckpointSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import torch
        from accelerate import load_checkpoint_and_dispatch
        from accelerate.utils.modeling import load_checkpoint_in_model

        cls.torch = torch
        cls.load = staticmethod(load_checkpoint_in_model)
        cls.dispatch = staticmethod(load_checkpoint_and_dispatch)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="accelerate-checkpoint-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.checkpoint = self.root / "checkpoint"
        self.checkpoint.mkdir()
        self.outside = self.root / "outside.bin"
        self.save(self.outside)

    def save(self, path, value=7.0):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.torch.save({"weight": self.torch.tensor([[value]])}, path)

    def model(self):
        model = self.torch.nn.Linear(1, 1, bias=False)
        with self.torch.no_grad():
            model.weight.zero_()
        return model

    def index(self, entries, folder=None):
        path = (folder or self.checkpoint) / "pytorch_model.bin.index.json"
        path.write_text(json.dumps({"weight_map": entries}), encoding="utf-8")
        return path

    def reject(self, checkpoint, *, dispatch=False):
        model = self.model()
        try:
            if dispatch:
                self.dispatch(model, str(checkpoint), device_map={"": "cpu"})
            else:
                self.load(model, str(checkpoint))
        except Exception as error:
            self.assertIsInstance(error, ValueError)
        else:
            self.fail("Unsafe checkpoint was loaded")
        self.assertEqual(model.weight.item(), 0.0)

    def symlink(self, link, target, *, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except OSError as error:
            if os.name == "nt" and error.winerror == 1314:
                self.skipTest("Windows symlink privilege unavailable; required native Linux coverage")
            raise

    def test_rejects_parent_traversal_before_loading(self):
        self.reject(self.index({"weight": "../outside.bin"}))

    def test_dispatch_rejects_the_same_parent_traversal(self):
        self.reject(self.index({"weight": "../outside.bin"}), dispatch=True)

    def test_rejects_absolute_shard_path(self):
        self.reject(self.index({"weight": self.outside.as_posix()}))

    def test_rejects_windows_traversal_on_every_platform(self):
        entry = "..\\outside.bin"
        if os.name != "nt":
            self.save(self.checkpoint / entry)
        self.reject(self.index({"weight": entry}))

    def test_rejects_windows_drive_relative_path(self):
        entry = "C:outside.bin"
        if os.name != "nt":
            self.save(self.checkpoint / entry)
        self.reject(self.index({"weight": entry}))

    def test_rejects_windows_rooted_path_on_every_platform(self):
        entry = str(self.outside)[2:] if os.name == "nt" else "\\outside.bin"
        if os.name != "nt":
            self.save(self.checkpoint / entry)
        self.reject(self.index({"weight": entry}))

    def test_rejects_windows_alternate_stream_syntax(self):
        entry = "shard.bin:stream"
        self.save(self.checkpoint / entry)
        self.reject(self.index({"weight": entry}))

    def test_rejects_windows_device_name_on_every_platform(self):
        entry = "NUL.bin"
        if os.name != "nt":
            self.save(self.checkpoint / entry)
        self.reject(self.index({"weight": entry}))

    def test_validates_every_shard_before_changing_model(self):
        self.save(self.checkpoint / "a-valid.bin", 3.0)
        self.symlink(self.checkpoint / "z-escape.bin", self.outside)
        self.reject(self.index({"weight": "a-valid.bin", "other": "z-escape.bin"}))

    def test_rejects_non_string_shard_entry(self):
        self.reject(self.index({"weight": 1}))

    def test_rejects_empty_shard_entry(self):
        self.reject(self.index({"weight": ""}))

    def test_rejects_directory_shard(self):
        (self.checkpoint / "directory").mkdir()
        self.reject(self.index({"weight": "directory"}))

    def test_rejects_symlink_to_external_file(self):
        self.symlink(self.checkpoint / "escape.bin", self.outside)
        self.reject(self.index({"weight": "escape.bin"}))

    def test_rejects_symlinked_directory_escape(self):
        self.symlink(self.checkpoint / "escape", self.root, directory=True)
        self.reject(self.index({"weight": "escape/outside.bin"}))

    def test_rejects_missing_shard(self):
        self.reject(self.index({"weight": "missing.bin"}))

    def test_loads_normal_nested_shard_and_duplicate_map_entries(self):
        self.save(self.checkpoint / "nested" / "weights.bin")
        index = self.index({"weight": "nested/weights.bin", "alias": "nested/weights.bin"})
        model = self.model()
        self.load(model, str(index))
        self.assertEqual(model.weight.item(), 7.0)

    def test_loads_unsharded_checkpoint_file(self):
        model = self.model()
        self.load(model, str(self.outside))
        self.assertEqual(model.weight.item(), 7.0)

    def test_dispatch_preserves_normal_cpu_checkpoint(self):
        self.save(self.checkpoint / "weights.bin")
        model = self.model()
        returned = self.dispatch(model, str(self.index({"weight": "weights.bin"})),
                                 device_map={"": "cpu"})
        self.assertIs(returned, model)
        self.assertEqual(model.weight.item(), 7.0)

    def test_preserves_legacy_flat_weight_map(self):
        self.save(self.checkpoint / "weights.bin")
        index = self.checkpoint / "pytorch_model.bin.index.json"
        index.write_text(json.dumps({"weight": "weights.bin"}), encoding="utf-8")
        model = self.model()
        self.load(model, str(index))
        self.assertEqual(model.weight.item(), 7.0)

    def test_loads_internal_symlink(self):
        self.save(self.checkpoint / "weights.bin")
        self.symlink(self.checkpoint / "alias.bin", "weights.bin")
        model = self.model()
        self.load(model, str(self.index({"weight": "alias.bin"})))
        self.assertEqual(model.weight.item(), 7.0)

    def cache_snapshot(self):
        repo = self.root / "models--owned--synthetic"
        snapshot = repo / "snapshots" / ("a" * 40)
        snapshot.mkdir(parents=True)
        blobs = repo / "blobs"
        blobs.mkdir()
        return snapshot, blobs

    def test_preserves_hub_snapshot_blob_symlinks_including_index(self):
        snapshot, blobs = self.cache_snapshot()
        self.save(blobs / ("b" * 64))
        index = self.index({"weight": "weights.bin"}, folder=blobs)
        index = index.rename(blobs / ("c" * 40))
        self.symlink(snapshot / "pytorch_model.bin.index.json", index)
        self.symlink(snapshot / "weights.bin", blobs / ("b" * 64))
        model = self.model()
        self.load(model, str(snapshot))
        self.assertEqual(model.weight.item(), 7.0)

    def test_preserves_safetensors_format_through_hub_symlink(self):
        from safetensors.torch import save_file

        snapshot, blobs = self.cache_snapshot()
        save_file({"weight": self.torch.tensor([[9.0]])}, str(blobs / ("d" * 64)),
                  metadata={"format": "pt"})
        self.symlink(snapshot / "weights.safetensors", blobs / ("d" * 64))
        model = self.model()
        self.load(model, str(self.index({"weight": "weights.safetensors"}, folder=snapshot)))
        self.assertEqual(model.weight.item(), 9.0)

    def test_hub_layout_does_not_authorize_external_symlink(self):
        snapshot, _ = self.cache_snapshot()
        self.symlink(snapshot / "weights.bin", self.outside)
        self.reject(self.index({"weight": "weights.bin"}, folder=snapshot))

    def test_hub_layout_only_allows_content_addressed_blob_names(self):
        snapshot, blobs = self.cache_snapshot()
        self.save(blobs / "unrelated.bin")
        self.symlink(snapshot / "weights.bin", blobs / "unrelated.bin")
        self.reject(self.index({"weight": "weights.bin"}, folder=snapshot))

    def test_non_hub_directory_does_not_gain_blob_exception(self):
        snapshot = self.root / "unrelated" / "snapshots" / ("a" * 40)
        snapshot.mkdir(parents=True)
        blob = snapshot.parent.parent / "blobs" / ("b" * 64)
        self.save(blob)
        self.symlink(snapshot / "weights.bin", blob)
        self.reject(self.index({"weight": "weights.bin"}, folder=snapshot))

    def test_hub_blob_directory_symlink_does_not_expand_trust_root(self):
        snapshot, blobs = self.cache_snapshot()
        blobs.rmdir()
        self.symlink(blobs, self.root, directory=True)
        self.symlink(snapshot / "weights.bin", blobs / "outside.bin")
        self.reject(self.index({"weight": "weights.bin"}, folder=snapshot))

    def assert_fifo_rejected(self, checkpoint):
        # A child bounds the original blocking read, including imports, on native POSIX.
        code = (
            "import sys; sys.path[:] = " + repr(sys.path) + "; "
            "import torch; from accelerate.utils.modeling import load_checkpoint_in_model; "
            "model = torch.nn.Linear(1, 1, bias=False)\n"
            "try:\n load_checkpoint_in_model(model, sys.argv[1])\n"
            "except ValueError:\n sys.exit(0)\n"
            "sys.exit(2)\n"
        )
        try:
            result = subprocess.run([sys.executable, "-c", code, str(checkpoint)],
                                    capture_output=True, text=True, timeout=15)
        except subprocess.TimeoutExpired:
            self.fail("Checkpoint loading blocked on a FIFO")
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires native POSIX FIFO; covered in Linux CI")
    def test_rejects_fifo_shard_without_blocking(self):
        os.mkfifo(self.checkpoint / "weights.bin")
        self.assert_fifo_rejected(self.index({"weight": "weights.bin"}))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires native POSIX FIFO; covered in Linux CI")
    def test_rejects_fifo_index_selected_from_directory(self):
        os.mkfifo(self.checkpoint / "pytorch_model.bin.index.json")
        self.assert_fifo_rejected(self.checkpoint)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires native POSIX FIFO; covered in Linux CI")
    def test_rejects_fifo_unsharded_weights_selected_from_directory(self):
        os.mkfifo(self.checkpoint / "pytorch_model.bin")
        self.assert_fifo_rejected(self.checkpoint)


if __name__ == "__main__":
    unittest.main()
