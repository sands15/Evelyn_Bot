"""Fail-closed audit accounting using synthetic reports and owned package files."""
from __future__ import annotations

import copy
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib
from importlib.metadata import PathDistribution
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class SecurityPatchAuditTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("tools.audit_locked_dependencies"),
                             "The fail-closed audit gate is not implemented")
        self.audit = importlib.import_module("tools.audit_locked_dependencies")
        temporary = tempfile.TemporaryDirectory(prefix="security-audit-unit-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.patch_root = self.root / "tools" / "security_patches"
        self.patch_root.mkdir(parents=True)
        self.site = self.root / "site"
        self.site.mkdir()
        self.manifest = {"packages": []}
        self.distributions = {}
        for name, version, module, count, advisories in (
            ("accelerate", "1.12.0", "tests.hygiene.test_accelerate_checkpoint_security", 29,
             ["PYSEC-2026-3804"]),
            ("transformers", "4.57.6", "tests.hygiene.test_transformers_security", 4,
             ["PYSEC-2026-3929", "PYSEC-2026-4174"]),
        ):
            content = (name + " owned patched source\n").encode()
            files = {name + "/__init__.py": {"before": "0" * 64,
                      "after": hashlib.sha256(content).hexdigest()},
                     name + "/boundary.py": {"before": "0" * 64,
                      "after": hashlib.sha256(content).hexdigest()}}
            entry = {"name": name, "version": version, "wheel_sha256": "1" * 64,
                     "patch": name + ".patch", "files": files, "advisories": advisories,
                     "test_module": module,
                     "test_ids": [module + ".Synthetic.test_" + str(i) for i in range(count)]}
            self.manifest["packages"].append(entry)
            (self.patch_root / entry["patch"]).write_bytes(b"owned patch\n")
            for relative in files:
                path = self.site / relative
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(content)
            info = self.site / (name + "-" + version + "+evelyn.1.dist-info")
            info.mkdir()
            (info / "METADATA").write_text(
                "Metadata-Version: 2.1\nName: " + name + "\nVersion: " + version + "+evelyn.1\n",
                encoding="utf-8")
            marker = {"name": name, "upstream_version": version,
                      "local_version": version + "+evelyn.1", "wheel_sha256": "1" * 64,
                      "patch_sha256": hashlib.sha256(b"owned patch\n").hexdigest(), "files": files}
            (info / "evelyn_security_patch.json").write_text(json.dumps(marker), encoding="utf-8")
            self.distributions[name] = PathDistribution(info)
        self.lock = ("./tools/security_patches/accelerate\n./tools/security_patches/transformers\n"
                     "accelerate==1.12.0+evelyn.1\nSome_Package==2.3.4\n"
                     "transformers==4.57.6+evelyn.1\n")
        self.inventory = {"accelerate": "1.12.0", "some-package": "2.3.4", "transformers": "4.57.6"}
        self.raw = {"dependencies": [
            {"name": "accelerate", "version": "1.12.0", "vulns": [{"id": "PYSEC-2026-3804"}]},
            {"name": "some-package", "version": "2.3.4", "vulns": []},
            {"name": "transformers", "version": "4.57.6", "vulns": [
                {"id": "PYSEC-2026-3929"}, {"id": "PYSEC-2026-4174"}, {"id": "PYSEC-2025-217"}]},
        ], "fixes": []}

    def verify(self):
        with patch.object(self.audit, "PATCH_ROOT", self.patch_root), patch.object(
            self.audit.metadata, "distribution", side_effect=self.distributions.__getitem__
        ), patch.object(self.audit.importlib.util, "find_spec", side_effect=lambda name:
                        SimpleNamespace(origin=str(self.site / name / "__init__.py"))):
            return self.audit.verify_installed_patches(self.manifest)

    def evidence(self):
        ids = [test for entry in self.manifest["packages"] for test in entry["test_ids"]]
        return {"test_ids": ids, "tests": len(ids), "failures": 0, "errors": 0,
                "skipped": [], "expected_failures": 0, "unexpected_successes": 0,
                "stopped": False}

    def test_inventory_replaces_only_two_explicit_local_projects(self):
        text, inventory = self.audit.audit_inventory(self.lock, self.manifest)
        self.assertEqual(text, "accelerate==1.12.0\nSome_Package==2.3.4\ntransformers==4.57.6\n")
        self.assertEqual(inventory, self.inventory)

    def test_inventory_rejects_missing_pin_project_duplicate_and_unexpected_line(self):
        for text in (
            self.lock.replace("accelerate==1.12.0+evelyn.1\n", ""),
            self.lock.replace("./tools/security_patches/accelerate\n", ""),
            self.lock + "ACCELERATE==1.12.0+evelyn.1\n",
            self.lock + "./tools/security_patches/accelerate\n",
            self.lock + "./unexpected-project\n",
            self.lock + "--ignore-installed\n",
            self.lock.replace("1.12.0+evelyn.1", "1.12.0"),
        ):
            with self.subTest(lock=text), self.assertRaises(ValueError):
                self.audit.audit_inventory(text, self.manifest)

    def test_manifest_rejects_added_advisory_and_reduced_required_coverage(self):
        bad = copy.deepcopy(self.manifest)
        bad["packages"][0]["advisories"].append("PYSEC-2099-1")
        with self.assertRaises(ValueError):
            self.audit.audit_inventory(self.lock, bad)
        bad = copy.deepcopy(self.manifest)
        bad["packages"][0]["test_ids"].pop()
        with self.assertRaises(ValueError):
            self.audit.audit_inventory(self.lock, bad)

    def test_installed_source_provenance_and_all_target_hashes_pass(self):
        self.assertEqual(set(self.verify()), {"accelerate", "transformers"})

    def test_installed_unpatched_version_fails(self):
        info = self.distributions["accelerate"]._path / "METADATA"
        info.write_text("Name: accelerate\nVersion: 1.12.0\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.verify()

    def test_tampered_second_target_fails(self):
        (self.site / "accelerate" / "boundary.py").write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            self.verify()

    def test_missing_target_fails(self):
        (self.site / "transformers" / "boundary.py").unlink()
        with self.assertRaises(ValueError):
            self.verify()

    def test_missing_provenance_fails(self):
        (self.distributions["accelerate"]._path / "evelyn_security_patch.json").unlink()
        with self.assertRaises(ValueError):
            self.verify()

    def test_mismatched_provenance_fails(self):
        path = self.distributions["accelerate"]._path / "evelyn_security_patch.json"
        marker = json.loads(path.read_text())
        marker["wheel_sha256"] = "2" * 64
        path.write_text(json.dumps(marker), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.verify()

    def test_changed_reviewed_patch_fails_provenance(self):
        (self.patch_root / "accelerate.patch").write_bytes(b"different patch")
        with self.assertRaises(ValueError):
            self.verify()

    def test_active_import_shadowing_fails(self):
        with patch.object(self.audit, "PATCH_ROOT", self.patch_root), patch.object(
            self.audit.metadata, "distribution", side_effect=self.distributions.__getitem__
        ), patch.object(self.audit.importlib.util, "find_spec",
                        return_value=SimpleNamespace(origin=str(self.root / "shadow.py"))):
            with self.assertRaises(ValueError):
                self.audit.verify_installed_patches(self.manifest)

    def test_normal_classification_preserves_raw_findings_and_old_deferral(self):
        original = copy.deepcopy(self.raw)
        result = self.audit.classify_findings(self.raw, self.inventory, self.manifest,
                                              {"accelerate", "transformers"}, raw_exit=1)
        self.assertEqual(result["raw_count"], 4)
        self.assertEqual(len(result["verified_remediations"]), 3)
        self.assertEqual(result["old_deferred"], [{"package": "transformers", "id": "PYSEC-2025-217"}])
        self.assertEqual(result["unresolved"], [])
        self.assertEqual(result["raw_exit"], 1)
        self.assertEqual(self.raw, original)

    def test_repeated_upstream_advisory_rows_remain_visible_without_expanding_policy(self):
        # PyPI returns separate range/fix records for these same advisory IDs.
        for identifier in ("PYSEC-2026-2288", "PYSEC-2026-2289", "PYSEC-2026-2290"):
            self.raw["dependencies"][2]["vulns"].extend([
                {"id": identifier, "fix_versions": []},
                {"id": identifier, "fix_versions": ["5.5.0"]},
            ])
        original = copy.deepcopy(self.raw)
        result = self.audit.classify_findings(self.raw, self.inventory, self.manifest,
                                              {"accelerate", "transformers"}, raw_exit=1)
        self.assertEqual(result["raw_count"], 10)
        self.assertEqual(result["raw_unique_count"], 7)
        self.assertEqual(len(result["old_deferred"]), 7)
        self.assertEqual(len({item["id"] for item in result["old_deferred"]}), 4)
        self.assertEqual(len(result["verified_remediations"]), 3)
        self.assertEqual(result["unresolved"], [])
        self.assertEqual(self.raw, original)

    def test_unknown_advisory_remains_unresolved(self):
        self.raw["dependencies"][0]["vulns"].append({"id": "PYSEC-2099-1"})
        result = self.audit.classify_findings(self.raw, self.inventory, self.manifest,
                                              {"accelerate", "transformers"}, raw_exit=1)
        self.assertEqual(result["unresolved"], [{"package": "accelerate", "id": "PYSEC-2099-1"}])
        self.assertEqual(result["raw_count"], 5)

    def test_old_deferred_id_on_another_package_is_not_exempt(self):
        self.raw["dependencies"][0]["vulns"].append({"id": "PYSEC-2025-217"})
        result = self.audit.classify_findings(self.raw, self.inventory, self.manifest,
                                              {"accelerate", "transformers"}, raw_exit=1)
        self.assertIn({"package": "accelerate", "id": "PYSEC-2025-217"}, result["unresolved"])

    def test_unverified_patch_advisory_is_not_remediated(self):
        result = self.audit.classify_findings(self.raw, self.inventory, self.manifest, set(), raw_exit=1)
        self.assertEqual(result["verified_remediations"], [])
        self.assertEqual(len(result["unresolved"]), 3)

    def test_missing_audited_package_and_skipped_package_fail(self):
        bad = copy.deepcopy(self.raw)
        bad["dependencies"].pop(1)
        with self.assertRaises(ValueError):
            self.audit.classify_findings(bad, self.inventory, self.manifest, set(), raw_exit=1)
        self.raw["dependencies"][1] = {"name": "some-package", "skip_reason": "unavailable"}
        with self.assertRaises(ValueError):
            self.audit.classify_findings(self.raw, self.inventory, self.manifest, set(), raw_exit=1)

    def test_raw_exit_inconsistent_with_findings_fails(self):
        with self.assertRaises(ValueError):
            self.audit.classify_findings(self.raw, self.inventory, self.manifest, set(), raw_exit=0)

    def test_duplicate_unexpected_package_and_wrong_audit_version_fail(self):
        variants = []
        duplicate = copy.deepcopy(self.raw)
        duplicate["dependencies"].append(copy.deepcopy(duplicate["dependencies"][0]))
        variants.append(duplicate)
        unexpected = copy.deepcopy(self.raw)
        unexpected["dependencies"][1]["name"] = "unexpected"
        variants.append(unexpected)
        wrong_version = copy.deepcopy(self.raw)
        wrong_version["dependencies"][0]["version"] = "1.12.0+evelyn.1"
        variants.append(wrong_version)
        for raw in variants:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.audit.classify_findings(raw, self.inventory, self.manifest, set(), raw_exit=1)

    def test_exact_regression_coverage_passes(self):
        self.assertEqual(self.audit.validate_regressions(self.evidence(), self.manifest, "posix"), [])

    def test_regression_undercoverage_failure_or_unknown_skip_fails(self):
        for key, value in (("tests", 32), ("failures", 1), ("errors", 1),
                           ("expected_failures", 1), ("unexpected_successes", 1),
                           ("skipped", ["unrecognized.test"]), ("stopped", True)):
            evidence = self.evidence()
            evidence[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.audit.validate_regressions(evidence, self.manifest, "nt")
        evidence = self.evidence()
        evidence["test_ids"][-1] = evidence["test_ids"][0]
        with self.assertRaises(ValueError):
            self.audit.validate_regressions(evidence, self.manifest, "posix")

    def test_known_windows_fifo_gap_is_explicit_and_not_allowed_on_linux(self):
        known = "tests.hygiene.test_accelerate_checkpoint_security.AccelerateCheckpointSecurityTests.test_rejects_fifo_shard_without_blocking"
        self.manifest["packages"][0]["test_ids"][0] = known
        evidence = self.evidence()
        evidence["skipped"] = [known]
        self.assertEqual(self.audit.validate_regressions(evidence, self.manifest, "nt"), [known])
        with self.assertRaises(ValueError):
            self.audit.validate_regressions(evidence, self.manifest, "posix")

    def run_pipeline(self, *, missing_report=False, regressions_fail=False):
        (self.root / "requirements.lock").write_text(self.lock, encoding="utf-8")
        (self.patch_root / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        raw_bytes = json.dumps(self.raw, indent=3).encode()
        observed = {}

        def audit_boundary(command, **kwargs):
            observed.update(command=command, environment=kwargs["env"])
            if not missing_report:
                Path(command[command.index("--output") + 1]).write_bytes(raw_bytes)
            return SimpleNamespace(returncode=1)

        evidence = self.evidence()
        evidence["platform_gaps"] = []
        with patch.object(self.audit, "PATCH_ROOT", self.patch_root), patch.object(
            self.audit.metadata, "distribution", side_effect=self.distributions.__getitem__
        ), patch.object(self.audit.metadata, "version", return_value="2.10.1"), patch.object(
            self.audit.importlib.util, "find_spec", side_effect=lambda name:
            SimpleNamespace(origin=str(self.site / name / "__init__.py"))
        ), patch.object(self.audit.subprocess, "run", side_effect=audit_boundary), patch.object(
            self.audit, "run_security_tests", side_effect=ValueError("regressions failed") if regressions_fail else None,
            return_value=evidence
        ), redirect_stdout(io.StringIO()):
            code = self.audit.audit_repository(self.root, self.root / "attempts")
        latest = max((self.root / "attempts").iterdir(), key=lambda path: path.stat().st_mtime_ns)
        return code, latest, observed, raw_bytes

    def test_pipeline_keeps_full_raw_exit_and_distinct_non_overwriting_attempts(self):
        code, first, observed, raw = self.run_pipeline()
        self.assertEqual(code, 0)
        self.assertEqual((first / "raw-audit.json").read_bytes(), raw)
        self.assertEqual(json.loads((first / "raw-exit.json").read_text()), {"raw_exit": 1})
        summary = json.loads((first / "summary.json").read_text())
        self.assertEqual(summary["raw_count"], 4)
        self.assertEqual(len(summary["verified_remediations"]), 3)
        self.assertNotIn("--ignore-vuln", observed["command"])
        self.assertIn("--no-deps", observed["command"])
        self.assertIn("--disable-pip", observed["command"])
        self.assertFalse(any(key.upper().startswith("PIP_AUDIT_") for key in observed["environment"]))
        code, second, _, _ = self.run_pipeline()
        self.assertEqual(code, 0)
        self.assertNotEqual(first, second)
        self.assertEqual((first / "raw-audit.json").read_bytes(), raw)

    def test_pipeline_never_accepts_missing_report_as_zero_findings(self):
        code, folder, _, _ = self.run_pipeline(missing_report=True)
        self.assertEqual(code, 1)
        summary = json.loads((folder / "summary.json").read_text())
        self.assertFalse(summary["checks_valid"])
        self.assertIsNone(summary["raw_count"])
        self.assertEqual(summary["raw_exit"], 1)

    def test_pipeline_does_not_classify_remediation_after_regression_failure(self):
        code, folder, _, raw = self.run_pipeline(regressions_fail=True)
        self.assertEqual(code, 1)
        summary = json.loads((folder / "summary.json").read_text())
        self.assertEqual(summary["verified_remediations"], [])
        self.assertEqual(len(summary["unresolved"]), 3)
        self.assertEqual((folder / "raw-audit.json").read_bytes(), raw)

    def test_pipeline_unknown_advisory_returns_failure(self):
        self.raw["dependencies"][0]["vulns"].append({"id": "PYSEC-2099-1"})
        code, folder, _, _ = self.run_pipeline()
        self.assertEqual(code, 1)
        self.assertEqual(len(json.loads((folder / "summary.json").read_text())["unresolved"]), 1)

    def test_cli_has_no_skip_checks_option(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            self.audit.main(["--skip-checks"])
        self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
