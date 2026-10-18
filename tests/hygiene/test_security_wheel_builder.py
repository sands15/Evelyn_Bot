from __future__ import annotations

import base64
import csv
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile


ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "tools/security_patches/wheel_builder.py"


class SecurityWheelBuilderTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".tmp").mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="security-wheel-", dir=ROOT / ".tmp")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "accelerate"
        self.project.mkdir()
        self.output = self.root / "output"
        self.before = b"def answer():\n    return 'unsafe'\n"
        self.after = b"def answer():\n    return 'safe'\n"
        self.files = {
            "accelerate/example.py": self.before,
            "accelerate/untouched.py": b"UNCHANGED = True\n",
            "accelerate-1.0.0.dist-info/METADATA": b"Metadata-Version: 2.1\nName: accelerate\nVersion: 1.0.0\nRequires-Dist: example-dependency==2.0\n\nSynthetic package.\n",
            "accelerate-1.0.0.dist-info/WHEEL": b"Wheel-Version: 1.0\nGenerator: synthetic\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
            "accelerate-1.0.0.dist-info/licenses/LICENSE": b"Synthetic license retained verbatim.\n",
            "accelerate-1.0.0.dist-info/RECORD": b"",
        }
        self.entry = {
            "name": "accelerate", "version": "1.0.0",
            "wheel_url": "https://files.pythonhosted.org/packages/synthetic/accelerate-1.0.0-py3-none-any.whl",
            "wheel_sha256": "",
            "patch": "accelerate.patch",
            "files": {"accelerate/example.py": {
                "before": hashlib.sha256(self.before).hexdigest(),
                "after": hashlib.sha256(self.after).hexdigest(),
            }},
            "advisories": ["SYNTHETIC-ONLY"],
        }
        (self.root / "accelerate.patch").write_text(
            "diff --git a/accelerate/example.py b/accelerate/example.py\n"
            "--- a/accelerate/example.py\n+++ b/accelerate/example.py\n"
            "@@ -1,2 +1,2 @@\n def answer():\n-    return 'unsafe'\n+    return 'safe'\n",
            encoding="utf-8", newline="\n")
        self.wheel = self.make_wheel()
        self.save_manifest()

    def make_wheel(self, extra=()):
        stream = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(stream, "w") as archive:
                for name, content in [*self.files.items(), *extra]:
                    if isinstance(name, str) and "\\" in name:
                        raw_name = name
                        name = zipfile.ZipInfo(name)
                        name.filename = name.orig_filename = raw_name
                    archive.writestr(name, content)
        return stream.getvalue()

    def save_manifest(self):
        self.entry["wheel_sha256"] = hashlib.sha256(self.wheel).hexdigest()
        (self.root / "manifest.json").write_text(json.dumps({"packages": [self.entry]}), encoding="utf-8")

    def builder(self):
        self.assertTrue(BUILDER.is_file(), "Missing verified security wheel build backend")
        spec = importlib.util.spec_from_file_location("security_wheel_builder_test", BUILDER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def build(self, builder=None, output=None):
        builder = builder or self.builder()
        with patch.object(builder, "_download_wheel", return_value=self.wheel):
            return builder.build_wheel("accelerate", self.project, output or self.output)

    def test_reproducible_wheel_preserves_metadata_licenses_and_record(self):
        name = self.build()
        self.assertEqual(name, "accelerate-1.0.0+evelyn.1-py3-none-any.whl")
        first = (self.output / name).read_bytes()
        other = self.root / "other-output"
        self.assertEqual(self.build(output=other), name)
        self.assertEqual(first, (other / name).read_bytes())
        with self.assertRaises(FileExistsError):
            self.build()
        with zipfile.ZipFile(io.BytesIO(first)) as archive:
            self.assertEqual(archive.read("accelerate/example.py"), self.after)
            self.assertEqual(archive.read("accelerate/untouched.py"), self.files["accelerate/untouched.py"])
            info = "accelerate-1.0.0+evelyn.1.dist-info/"
            self.assertEqual(archive.read(info + "licenses/LICENSE"), self.files["accelerate-1.0.0.dist-info/licenses/LICENSE"])
            self.assertEqual(archive.read(info + "METADATA"), self.files["accelerate-1.0.0.dist-info/METADATA"].replace(b"Version: 1.0.0\n", b"Version: 1.0.0+evelyn.1\n"))
            self.assertIn(info + "evelyn_security_patch.json", archive.namelist())
            self.assertEqual(json.loads(archive.read(info + "evelyn_security_patch.json")), {
                "name": "accelerate", "upstream_version": "1.0.0", "local_version": "1.0.0+evelyn.1",
                "wheel_sha256": hashlib.sha256(self.wheel).hexdigest(),
                "patch_sha256": hashlib.sha256((self.root / "accelerate.patch").read_bytes()).hexdigest(),
                "files": self.entry["files"],
            })
            rows = list(csv.reader(io.StringIO(archive.read(info + "RECORD").decode())))
            self.assertEqual({row[0] for row in rows}, set(archive.namelist()))
            for path, digest, size in rows:
                if path == info + "RECORD":
                    self.assertEqual((digest, size), ("", ""))
                else:
                    content = archive.read(path)
                    expected = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
                    self.assertEqual((digest, size), ("sha256=" + expected, str(len(content))))

    def test_wrong_wheel_or_source_hash_and_patch_drift_are_rejected(self):
        builder = self.builder()
        for fault in ("wheel", "before", "after", "patch"):
            with self.subTest(fault=fault):
                original = json.loads(json.dumps(self.entry))
                patch_path = self.root / "accelerate.patch"
                original_patch = patch_path.read_bytes()
                if fault == "wheel":
                    self.entry["wheel_sha256"] = "0" * 64
                elif fault in ("before", "after"):
                    self.entry["files"]["accelerate/example.py"][fault] = "0" * 64
                else:
                    patch_path.write_bytes(original_patch.replace(b"'unsafe'", b"'missing'"))
                (self.root / "manifest.json").write_text(json.dumps({"packages": [self.entry]}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.build(builder)
                self.assertFalse(self.output.exists() and any(self.output.iterdir()))
                self.entry = original
                patch_path.write_bytes(original_patch)
                self.save_manifest()

    def test_unsafe_duplicate_and_link_zip_entries_are_rejected(self):
        builder = self.builder()
        link = zipfile.ZipInfo("accelerate/link.py")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        for name in ("../escape.py", "/absolute.py", "C:/escape.py", "accelerate\\escape.py", "accelerate/example.py", "ACCELERATE/EXAMPLE.PY", "accelerate/NUL.py", link, "accelerate//"):
            with self.subTest(path=str(name)):
                self.wheel = self.make_wheel([(name, b"bad")])
                self.save_manifest()
                with self.assertRaises(ValueError):
                    self.build(builder)
                self.assertFalse(self.output.exists() and any(self.output.iterdir()))

    def test_patch_cannot_change_unlisted_files(self):
        with (self.root / "accelerate.patch").open("a", encoding="utf-8") as stream:
            stream.write("diff --git a/extra.py b/extra.py\nnew file mode 100644\n--- /dev/null\n+++ b/extra.py\n@@ -0,0 +1 @@\n+bad\n")
        with self.assertRaises(ValueError):
            self.build()

    def test_download_rejects_nonofficial_url_and_oversized_response(self):
        builder = self.builder()
        with self.assertRaises(ValueError):
            builder._download_wheel("http://127.0.0.1/private.whl")
        response = io.BytesIO(b"0123456789")
        with patch.object(builder.urllib.request, "build_opener") as opener, patch.object(builder, "MAX_WHEEL_BYTES", 4):
            opener.return_value.open.return_value = response
            with self.assertRaises(ValueError):
                builder._download_wheel(self.entry["wheel_url"])

    def test_metadata_mismatch_and_expansion_limit_are_rejected(self):
        builder = self.builder()
        metadata = "accelerate-1.0.0.dist-info/METADATA"
        self.files[metadata] = self.files[metadata].replace(b"Version: 1.0.0", b"Version: 2.0.0")
        self.wheel = self.make_wheel()
        self.save_manifest()
        with self.assertRaises(ValueError):
            self.build(builder)
        with patch.object(builder, "MAX_EXPANDED_BYTES", 4):
            with self.assertRaises(ValueError):
                builder._read_wheel(self.wheel)

    def test_redirect_and_download_deadline_fail_closed(self):
        builder = self.builder()
        with self.assertRaises(ValueError):
            builder._NoRedirect().redirect_request(None, None, 302, "redirect", {}, "http://127.0.0.1/private")
        with patch.object(builder.urllib.request, "build_opener") as opener, patch.object(builder.time, "monotonic", side_effect=[0, 61]):
            opener.return_value.open.return_value = io.BytesIO(b"small")
            with self.assertRaises(ValueError):
                builder._download_wheel(self.entry["wheel_url"])

    def test_standard_pip_builds_owned_synthetic_project_without_dependencies(self):
        self.builder()
        shutil.copyfile(BUILDER, self.root / "wheel_builder.py")
        source = ROOT / "tools/security_patches/accelerate"
        shutil.copyfile(source / "pyproject.toml", self.project / "pyproject.toml")
        backend = (source / "build_backend.py").read_text(encoding="utf-8")
        # Only the owned fixture transport is replaced; pip executes the real builder.
        (self.root / "source.whl").write_bytes(self.wheel)
        backend += '\n_builder._download_wheel = lambda _url: (_PROJECT.parent / "source.whl").read_bytes()\n'
        (self.project / "build_backend.py").write_text(backend, encoding="utf-8")
        env = {name: value for name, value in os.environ.items() if name.upper() in {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT"}}
        env.update(TEMP=str(self.root), TMP=str(self.root), TMPDIR=str(self.root), PIP_CONFIG_FILE=os.devnull, PYTHONUTF8="1")
        result = subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-index", "--no-cache-dir", "--disable-pip-version-check", "--wheel-dir", str(self.output), str(self.project)], cwd=self.root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.output / "accelerate-1.0.0+evelyn.1-py3-none-any.whl").is_file())


if __name__ == "__main__":
    unittest.main()
