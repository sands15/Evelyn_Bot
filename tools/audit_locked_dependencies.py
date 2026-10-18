"""Keep the full upstream audit, then verify narrowly documented source repairs."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
from importlib import metadata
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]
PATCH_ROOT = ROOT / "tools" / "security_patches"
PATCH_POLICY = {
    "accelerate": ("tests.hygiene.test_accelerate_checkpoint_security", 29, {"PYSEC-2026-3804"}),
    "transformers": ("tests.hygiene.test_transformers_security", 4,
                     {"PYSEC-2026-3929", "PYSEC-2026-4174"}),
}
# Existing, unresolved policy only; never send these as pip-audit ignore arguments.
OLD_TRANSFORMERS_DEFERRED = {"PYSEC-2025-217", "PYSEC-2026-2290", "PYSEC-2026-2288", "PYSEC-2026-2289"}
WINDOWS_FIFO_GAPS = {
    "tests.hygiene.test_accelerate_checkpoint_security.AccelerateCheckpointSecurityTests." + name
    for name in ("test_rejects_fifo_shard_without_blocking",
                 "test_rejects_fifo_index_selected_from_directory",
                 "test_rejects_fifo_unsharded_weights_selected_from_directory")
}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_object)


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
        raise ValueError("Invalid package name")
    return re.sub(r"[-_.]+", "-", value).lower()


def _entries(manifest):
    entries = manifest.get("packages")
    if not isinstance(entries, list) or len(entries) != len(PATCH_POLICY):
        raise ValueError("Expected the two reviewed security patch packages")
    result = {}
    for entry in entries:
        name = entry.get("name")
        if name not in PATCH_POLICY or name in result:
            raise ValueError("Unexpected or duplicate patched package")
        module, minimum, advisories = PATCH_POLICY[name]
        if (entry.get("test_module") != module or set(entry.get("advisories", [])) != advisories
                or len(entry["advisories"]) != len(advisories)):
            raise ValueError("Patch advisory or regression module differs from reviewed scope")
        ids = entry.get("test_ids")
        if (not isinstance(ids, list) or len(ids) < minimum or len(set(ids)) != len(ids)
                or any(not isinstance(test, str) or not test.startswith(module + ".") for test in ids)):
            raise ValueError("Required regression coverage is missing or duplicated")
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", entry.get("version", "")):
            raise ValueError("Expected exact upstream base version")
        if not re.fullmatch(r"[0-9a-f]{64}", entry.get("wheel_sha256", "")):
            raise ValueError("Missing upstream wheel provenance")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+\.patch", entry.get("patch", "")):
            raise ValueError("Invalid reviewed patch path")
        files = entry.get("files")
        if not isinstance(files, dict) or not files or name + "/__init__.py" not in files:
            raise ValueError("Missing installed source targets")
        for relative, hashes in files.items():
            parts = PurePosixPath(relative).parts
            if (not relative.startswith(name + "/") or "\\" in relative or ":" in relative
                    or any(part in (".", "..") for part in parts)
                    or any(not re.fullmatch(r"[0-9a-f]{64}", hashes.get(key, "")) for key in ("before", "after"))):
                raise ValueError("Invalid installed source target")
        result[name] = entry
    return result


def audit_inventory(lock_text, manifest):
    entries = _entries(manifest)
    projects, inventory, lines = set(), {}, []
    for raw_line in lock_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("./tools/security_patches/"):
            name = line.removeprefix("./tools/security_patches/")
            if name not in entries or name in projects:
                raise ValueError("Unexpected or duplicate local patch project")
            projects.add(name)
            continue
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9.!+_-]*)", line)
        if match is None:
            raise ValueError("Lock must contain only exact pins and the two reviewed local projects")
        spelling, version = match.groups()
        name = _name(spelling)
        if name in inventory:
            raise ValueError("Duplicate locked package")
        if name in entries:
            if version != entries[name]["version"] + "+evelyn.1":
                raise ValueError("Local patched version differs from manifest")
            version = entries[name]["version"]
            line = spelling + "==" + version
        inventory[name] = version
        lines.append(line)
    if projects != set(entries) or not set(entries).issubset(inventory):
        raise ValueError("Missing explicit local project or exact patched pin")
    return "\n".join(lines) + "\n", inventory


def verify_installed_patches(manifest):
    verified = []
    for name, entry in _entries(manifest).items():
        try:
            distribution = metadata.distribution(name)
        except metadata.PackageNotFoundError as error:
            raise ValueError("Required patched package is not installed") from error
        local_version = entry["version"] + "+evelyn.1"
        if _name(distribution.metadata.get("Name")) != name or distribution.version != local_version:
            raise ValueError("Installed package identity or local version differs")
        expected = {"name": name, "upstream_version": entry["version"], "local_version": local_version,
                    "wheel_sha256": entry["wheel_sha256"],
                    "patch_sha256": _sha256(PATCH_ROOT / entry["patch"]), "files": entry["files"]}
        marker = distribution.read_text("evelyn_security_patch.json")
        if marker is None or json.loads(marker, object_pairs_hook=_unique_object) != expected:
            raise ValueError("Installed wheel source provenance differs from reviewed patch")
        installation = Path(distribution.locate_file("")).resolve()
        for relative, hashes in entry["files"].items():
            target = Path(distribution.locate_file(relative))
            if (not target.resolve().is_relative_to(installation) or target.is_symlink()
                    or not target.is_file() or _sha256(target) != hashes["after"]):
                raise ValueError("Installed patched source is missing or changed")
        spec = importlib.util.find_spec(name)
        expected_import = Path(distribution.locate_file(name + "/__init__.py")).resolve()
        if spec is None or not spec.origin or Path(spec.origin).resolve() != expected_import:
            raise ValueError("Runtime import is shadowing the verified installed package")
        verified.append(name)
    return verified


def validate_regressions(evidence, manifest, platform):
    expected = {test for entry in _entries(manifest).values() for test in entry["test_ids"]}
    actual = evidence["test_ids"]
    if (set(actual) != expected or len(actual) != len(expected) or evidence["tests"] != len(expected)
            or any(evidence[key] for key in ("failures", "errors", "expected_failures", "unexpected_successes", "stopped"))):
        raise ValueError("Security regressions failed or required coverage did not execute")
    skipped = evidence["skipped"]
    if (len(set(skipped)) != len(skipped) or not set(skipped).issubset(expected)
            or (skipped and (platform != "nt" or not set(skipped).issubset(WINDOWS_FIFO_GAPS)))):
        raise ValueError("Unexpected security regression skip")
    return sorted(skipped)


class _CoverageResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.test_ids = []

    def startTest(self, test):
        self.test_ids.append(test.id())
        super().startTest(test)


def run_security_tests(manifest, attempt_directory):
    entries = _entries(manifest)
    settings = {"EVELYN_REQUIRE_PATCHED_DEPS": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                "EVELYN_TEST_ATTEMPT_ROOT": str(Path(attempt_directory).resolve())}
    previous = {key: os.environ.get(key) for key in settings}
    try:
        os.environ.update(settings)
        suite = unittest.TestLoader().loadTestsFromNames([entry["test_module"] for entry in entries.values()])
        result = unittest.TextTestRunner(verbosity=2, resultclass=_CoverageResult).run(suite)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    evidence = {"test_ids": result.test_ids, "tests": result.testsRun, "failures": len(result.failures),
                "errors": len(result.errors), "skipped": [test.id() for test, _reason in result.skipped],
                "expected_failures": len(result.expectedFailures), "unexpected_successes": len(result.unexpectedSuccesses),
                "stopped": result.shouldStop}
    evidence["platform_gaps"] = validate_regressions(evidence, manifest, os.name)
    receipts = list(Path(attempt_directory).glob("*/result.json"))
    if len(receipts) != 1:
        raise ValueError("Missing unique automatic security regression receipt")
    receipt = _read_json(receipts[0])
    if (receipt["status"] not in ("PASS", "INCOMPLETE") or receipt["tests"] != evidence["tests"]
            or receipt["failures"] or receipt["errors"] or receipt["skipped"] != len(evidence["skipped"])):
        raise ValueError("Automatic regression receipt did not confirm execution")
    evidence["receipt"] = str(receipts[0])
    return evidence


def classify_findings(raw, inventory, manifest, verified_names, *, raw_exit):
    entries = _entries(manifest)
    if not isinstance(raw, dict) or not isinstance(raw.get("dependencies"), list) or raw.get("fixes") != []:
        raise ValueError("Missing complete unmodified pip-audit JSON")
    observed, findings = {}, []
    for dependency in raw["dependencies"]:
        name = _name(dependency.get("name"))
        if (name in observed or name not in inventory or "skip_reason" in dependency
                or dependency.get("version") != inventory[name] or not isinstance(dependency.get("vulns"), list)):
            raise ValueError("Audit inventory is skipped, duplicated, unexpected or version-mismatched")
        observed[name] = dependency["version"]
        for vulnerability in dependency["vulns"]:
            identifier = vulnerability.get("id")
            if not isinstance(identifier, str) or not identifier:
                raise ValueError("Invalid raw vulnerability")
            findings.append({"package": name, "id": identifier})
    if observed != inventory or raw_exit != (1 if findings else 0):
        raise ValueError("Audit coverage or raw exit status is incomplete")
    summary = {"raw_count": len(findings), "raw_exit": raw_exit,
               "raw_unique_count": len({(item["package"], item["id"]) for item in findings}),
               "old_deferred": [], "verified_remediations": [], "unresolved": []}
    for finding in findings:
        name, identifier = finding["package"], finding["id"]
        if name == "transformers" and identifier in OLD_TRANSFORMERS_DEFERRED:
            category = "old_deferred"
        elif name in verified_names and name in entries and identifier in entries[name]["advisories"]:
            category = "verified_remediations"
        else:
            category = "unresolved"
        summary[category].append(finding)
    return summary


def audit_repository(root, output_root):
    root = Path(root).resolve()
    folder = Path(output_root).resolve() / uuid.uuid4().hex
    folder.mkdir(parents=True)  # A fresh exclusive directory; no retry overwrites evidence.
    summary = {"raw_count": None, "raw_exit": None, "old_deferred": [], "verified_remediations": [],
               "unresolved": [], "checks_valid": False, "platform_gaps": [], "check_errors": []}
    stage = "inventory"
    try:
        manifest_path = root / "tools/security_patches/manifest.json"
        lock_path = root / "requirements.lock"
        inputs = {str(path): _sha256(path) for path in (manifest_path, lock_path)}
        manifest = _read_json(manifest_path)
        text, inventory = audit_inventory(lock_path.read_text(encoding="utf-8"), manifest)
        inventory_path = folder / "upstream-inventory.txt"
        with inventory_path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        if metadata.version("pip-audit") != "2.10.1":
            raise ValueError("Expected the reviewed pip-audit 2.10.1")
        stage = "raw_audit"
        raw_path = folder / "raw-audit.json"
        environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("PIP_AUDIT_")}
        command = [sys.executable, "-m", "pip_audit", "-r", str(inventory_path), "--no-deps", "--disable-pip",
                   "--strict", "--format", "json", "--aliases", "on", "--desc", "on",
                   "--vulnerability-service", "pypi",
                   "--output", str(raw_path)]
        with tempfile.TemporaryDirectory(prefix="pip-audit-http-") as cache:
            result = subprocess.run(command + ["--cache-dir", cache], cwd=root, env=environment,
                                    capture_output=True, timeout=300)
        summary["raw_exit"] = result.returncode
        with (folder / "raw-exit.json").open("x", encoding="utf-8") as stream:
            json.dump({"raw_exit": result.returncode}, stream)
        raw = _read_json(raw_path)
        raw_hash = _sha256(raw_path)
        stage = "raw_inventory_coverage"
        summary.update(classify_findings(raw, inventory, manifest, set(), raw_exit=result.returncode))
        stage = "installed_patch_provenance"
        verified = verify_installed_patches(manifest)
        stage = "security_regressions"
        evidence = run_security_tests(manifest, folder / "security_test_attempts")
        stage = "final_integrity"
        if set(verify_installed_patches(manifest)) != set(verified):
            raise ValueError("Installed security patch identity changed during regressions")
        if inputs != {path: _sha256(path) for path in inputs} or _sha256(raw_path) != raw_hash:
            raise ValueError("Audit input or original report changed during verification")
        summary.update(classify_findings(raw, inventory, manifest, set(verified), raw_exit=result.returncode))
        summary.update(checks_valid=True, platform_gaps=evidence["platform_gaps"], regressions=evidence,
                       source_hashes=inputs, raw_sha256=raw_hash)
    except (Exception, KeyboardInterrupt) as error:
        summary["check_errors"].append({"stage": stage, "type": type(error).__name__})
    summary["status"] = "PASS_WITH_DOCUMENTED_DEFERRALS" if summary["checks_valid"] and not summary["unresolved"] else "FAIL"
    with (folder / "summary.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(summary, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps({"evidence": str(folder), **summary}, sort_keys=True))
    return 0 if summary["checks_valid"] and not summary["unresolved"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "runtime_artifacts/validation/dependency_audit")
    args = parser.parse_args(argv)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return audit_repository(ROOT, args.output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
