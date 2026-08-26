from __future__ import annotations

import hashlib
import os
import runpy
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import workspace_test_sandbox as sandbox_module  # noqa: E402
from evelyn_core.workspace_test_sandbox import (  # noqa: E402
    CapacityOneWorker,
    WorkspaceSnapshotError,
    WorkspaceTestSandbox,
    attest_workspace_test_image_reference,
    reconcile_workspace_snapshot_root,
    workspace_stage_tree_digests,
    workspace_tree_digest,
)
from evelyn_core.host_supervisor import (  # noqa: E402
    HostSupervisor,
    WORKSPACE_SANDBOX_IMAGE_REFERENCE,
)


IMAGE_ID = f"sha256:{'a' * 64}"
CONTAINER_ID = "b" * 64
TARGET = "tests/runtime/test_widget.py"
BASE = b"VALUE = 1\n"
CANDIDATE = b"VALUE = 2\n"
RESULT_KEYS = {
    "attempted",
    "executed",
    "observed",
    "verified",
    "outcome",
    "code",
    "summary",
    "evidence",
}


def _completed(
    returncode: int = 0,
    stdout: str | bytes = "",
    stderr: str | bytes = "",
) -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class FakeCommands:
    def __init__(
        self,
        *,
        tracked: tuple[str, ...] = (TARGET,),
        tracked_stdout: bytes | None = None,
        exit_code: int = 0,
        stdout: str | None = None,
        stderr: str = "",
        timeout_on_create: bool = False,
        timeout_on_wait: bool = False,
        rm_fails: bool = False,
        create_stdout: str = CONTAINER_ID,
        create_returncode: int = 0,
        resolve_created: bool = True,
        inspect_image_id: str = IMAGE_ID,
        snapshot_check=None,
    ) -> None:
        self.tracked = tracked
        self.tracked_stdout = tracked_stdout
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.timeout_on_create = timeout_on_create
        self.timeout_on_wait = timeout_on_wait
        self.rm_fails = rm_fails
        self.create_stdout = create_stdout
        self.create_returncode = create_returncode
        self.resolve_created = resolve_created
        self.inspect_image_id = inspect_image_id
        self.snapshot_check = snapshot_check
        self.calls: list[list[str]] = []

    def __call__(self, command, **kwargs):
        command = list(command)
        self.calls.append(command)
        assert kwargs.get("capture_output") is True
        assert kwargs.get("check") is False
        assert kwargs.get("shell") is False
        assert isinstance(kwargs.get("timeout"), float)
        if command[0] == "git":
            assert kwargs.get("text") is False
            value = self.tracked_stdout
            if value is None:
                value = b"".join(path.encode("utf-8") + b"\x00" for path in self.tracked)
            return _completed(stdout=value, stderr=b"")
        if command[:3] == ["docker", "image", "inspect"]:
            return _completed(stdout=f"{self.inspect_image_id}\n")
        if command[:2] == ["docker", "ps"]:
            return _completed(stdout="")
        if command[:3] == ["docker", "container", "inspect"]:
            if not self.resolve_created:
                return _completed(returncode=1, stderr="not found")
            labels = [
                command[index + 1]
                for index, value in enumerate(command[:-1])
                if value == "--filter"
            ]
            del labels
            name = command[-1]
            create = next(
                call
                for call in reversed(self.calls[:-1])
                if call[:2] == ["docker", "create"] and name in call
            )
            project_label = next(
                value.split("=", 1)[1]
                for value in create
                if value.startswith("com.evelyn.workspace-test.project=")
            )
            role_label = next(
                value.split("=", 1)[1]
                for value in create
                if value.startswith("com.evelyn.workspace-test.role=")
            )
            return _completed(
                stdout=f"{CONTAINER_ID} 1 evelyn-host {project_label} {role_label}\n"
            )
        if command[:2] == ["docker", "create"]:
            if self.snapshot_check is not None and "--mount" in command:
                mount = command[command.index("--mount") + 1]
                source = mount.removeprefix("type=bind,source=").split(",target=", 1)[0]
                self.snapshot_check(Path(source), command)
            if self.timeout_on_create:
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            return _completed(
                returncode=self.create_returncode,
                stdout=f"{self.create_stdout}\n" if self.create_stdout else "",
            )
        if command[:2] == ["docker", "start"]:
            return _completed(stdout=f"{CONTAINER_ID}\n")
        if command[:2] == ["docker", "wait"]:
            if self.timeout_on_wait:
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            return _completed(stdout=f"{self.exit_code}\n")
        if command[:2] == ["docker", "logs"]:
            role_is_canary = any(
                "workspace-canary" in item
                for call in self.calls
                if call[:2] == ["docker", "create"]
                for item in call
            )
            return _completed(
                stdout="evelyn-workspace-sandbox-canary-v2\n"
                if role_is_canary
                else self.stdout
                if self.stdout is not None
                else (
                    "evelyn-workspace-test-runner-v1:passed:1\n"
                    if self.exit_code == 0
                    else "evelyn-workspace-test-runner-v1:failed:1\n"
                ),
                stderr="" if role_is_canary else self.stderr,
            )
        if command[:3] == ["docker", "rm", "-f"]:
            return _completed(
                returncode=1 if self.rm_fails else 0,
                stdout="" if self.rm_fails else f"{command[-1]}\n",
            )
        raise AssertionError(f"unexpected command: {command}")


class WorkspaceTestSandboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self._write("main.py", b"print('root')\n")
        self._write("evelyn_core/runtime/evelyn_core/widget.py", BASE)
        self._write(
            TARGET,
            b"import unittest\nfrom evelyn_core import widget\n"
            b"class WidgetTests(unittest.TestCase):\n"
            b"    def test_widget(self):\n        self.assertEqual(widget.VALUE, 2)\n",
        )
        self._write("tests/runtime/test_other.py", b"import unittest\n")
        self._write("docs/normal.md", b"safe\n")
        self._write("docs/untracked.md", b"UNTRACKED-DOC\n")
        self._write("docs/99_PROJECT_INBOX.md", b"PRIVATE-INBOX\n")
        self._write("docs/private/secret.txt", b"PRIVATE-DOC\n")
        self._write("external/vendor/allowed.py", b"TRACKED-EXTERNAL\n")
        self._write("external/vendor/widget.py", BASE)
        self._write("external/vendor/untracked.py", b"UNTRACKED-EXTERNAL\n")
        self._write("external/vendor/account_live-cache.json", b"AUTH-CACHE\n")
        self.workspace_manifest = frozenset(
            {
                "main.py",
                "evelyn_core",
                "evelyn_core/runtime",
                "evelyn_core/runtime/evelyn_core",
                "evelyn_core/runtime/evelyn_core/widget.py",
                "tests",
                "tests/runtime",
                TARGET,
                "tests/runtime/test_other.py",
                "docs",
                "docs/normal.md",
                "docs/99_PROJECT_INBOX.md",
                "external",
                "external/vendor",
                "external/vendor/allowed.py",
                "external/vendor/account_live-cache.json",
            }
        )
        self.stage = {
            "stageId": "stage-abc123",
            "path": "evelyn_core/runtime/evelyn_core/widget.py",
            "mode": "replace",
            "baseSha256": hashlib.sha256(BASE).hexdigest(),
            "candidateSha256": hashlib.sha256(CANDIDATE).hexdigest(),
            "candidateBytes": CANDIDATE,
            "requiresSandboxTest": True,
        }
        self.args = {"runner": "python_unittest", "targets": [TARGET]}
        self.snapshot_root = self.root / "artifacts" / "workspace_test_snapshots"
        self.assertTrue(
            reconcile_workspace_snapshot_root(self.root, self.snapshot_root)
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write(self, relative: str, data: bytes) -> None:
        path = self.root.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def _sandbox(self, runner: FakeCommands) -> WorkspaceTestSandbox:
        return WorkspaceTestSandbox(
            self.root,
            image_id=IMAGE_ID,
            attested_image_id=IMAGE_ID,
            canary_verified=True,
            run_command=runner,
            snapshot_root=self.snapshot_root,
            snapshot_reconciled=True,
            id_factory=lambda: "c" * 16,
        )

    def test_default_and_mismatched_attestation_never_call_commands(self) -> None:
        calls = 0

        def forbidden(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            raise AssertionError("command must not run")

        for sandbox in (
            WorkspaceTestSandbox(self.root, run_command=forbidden),
            WorkspaceTestSandbox(
                self.root,
                image_id=IMAGE_ID,
                attested_image_id=f"sha256:{'d' * 64}",
                canary_verified=True,
                run_command=forbidden,
            ),
        ):
            self.assertFalse(sandbox.ready)
            result = sandbox.run(
                stage=self.stage,
                args=self.args,
                external_tracked_paths=self.workspace_manifest,
            )
            self.assertEqual(result["code"], "workspace_test_sandbox_unavailable")
            self.assertEqual(set(result), RESULT_KEYS)
        self.assertEqual(calls, 0)

    def test_image_owned_runner_rejects_zero_skipped_and_abrupt_exit(self) -> None:
        runner = runpy.run_path(str(REPO_ROOT / "docker" / "workspace_test_runner.py"))
        execute = runner["_run_targets"]
        root = self.root / "runner-contract"
        root.mkdir()
        (root / "test_zero.py").write_text(
            "import unittest\nclass Zero(unittest.TestCase):\n    pass\n",
            encoding="utf-8",
        )
        (root / "test_pass.py").write_text(
            "import unittest\nclass Pass(unittest.TestCase):\n"
            "    def test_pass(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        (root / "test_skip.py").write_text(
            "import unittest\nclass Skip(unittest.TestCase):\n"
            "    @unittest.skip('contract')\n"
            "    def test_skip(self): pass\n",
            encoding="utf-8",
        )
        (root / "test_abrupt.py").write_text(
            "import os\nos._exit(0)\n",
            encoding="utf-8",
        )
        (root / "test_forged_receipt.py").write_text(
            "import os\nos._exit(65)\n",
            encoding="utf-8",
        )

        self.assertEqual(execute(["test_zero.py"], cwd=root), ("invalid", 0))
        self.assertEqual(execute(["test_pass.py"], cwd=root), ("passed", 1))
        self.assertEqual(execute(["test_skip.py"], cwd=root), ("invalid", 0))
        self.assertEqual(execute(["test_abrupt.py"], cwd=root), ("invalid", 0))
        # This deliberate limitation is why every sandbox receipt is bound to
        # semanticVerified:false and cannot complete a behavioral task.
        self.assertEqual(
            execute(["test_forged_receipt.py"], cwd=root),
            ("passed", 1),
        )

    def test_bot_api_image_installs_the_fixed_runner_executable(self) -> None:
        dockerfile = (REPO_ROOT / "docker" / "Dockerfile.bot-api").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "COPY docker/workspace_test_runner.py "
            "/usr/local/bin/evelyn-workspace-test-runner",
            dockerfile,
        )
        self.assertIn(
            "RUN chmod 0555 /usr/local/bin/evelyn-workspace-test-runner",
            dockerfile,
        )

    def test_pass_uses_candidate_snapshot_and_fixed_docker_boundary(self) -> None:
        captured_snapshot: list[Path] = []

        def inspect_snapshot(snapshot: Path, command: list[str]) -> None:
            captured_snapshot.append(snapshot)
            self.assertEqual(
                (snapshot / "evelyn_core/runtime/evelyn_core/widget.py").read_bytes(),
                CANDIDATE,
            )
            self.assertEqual(
                (snapshot / "external/vendor/allowed.py").read_bytes(),
                b"TRACKED-EXTERNAL\n",
            )
            for denied in (
                "docs/99_PROJECT_INBOX.md",
                "docs/private/secret.txt",
                "docs/untracked.md",
                "external/vendor/untracked.py",
                "external/vendor/account_live-cache.json",
            ):
                self.assertFalse(snapshot.joinpath(*denied.split("/")).exists())
            fixed_pairs = {
                "--network": "none",
                "--ipc": "none",
                "--user": "65534:65534",
                "--pids-limit": "64",
                "--memory": "512m",
                "--memory-swap": "512m",
                "--cpus": "1.0",
                "--security-opt": "no-new-privileges:true",
            }
            for option, expected in fixed_pairs.items():
                self.assertEqual(command[command.index(option) + 1], expected)
            self.assertIn("--read-only", command)
            self.assertEqual(command[command.index("--cap-drop") + 1], "ALL")
            self.assertIn("/app:rw,noexec,nosuid,nodev,size=1m,mode=755", command)
            self.assertIn("/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777", command)
            self.assertIn("max-size=64k", command)
            self.assertIn("max-file=1", command)
            self.assertIn("com.evelyn.workspace-test.owner=evelyn-host", command)
            self.assertIn("com.evelyn.workspace-test.role=candidate", command)
            self.assertTrue(
                any(value.startswith("com.evelyn.workspace-test.project=") for value in command)
            )
            self.assertIn(IMAGE_ID, command)
            self.assertEqual(
                command[-6:],
                [
                    IMAGE_ID,
                    "python-unittest",
                    "--protocol",
                    "evelyn-workspace-test-runner-v1",
                    "--",
                    TARGET,
                ],
            )
            self.assertEqual(
                command[command.index("--entrypoint") + 1],
                "/usr/local/bin/evelyn-workspace-test-runner",
            )

        runner = FakeCommands(snapshot_check=inspect_snapshot)
        result = self._sandbox(runner).run(
            stage=self.stage,
            args=self.args,
                external_tracked_paths=self.workspace_manifest,
        )
        self.assertEqual(result["outcome"], "succeeded")
        self.assertEqual(result["code"], "workspace_test_passed")
        self.assertTrue(result["verified"])
        self.assertIs(result["evidence"]["semanticVerified"], False)
        self.assertEqual(result["evidence"]["candidatePath"], self.stage["path"])
        self.assertEqual(result["evidence"]["candidateSha256"], self.stage["candidateSha256"])
        self.assertEqual(result["evidence"]["runner"], "python_unittest")
        self.assertRegex(result["evidence"]["baseTreeSha256"], r"^[a-f0-9]{64}$")
        self.assertRegex(result["evidence"]["candidateTreeSha256"], r"^[a-f0-9]{64}$")
        self.assertNotEqual(
            result["evidence"]["baseTreeSha256"],
            result["evidence"]["candidateTreeSha256"],
        )
        self.assertTrue(
            any(call == ["docker", "rm", "-f", CONTAINER_ID] for call in runner.calls)
        )
        self.assertEqual(len(captured_snapshot), 1)
        self.assertFalse(captured_snapshot[0].exists())

    def test_tree_digest_is_deterministic_and_accepts_manifest_ancestor_directories(self) -> None:
        first = workspace_tree_digest(
            self.root,
                external_tracked_paths=self.workspace_manifest,
        )
        second = workspace_tree_digest(
            self.root,
                external_tracked_paths=self.workspace_manifest,
        )
        candidate = workspace_tree_digest(
            self.root,
                external_tracked_paths=self.workspace_manifest,
            overlay_path=self.stage["path"],
            overlay_bytes=CANDIDATE,
        )
        self.assertEqual(first, second)
        self.assertNotEqual(first, candidate)

    def test_stage_digest_helper_matches_run_and_create_semantics(self) -> None:
        expected = workspace_stage_tree_digests(
            self.root,
            stage=self.stage,
            workspace_tracked_paths=self.workspace_manifest,
        )
        result = self._sandbox(FakeCommands()).run(
            stage=self.stage,
            args=self.args,
            external_tracked_paths=self.workspace_manifest,
        )
        for key, value in expected.items():
            self.assertEqual(result["evidence"][key], value)

        created = {
            **self.stage,
            "path": "evelyn_core/runtime/evelyn_core/new_widget.py",
            "mode": "create",
            "baseSha256": "ABSENT",
        }
        created_digests = workspace_stage_tree_digests(
            self.root,
            stage=created,
            workspace_tracked_paths=self.workspace_manifest,
        )
        self.assertEqual(
            created_digests["baseTreeSha256"],
            workspace_tree_digest(
                self.root,
                external_tracked_paths=self.workspace_manifest,
            ),
        )

    def test_untracked_external_candidate_is_the_only_manifest_exception(self) -> None:
        stage = {
            **self.stage,
            "path": "external/vendor/widget.py",
            "baseSha256": hashlib.sha256(BASE).hexdigest(),
        }
        expected = workspace_stage_tree_digests(
            self.root,
            stage=stage,
            workspace_tracked_paths=self.workspace_manifest,
        )
        result = self._sandbox(FakeCommands()).run(
            stage=stage,
            args=self.args,
            external_tracked_paths=self.workspace_manifest,
        )
        self.assertEqual(result["outcome"], "succeeded")
        self.assertEqual(result["evidence"]["baseTreeSha256"], expected["baseTreeSha256"])
        self.assertEqual(
            result["evidence"]["candidateTreeSha256"],
            expected["candidateTreeSha256"],
        )
        with self.assertRaises(WorkspaceSnapshotError):
            workspace_tree_digest(
                self.root,
                external_tracked_paths=self.workspace_manifest,
                overlay_path=stage["path"],
                overlay_bytes=CANDIDATE,
            )

    def test_only_exact_tracked_related_test_targets_are_accepted(self) -> None:
        cases = (
            (
                {"runner": "python_unittest", "targets": ["tests/runtime/helper.py"]},
                self.workspace_manifest,
                "workspace_test_target_invalid",
            ),
            (
                {"runner": "python_unittest", "targets": [TARGET]},
                self.workspace_manifest - {TARGET},
                "workspace_test_target_untracked",
            ),
            (
                {"runner": "python_unittest", "targets": ["tests/runtime/test_other.py"]},
                self.workspace_manifest,
                "workspace_test_target_unrelated",
            ),
            (
                {"runner": "unittest", "targets": [TARGET]},
                self.workspace_manifest,
                "workspace_test_request_invalid",
            ),
        )
        for args, manifest, code in cases:
            with self.subTest(code=code):
                result = self._sandbox(FakeCommands()).run(
                    stage=self.stage,
                    args=args,
                    external_tracked_paths=manifest,
                )
                self.assertEqual(result["outcome"], "blocked")
                self.assertEqual(result["code"], code)

    def test_manifest_case_aliases_fail_closed(self) -> None:
        result = self._sandbox(FakeCommands()).run(
            stage=self.stage,
            args=self.args,
            external_tracked_paths=self.workspace_manifest | {TARGET.upper()},
        )
        self.assertEqual(result["outcome"], "blocked")
        self.assertEqual(result["code"], "workspace_snapshot_manifest_invalid")

    def test_verified_failure_is_distinct_from_timeout_and_cleanup_uncertainty(self) -> None:
        failure = FakeCommands(exit_code=1, stderr="FAILED\n")
        result = self._sandbox(failure).run(
            stage=self.stage,
            args=self.args,
                external_tracked_paths=self.workspace_manifest,
        )
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["code"], "workspace_test_failed")
        self.assertTrue(result["verified"])

        timed_out = FakeCommands(timeout_on_wait=True)
        result = self._sandbox(timed_out).run(
            stage=self.stage,
            args=self.args,
                external_tracked_paths=self.workspace_manifest,
        )
        self.assertEqual(result["outcome"], "outcome_unverified")
        self.assertTrue(any(call[:3] == ["docker", "rm", "-f"] for call in timed_out.calls))

        cleanup_failed = FakeCommands(rm_fails=True)
        sandbox = self._sandbox(cleanup_failed)
        result = sandbox.run(
            stage=self.stage,
            args=self.args,
                external_tracked_paths=self.workspace_manifest,
        )
        self.assertEqual(result["outcome"], "outcome_unverified")
        self.assertEqual(result["code"], "workspace_test_cleanup_unverified")
        self.assertFalse(sandbox.ready)
        calls_after_failure = list(cleanup_failed.calls)
        blocked = sandbox.run(
            stage=self.stage,
            args=self.args,
            external_tracked_paths=self.workspace_manifest,
        )
        self.assertEqual(blocked["code"], "workspace_test_sandbox_unavailable")
        self.assertEqual(cleanup_failed.calls, calls_after_failure)

    def test_exit_zero_without_runner_owned_nonzero_test_count_is_unverified(self) -> None:
        values = (
            "",
            "Ran 1 test in 0.001s\n\nOK\n",
            "evelyn-workspace-test-runner-v1:passed:0\n",
        )
        for stdout in values:
            with self.subTest(stdout=stdout):
                result = self._sandbox(FakeCommands(stdout=stdout)).run(
                    stage=self.stage,
                    args=self.args,
                    external_tracked_paths=self.workspace_manifest,
                )
                self.assertEqual(result["outcome"], "outcome_unverified")
                self.assertEqual(
                    result["code"],
                    "workspace_test_runner_protocol_invalid",
                )

    def test_marker_write_failure_purges_partial_snapshot_and_keeps_ready(self) -> None:
        runner = FakeCommands()
        sandbox = self._sandbox(runner)
        with patch.object(
            sandbox_module,
            "_write_snapshot_marker",
            return_value=False,
        ):
            result = sandbox.run(
                stage=self.stage,
                args=self.args,
                external_tracked_paths=self.workspace_manifest,
            )

        self.assertEqual(result["code"], "workspace_snapshot_copy_failed")
        self.assertTrue(sandbox.ready)
        self.assertEqual(
            [
                path.name
                for path in self.snapshot_root.iterdir()
                if path.name != ".evelyn-workspace-snapshot-owner"
            ],
            [],
        )
        self.assertEqual(runner.calls, [])

    def test_marker_write_cleanup_failure_latches_sandbox_unavailable(self) -> None:
        runner = FakeCommands()
        sandbox = self._sandbox(runner)
        with (
            patch.object(
                sandbox_module,
                "_write_snapshot_marker",
                return_value=False,
            ),
            patch.object(
                sandbox_module,
                "_purge_owned_snapshot_directory",
                return_value=False,
            ),
        ):
            result = sandbox.run(
                stage=self.stage,
                args=self.args,
                external_tracked_paths=self.workspace_manifest,
            )

        self.assertEqual(result["outcome"], "outcome_unverified")
        self.assertEqual(result["code"], "workspace_test_snapshot_cleanup_unverified")
        self.assertFalse(result["verified"])
        self.assertFalse(sandbox.ready)
        calls_after_failure = list(runner.calls)
        blocked = sandbox.run(
            stage=self.stage,
            args=self.args,
            external_tracked_paths=self.workspace_manifest,
        )
        self.assertEqual(blocked["code"], "workspace_test_sandbox_unavailable")
        self.assertEqual(runner.calls, calls_after_failure)

    def test_host_restart_purges_only_owned_snapshot_orphans(self) -> None:
        sandbox = self._sandbox(FakeCommands())
        with patch.object(
            sandbox_module,
            "_purge_owned_snapshot_directory",
            return_value=False,
        ):
            result = sandbox.run(
                stage=self.stage,
                args=self.args,
                external_tracked_paths=self.workspace_manifest,
            )
        owned = [
            path
            for path in self.snapshot_root.iterdir()
            if path.name != ".evelyn-workspace-snapshot-owner"
        ]
        self.assertEqual(result["code"], "workspace_test_snapshot_cleanup_unverified")
        self.assertEqual(len(owned), 1)
        self.assertTrue(
            owned[0]
            .joinpath(*("snapshot/" + self.stage["path"]).split("/"))
            .exists()
        )
        self.assertNotIn(str(self.snapshot_root), str(result))
        self.assertFalse(sandbox.ready)
        self.assertEqual(
            sandbox.run(
                stage=self.stage,
                args=self.args,
                external_tracked_paths=self.workspace_manifest,
            )["code"],
            "workspace_test_sandbox_unavailable",
        )

        self.assertTrue(
            reconcile_workspace_snapshot_root(self.root, self.snapshot_root)
        )
        self.assertEqual(
            [
                path.name
                for path in self.snapshot_root.iterdir()
                if path.name != ".evelyn-workspace-snapshot-owner"
            ],
            [],
        )

    def test_snapshot_purge_keeps_owner_marker_until_all_read_only_files_are_removed(self) -> None:
        with patch.object(
            sandbox_module,
            "_purge_owned_snapshot_directory",
            return_value=False,
        ):
            self._sandbox(FakeCommands()).run(
                stage=self.stage,
                args=self.args,
                external_tracked_paths=self.workspace_manifest,
            )
        owned = next(
            path
            for path in self.snapshot_root.iterdir()
            if path.name != ".evelyn-workspace-snapshot-owner"
        )
        owner_marker = owned / ".evelyn-workspace-snapshot-owner"
        original_unlink = Path.unlink

        def fail_main(path: Path, *args, **kwargs) -> None:
            if path.name == "main.py":
                raise OSError("injected mid-purge failure")
            original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", fail_main):
            self.assertFalse(
                sandbox_module._purge_owned_snapshot_directory(
                    owned,
                    marker=sandbox_module._snapshot_marker_bytes(
                        sandbox_module._project_scope(self.root)
                    ),
                )
            )
        self.assertTrue(owner_marker.is_file())
        self.assertTrue(
            reconcile_workspace_snapshot_root(self.root, self.snapshot_root)
        )
        self.assertFalse(owned.exists())

    def test_partial_create_resolves_labeled_container_to_exact_id_before_cleanup(self) -> None:
        runner = FakeCommands(create_stdout="not-an-id")
        result = self._sandbox(runner).run(
            stage=self.stage,
            args=self.args,
                external_tracked_paths=self.workspace_manifest,
        )
        self.assertEqual(result["outcome"], "outcome_unverified")
        self.assertTrue(any(call[:3] == ["docker", "container", "inspect"] for call in runner.calls))
        self.assertIn(["docker", "rm", "-f", CONTAINER_ID], runner.calls)

        runner = FakeCommands(create_stdout="", create_returncode=1)
        result = self._sandbox(runner).run(
            stage=self.stage,
            args=self.args,
                external_tracked_paths=self.workspace_manifest,
        )
        self.assertEqual(result["outcome"], "blocked")
        self.assertIn(["docker", "rm", "-f", CONTAINER_ID], runner.calls)

    def test_create_timeout_resolves_exact_labels_before_cleanup(self) -> None:
        runner = FakeCommands(timeout_on_create=True)
        result = self._sandbox(runner).run(
            stage=self.stage,
            args=self.args,
            external_tracked_paths=self.workspace_manifest,
        )
        self.assertEqual(result["outcome"], "blocked")
        self.assertTrue(
            any(call[:3] == ["docker", "container", "inspect"] for call in runner.calls)
        )
        self.assertIn(["docker", "rm", "-f", CONTAINER_ID], runner.calls)

        unresolved = FakeCommands(timeout_on_create=True, resolve_created=False)
        result = self._sandbox(unresolved).run(
            stage=self.stage,
            args=self.args,
            external_tracked_paths=self.workspace_manifest,
        )
        self.assertEqual(result["outcome"], "outcome_unverified")
        self.assertEqual(result["code"], "workspace_test_cleanup_unverified")

    def test_output_is_capped(self) -> None:
        runner = FakeCommands(exit_code=1, stdout="x" * 100_000, stderr="y" * 100_000)
        result = self._sandbox(runner).run(
            stage=self.stage,
            args=self.args,
                external_tracked_paths=self.workspace_manifest,
        )
        self.assertTrue(result["evidence"]["outputTruncated"])
        self.assertLessEqual(len(result["evidence"]["stdout"].encode("utf-8")), 8192)
        self.assertLessEqual(len(result["evidence"]["stderr"].encode("utf-8")), 8192)

    def test_hardlinked_allowed_file_blocks_snapshot_before_docker(self) -> None:
        linked = self.root / "docs" / "linked-main.py"
        try:
            os.link(self.root / "main.py", linked)
        except OSError as exc:
            self.skipTest(f"hard links unavailable: {exc}")
        runner = FakeCommands()
        result = self._sandbox(runner).run(
            stage=self.stage,
            args=self.args,
                external_tracked_paths=self.workspace_manifest,
        )
        self.assertEqual(result["outcome"], "blocked")
        self.assertEqual(result["code"], "workspace_snapshot_hardlink_denied")
        self.assertFalse(any(call[:2] == ["docker", "create"] for call in runner.calls))

    def test_image_reference_attestation_inspects_reconciles_and_runs_canary(self) -> None:
        runner = FakeCommands()
        attestation = attest_workspace_test_image_reference(
            project_root=self.root,
            image_reference="evelyn-fast-control-discord_bot:latest",
            run_command=runner,
            id_factory=lambda: "d" * 16,
        )
        self.assertEqual(
            attestation,
            {
                "ready": True,
                "imageId": IMAGE_ID,
                "canaryVerified": True,
                "semanticVerified": False,
                "code": "workspace_test_image_attested",
            },
        )
        ps_calls = [call for call in runner.calls if call[:2] == ["docker", "ps"]]
        self.assertEqual(len(ps_calls), 2)
        self.assertTrue(all("--no-trunc" in call for call in ps_calls))
        self.assertTrue(all(any("project=" in value for value in call) for call in ps_calls))
        self.assertTrue(any("role=candidate" in value for value in ps_calls[0]))
        self.assertTrue(any("role=canary" in value for value in ps_calls[1]))
        create = next(call for call in runner.calls if call[:2] == ["docker", "create"])
        self.assertIn("com.evelyn.workspace-test.role=canary", create)
        self.assertIn("--ipc", create)
        self.assertIn(IMAGE_ID, create)
        self.assertIn(["docker", "rm", "-f", CONTAINER_ID], runner.calls)

    def test_image_reference_attestation_fails_closed(self) -> None:
        bad_id = FakeCommands(inspect_image_id="mutable-tag")
        result = attest_workspace_test_image_reference(
            project_root=self.root,
            image_reference="evelyn-fast-control-discord_bot:latest",
            run_command=bad_id,
        )
        self.assertFalse(result["ready"])
        self.assertFalse(any(call[:2] == ["docker", "create"] for call in bad_id.calls))

        cleanup_failed = FakeCommands(rm_fails=True)
        result = attest_workspace_test_image_reference(
            project_root=self.root,
            image_reference="evelyn-fast-control-discord_bot:latest",
            run_command=cleanup_failed,
            id_factory=lambda: "e" * 16,
        )
        self.assertFalse(result["ready"])
        self.assertFalse(result["canaryVerified"])

    def test_host_attests_only_the_fixed_image_reference(self) -> None:
        runner = FakeCommands()
        supervisor = HostSupervisor(
            project_root=self.root,
            artifacts_root=self.root / "artifacts",
            run_command=runner,
            retention_reporter=SimpleNamespace(),
            process_owner=SimpleNamespace(),
            bridge_lock_probe=lambda: True,
            workspace_sandbox_auth_token="s" * 32,
        )
        self.addCleanup(supervisor._workspace_test_worker.close)

        self.assertTrue(supervisor.workspace_test_sandbox.ready)
        self.assertIs(supervisor.workspace_test_sandbox.semantic_verified, False)
        inspect_calls = [
            call for call in runner.calls if call[:3] == ["docker", "image", "inspect"]
        ]
        self.assertEqual(len(inspect_calls), 2)
        self.assertTrue(
            all(call[-1] == WORKSPACE_SANDBOX_IMAGE_REFERENCE for call in inspect_calls)
        )
        self.assertEqual(supervisor.workspace_test_sandbox.image_id, IMAGE_ID)
        supervisor.retention_reporter.status = lambda: {}
        self.assertIs(supervisor.status()["workspaceSandboxSemanticVerified"], False)

    def test_host_ignores_forged_attestation_environment(self) -> None:
        runner = FakeCommands(inspect_image_id="mutable-tag")
        forged = {
            "EVELYN_WORKSPACE_SANDBOX_IMAGE_ID": IMAGE_ID,
            "EVELYN_WORKSPACE_SANDBOX_ATTESTED_IMAGE_ID": IMAGE_ID,
            "EVELYN_WORKSPACE_SANDBOX_CANARY_VERIFIED": "1",
        }
        with patch.dict(os.environ, forged, clear=False):
            supervisor = HostSupervisor(
                project_root=self.root,
                artifacts_root=self.root / "artifacts",
                run_command=runner,
                retention_reporter=SimpleNamespace(),
                process_owner=SimpleNamespace(),
                bridge_lock_probe=lambda: True,
                workspace_sandbox_auth_token="s" * 32,
            )
        self.addCleanup(supervisor._workspace_test_worker.close)

        self.assertFalse(supervisor.workspace_test_sandbox.ready)
        self.assertFalse(any(call[:2] == ["docker", "create"] for call in runner.calls))

    def test_host_without_sandbox_authority_never_probes_docker(self) -> None:
        runner = FakeCommands()
        supervisor = HostSupervisor(
            project_root=self.root,
            artifacts_root=self.root / "artifacts",
            run_command=runner,
            retention_reporter=SimpleNamespace(),
            process_owner=SimpleNamespace(),
            bridge_lock_probe=lambda: True,
            workspace_sandbox_auth_token="",
        )
        self.addCleanup(supervisor._workspace_test_worker.close)

        self.assertFalse(supervisor.workspace_test_sandbox.ready)
        self.assertFalse(any(call[0] == "docker" for call in runner.calls))

    def test_host_restart_purges_owned_orphan_even_without_sandbox_authority(self) -> None:
        artifacts = self.root / "host-artifacts"
        snapshot_root = artifacts / "host_supervisor" / "workspace_test_snapshots"
        self.assertTrue(reconcile_workspace_snapshot_root(self.root, snapshot_root))
        sandbox = WorkspaceTestSandbox(
            self.root,
            image_id=IMAGE_ID,
            attested_image_id=IMAGE_ID,
            canary_verified=True,
            run_command=FakeCommands(),
            snapshot_root=snapshot_root,
            snapshot_reconciled=True,
            id_factory=lambda: "f" * 16,
        )
        with patch.object(
            sandbox_module,
            "_purge_owned_snapshot_directory",
            return_value=False,
        ):
            self.assertEqual(
                sandbox.run(
                    stage=self.stage,
                    args=self.args,
                    external_tracked_paths=self.workspace_manifest,
                )["code"],
                "workspace_test_snapshot_cleanup_unverified",
            )
        self.assertGreater(len(list(snapshot_root.iterdir())), 1)

        runner = FakeCommands()
        supervisor = HostSupervisor(
            project_root=self.root,
            artifacts_root=artifacts,
            run_command=runner,
            retention_reporter=SimpleNamespace(),
            process_owner=SimpleNamespace(),
            bridge_lock_probe=lambda: True,
            workspace_sandbox_auth_token="",
        )
        self.addCleanup(supervisor._workspace_test_worker.close)

        self.assertFalse(supervisor.workspace_test_sandbox.ready)
        self.assertFalse(any(call[0] == "docker" for call in runner.calls))
        self.assertEqual(
            [
                path.name
                for path in snapshot_root.iterdir()
                if path.name != ".evelyn-workspace-snapshot-owner"
            ],
            [],
        )

    def test_host_does_not_advertise_ready_without_a_tracked_test_manifest(self) -> None:
        runner = FakeCommands(tracked_stdout=b"")
        supervisor = HostSupervisor(
            project_root=self.root,
            artifacts_root=self.root / "artifacts",
            run_command=runner,
            retention_reporter=SimpleNamespace(),
            process_owner=SimpleNamespace(),
            bridge_lock_probe=lambda: True,
            workspace_sandbox_auth_token="s" * 32,
        )
        self.addCleanup(supervisor._workspace_test_worker.close)

        self.assertFalse(supervisor._workspace_sandbox_ready())
        self.assertFalse(any(call[0] == "docker" for call in runner.calls))


class CapacityOneWorkerTests(unittest.TestCase):
    def test_capacity_poll_and_clean_close(self) -> None:
        release = threading.Event()

        def operation(*, value: int) -> dict:
            release.wait(1.0)
            return {"value": value}

        worker = CapacityOneWorker(operation)
        self.assertTrue(worker.submit("job-1", value=7))
        self.assertTrue(worker.busy)
        self.assertFalse(worker.submit("job-2", value=8))
        self.assertIsNone(worker.poll("job-1"))
        self.assertFalse(worker.close())
        release.set()
        deadline = time.monotonic() + 1.0
        result = None
        while result is None and time.monotonic() < deadline:
            result = worker.poll("job-1")
            if result is None:
                time.sleep(0.001)
        self.assertEqual(result, {"value": 7})
        self.assertFalse(worker.busy)
        self.assertTrue(worker.close())
        self.assertFalse(worker.submit("job-3", value=9))


if __name__ == "__main__":
    unittest.main()
