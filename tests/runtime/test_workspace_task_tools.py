from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import stat
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

from evelyn_core.host_supervisor import HostSupervisor  # noqa: E402
from evelyn_core.host_supervisor_client import (  # noqa: E402
    SUPERVISOR_REQUEST_SCHEMA,
    SUPERVISOR_STATUS_SCHEMA,
)
from evelyn_core.runtime_artifact_io import atomic_json_write  # noqa: E402
from evelyn_core import workspace_task_tools as workspace_tools  # noqa: E402
from evelyn_core.workspace_task_tools import (  # noqa: E402
    WORKSPACE_EDIT_MAX_PREVIEW_BYTES,
    WORKSPACE_MUTATION_REQUEST_AUTH_DOMAIN,
    WORKSPACE_MUTATION_REQUEST_SCHEMA,
    WORKSPACE_MUTATION_RESPONSE_AUTH_DOMAIN,
    WORKSPACE_MUTATION_RESPONSE_SCHEMA,
    WORKSPACE_SANDBOX_AUTH_DOMAIN,
    WORKSPACE_TASK_AUTH_ALGORITHM,
    WORKSPACE_TASK_COMMAND_TIMEOUT_SEC,
    WORKSPACE_TASK_MAX_OUTPUT_BYTES,
    WORKSPACE_TASK_MAX_REQUEST_BYTES,
    WORKSPACE_TASK_MAX_RESPONSE_BYTES,
    WORKSPACE_TASK_REQUEST_AUTH_DOMAIN,
    WORKSPACE_TASK_REQUEST_SCHEMA,
    WORKSPACE_TASK_RESPONSE_AUTH_DOMAIN,
    WORKSPACE_TASK_RESPONSE_SCHEMA,
    WORKSPACE_TASK_TOOL_NAMES,
    WorkspaceMutationHostClient,
    WorkspaceTaskHostClient,
    build_workspace_tracked_manifest,
    execute_workspace_task_tool,
    handle_workspace_mutation_request,
    handle_workspace_task_request,
    stage_workspace_edit,
)
from evelyn_core.workspace_test_sandbox import (  # noqa: E402
    workspace_stage_tree_digests,
    workspace_tree_digest,
)


AUTH_TOKEN = "workspace-task-auth-token-0123456789abcdef"
SANDBOX_TOKEN = "workspace-sandbox-auth-token-0123456789abcdef"
MUTATION_TOKEN = "workspace-mutation-auth-token-0123456789abcdef"
HOST_INSTANCE_ID = "host-instance-test"


def sign_payload(payload: dict, *, domain: bytes, token: str = AUTH_TOKEN) -> dict:
    signed = {**payload, "authAlgorithm": WORKSPACE_TASK_AUTH_ALGORITHM}
    digest = hmac.new(token.encode(), digestmod=hashlib.sha256)
    digest.update(domain)
    digest.update(
        json.dumps(
            signed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    return {**signed, "authTag": digest.hexdigest()}


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


class FakeProcessOwner:
    mode = "test"
    ready = True

    def close(self) -> None:
        self.ready = False


class FakeRetentionReporter:
    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def status(self) -> dict:
        return {"state": "clear"}


class FakeWorkspaceTestSandbox:
    def __init__(self, *, ready: bool = False, result=None) -> None:
        self.ready = ready
        self.result = result
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def run(self, *, stage, args, external_tracked_paths) -> dict:
        self.calls += 1
        self.started.set()
        self.release.wait(1.0)
        if callable(self.result):
            return self.result(stage, args, external_tracked_paths)
        if isinstance(self.result, dict):
            return dict(self.result)
        return {
            "attempted": True,
            "executed": False,
            "observed": True,
            "verified": True,
            "outcome": "blocked",
            "code": "workspace_test_sandbox_unavailable",
            "summary": "sandbox unavailable",
            "evidence": {},
        }


def sandbox_pass(stage: dict, args: dict, _tracked_paths: frozenset[str]) -> dict:
    return {
        "attempted": True,
        "executed": True,
        "observed": True,
        "verified": True,
        "outcome": "succeeded",
        "code": "workspace_test_passed",
        "summary": "passed",
        "evidence": {
            "stageId": stage["stageId"],
            "candidatePath": stage["path"],
            "candidateSha256": stage["candidateSha256"],
            "baseTreeSha256": "a" * 64,
            "candidateTreeSha256": "b" * 64,
            "runner": "python_unittest",
            "targets": list(args["targets"]),
            "testsRun": 1,
            "semanticVerified": False,
            "exitCode": 0,
        },
    }


class WorkspaceTaskToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.project_root = Path(self.temp_dir.name) / "project"
        self.project_root.mkdir()
        self.artifacts_root = Path(self.temp_dir.name) / "artifacts"
        self.commands: list[tuple[list[str], dict]] = []
        self.stages: dict[str, dict] = {}

    def run_command(self, command, **kwargs):
        self.commands.append((list(command), dict(kwargs)))
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    def git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=False,
        )

    def init_git(self) -> None:
        self.assertEqual(self.git("init", "-q").returncode, 0)
        self.assertEqual(self.git("config", "user.email", "test@example.invalid").returncode, 0)
        self.assertEqual(self.git("config", "user.name", "Evelyn Test").returncode, 0)

    def behavioral_supervisor(
        self,
        sandbox: FakeWorkspaceTestSandbox,
    ) -> tuple[HostSupervisor, dict]:
        self.init_git()
        target = self.project_root / "evelyn_core" / "module.py"
        target.parent.mkdir(exist_ok=True)
        target.write_text("before\n", encoding="utf-8")
        test_target = self.project_root / "tests" / "test_module.py"
        test_target.parent.mkdir(exist_ok=True)
        test_target.write_text("import unittest\n", encoding="utf-8")
        self.assertEqual(self.git("add", ".").returncode, 0)
        self.assertEqual(self.git("commit", "-qm", "base").returncode, 0)
        supervisor = HostSupervisor(
            project_root=self.project_root,
            artifacts_root=self.artifacts_root,
            run_command=subprocess.run,
            now=lambda: 1001.0,
            retention_reporter=FakeRetentionReporter(),
            process_owner=FakeProcessOwner(),
            bridge_lock_probe=lambda: True,
            workspace_task_auth_token=AUTH_TOKEN,
            workspace_mutation_auth_token=MUTATION_TOKEN,
            workspace_sandbox_auth_token=SANDBOX_TOKEN,
            workspace_test_sandbox=sandbox,
        )
        self.addCleanup(supervisor._workspace_test_worker.close)
        self.addCleanup(sandbox.release.set)
        request = self.request(
            tool="edit",
            args={
                "mode": "replace",
                "path": "evelyn_core/module.py",
                "oldText": "before",
                "newText": "after",
                "expectedSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            },
            requestId="edit-request",
            hostInstanceId=supervisor.host_instance_id,
            actionRunId="edit-action",
            requiresSandboxTest=True,
            issuedAt=1001.0,
            expiresAt=1010.0,
        )
        atomic_json_write(supervisor.requests_dir / "edit-request.json", request)
        supervisor.process_request_queue()
        response = json.loads(
            (supervisor.responses_dir / "edit-request.json").read_text(encoding="utf-8")
        )
        self.assertEqual(response["result"]["code"], "workspace_edit_staged")
        return supervisor, response["result"]["evidence"]

    def submit_behavioral_test(
        self,
        supervisor: HostSupervisor,
        stage_id: str,
        *,
        request_id: str = "test-request",
        action_run_id: str = "test-action",
    ) -> dict:
        request = self.request(
            tool="test",
            args={
                "runner": "python_unittest",
                "targets": ["tests/test_module.py"],
            },
            requestId=request_id,
            hostInstanceId=supervisor.host_instance_id,
            actionRunId=action_run_id,
            stepId=2,
            requiresSandboxTest=True,
            candidateStageId=stage_id,
            issuedAt=1001.0,
            expiresAt=1010.0,
        )
        atomic_json_write(supervisor.requests_dir / f"{request_id}.json", request)
        supervisor.process_request_queue()
        return request

    def stage(self, args: dict, *, at: float = 1000.0) -> dict:
        return stage_workspace_edit(
            project_root=self.project_root,
            args=args,
            task_id="task-1",
            grant_id="grant-1",
            action_run_id="action-1",
            step_id=1,
            surface="control_page",
            host_instance_id=HOST_INSTANCE_ID,
            stages=self.stages,
            now=lambda: at,
        )

    def mutation_request(
        self,
        evidence: dict,
        *,
        operation: str = "apply",
        dirty_acknowledged: bool = False,
        request_id: str = "mutation-1",
        token: str = MUTATION_TOKEN,
        grant_expires_at: float | None = None,
    ) -> dict:
        issued_at = max(1001.0, float(evidence["issuedAt"]) + 0.1)
        resolved_grant_expires_at = (
            issued_at + 30.0
            if grant_expires_at is None
            else float(grant_expires_at)
        )
        return sign_payload(
            {
                "schema": WORKSPACE_MUTATION_REQUEST_SCHEMA,
                "operation": operation,
                "requestId": request_id,
                "approvalId": "approval-1",
                "claimId": "claim-1",
                "stageId": evidence["stageId"],
                "hostInstanceId": evidence["hostInstanceId"],
                "taskId": "task-1",
                "grantId": "grant-1",
                "grantExpiresAt": resolved_grant_expires_at,
                "actionRunId": "action-1",
                "stepId": 1,
                "surface": "control_page",
                "tool": "edit",
                "argsHash": evidence["argsHash"],
                "baseSha256": evidence["baseSha256"],
                "candidateSha256": evidence["candidateSha256"],
                "previewDigest": evidence["previewDigest"],
                "dirtyBaseAcknowledged": dirty_acknowledged,
                "issuedAt": issued_at,
                "expiresAt": issued_at + 9.0,
            },
            domain=WORKSPACE_MUTATION_REQUEST_AUTH_DOMAIN,
            token=token,
        )

    def mutate(
        self,
        request: dict,
        *,
        consumed=None,
        at: float = 1002.0,
        external_tracked_paths: frozenset[str] | None = None,
    ) -> dict:
        return handle_workspace_mutation_request(
            request,
            project_root=self.project_root,
            host_instance_id=HOST_INSTANCE_ID,
            host_started_at=999.0,
            stages=self.stages,
            auth_token=MUTATION_TOKEN,
            request_filename=f"{request.get('requestId', '')}.json",
            consumed_request_ids=consumed,
            external_tracked_paths=external_tracked_paths,
            now=lambda: at,
        )

    def execute(self, tool: str, args: dict) -> dict:
        result = execute_workspace_task_tool(
            project_root=self.project_root,
            tool=tool,
            args=args,
            run_command=self.run_command,
        )
        self.assertEqual(set(result), RESULT_KEYS)
        return result

    def request(self, *, tool: str, args: dict, **overrides) -> dict:
        payload = {
            "schema": WORKSPACE_TASK_REQUEST_SCHEMA,
            "hostInstanceId": HOST_INSTANCE_ID,
            "requestId": "request-1",
            "taskId": "task-1",
            "grantId": "grant-1",
            "actionRunId": "action-1",
            "stepId": 1,
            "surface": "control_page",
            "tool": tool,
            "requiresSandboxTest": False,
            "candidateStageId": "",
            "args": args,
            "argsHash": hashlib.sha256(
                json.dumps(
                    args,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
            "issuedAt": 1000.0,
            "expiresAt": 1010.0,
        }
        payload.update(overrides)
        payload = workspace_tools._sign_workspace_sandbox_authority(
            payload,
            auth_token=SANDBOX_TOKEN,
        )
        return sign_payload(payload, domain=WORKSPACE_TASK_REQUEST_AUTH_DOMAIN)

    def handle(
        self,
        request: dict,
        *,
        at: float = 1001.0,
        consumed=None,
        staged_edits=None,
        workspace_test_executor=None,
        sandbox_ready: bool = False,
    ) -> dict:
        return handle_workspace_task_request(
            request,
            project_root=self.project_root,
            host_instance_id=HOST_INSTANCE_ID,
            host_started_at=999.0,
            auth_token=AUTH_TOKEN,
            sandbox_auth_token=SANDBOX_TOKEN,
            request_filename=f"{request.get('requestId', '')}.json",
            consumed_request_ids=consumed,
            staged_edits=staged_edits,
            workspace_test_executor=workspace_test_executor,
            sandbox_ready=sandbox_ready,
            now=lambda: at,
        )

    def test_tool_names_are_exact_and_read_search_list_outputs_are_bounded(self) -> None:
        self.assertEqual(
            WORKSPACE_TASK_TOOL_NAMES,
            frozenset({"list", "search", "read", "edit", "test", "diff"}),
        )
        source = self.project_root / "evelyn_core"
        source.mkdir()
        (source / "app.py").write_text(
            "needle = 'value'\n" + ("x" * (WORKSPACE_TASK_MAX_OUTPUT_BYTES * 2)),
            encoding="utf-8",
        )
        (self.project_root / ".env").write_text("SECRET=private", encoding="utf-8")

        listed = self.execute("list", {"path": ".", "recursive": True})
        searched = self.execute("search", {"path": ".", "query": "needle"})
        read = self.execute("read", {"path": "evelyn_core/app.py"})

        self.assertTrue(listed["verified"])
        self.assertEqual(listed["evidence"]["path"], ".")
        self.assertIs(listed["evidence"]["recursive"], True)
        self.assertNotIn(".env", json.dumps(listed["evidence"]))
        self.assertEqual(searched["evidence"]["path"], ".")
        self.assertEqual(searched["evidence"]["query"], "needle")
        self.assertEqual(
            searched["evidence"]["matches"][0]["path"],
            "evelyn_core/app.py",
        )
        self.assertLessEqual(
            len(read["evidence"]["content"].encode("utf-8")),
            WORKSPACE_TASK_MAX_OUTPUT_BYTES,
        )
        self.assertTrue(read["evidence"]["truncated"])

    def test_workspace_read_returns_sha_bound_utf8_chunks_below_model_limit(self) -> None:
        target = self.project_root / "docs" / "long.md"
        target.parent.mkdir()
        target.write_text(("가나다 abc \\\"line\\\"\n" * 200), encoding="utf-8")
        raw = target.read_bytes()
        expected_sha = hashlib.sha256(raw).hexdigest()

        first = self.execute("read", {"path": "docs/long.md"})
        evidence = first["evidence"]

        self.assertEqual(evidence["path"], "docs/long.md")
        self.assertEqual(evidence["sha256"], expected_sha)
        self.assertEqual(evidence["bytes"], len(raw))
        self.assertEqual(evidence["offset"], 0)
        self.assertEqual(evidence["length"], len(evidence["content"].encode("utf-8")))
        self.assertEqual(evidence["nextOffset"], evidence["length"])
        self.assertFalse(evidence["eof"])
        self.assertTrue(evidence["truncated"])
        self.assertGreater(evidence["length"], 0)
        self.assertLessEqual(
            len(
                json.dumps(
                    evidence,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            ),
            workspace_tools._MAX_READ_EVIDENCE_CHARS,
        )
        encoded_evidence = json.dumps(
            evidence,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self.assertLessEqual(
            len(json.dumps(encoded_evidence, ensure_ascii=False)),
            workspace_tools._MAX_READ_EVIDENCE_CHARS,
        )

        second = self.execute(
            "read",
            {
                "path": "docs/long.md",
                "offset": evidence["nextOffset"],
                "length": workspace_tools._MAX_READ_CHUNK_BYTES,
                "expectedSha256": expected_sha,
            },
        )["evidence"]
        self.assertEqual(second["offset"], evidence["nextOffset"])
        self.assertEqual(
            second["content"],
            raw[second["offset"] : second["nextOffset"]].decode("utf-8"),
        )
        self.assertEqual(second["sha256"], expected_sha)
        self.assertLessEqual(second["length"], workspace_tools._MAX_READ_CHUNK_BYTES)
        self.assertEqual(second["nextOffset"], second["offset"] + second["length"])

    def test_workspace_read_continuation_fails_closed_after_file_changes(self) -> None:
        target = self.project_root / "docs" / "long.md"
        target.parent.mkdir()
        target.write_text("before\n" * 400, encoding="utf-8")
        first = self.execute("read", {"path": "docs/long.md"})["evidence"]
        target.write_text("after\n" * 400, encoding="utf-8")

        changed = self.execute(
            "read",
            {
                "path": "docs/long.md",
                "offset": first["nextOffset"],
                "length": workspace_tools._MAX_READ_CHUNK_BYTES,
                "expectedSha256": first["sha256"],
            },
        )

        self.assertEqual(changed["code"], "workspace_read_sha256_mismatch")
        self.assertFalse(changed["executed"])
        self.assertEqual(changed["evidence"], {})

    def test_workspace_read_chunk_bound_uses_actual_json_escaping_and_empty_eof(self) -> None:
        docs = self.project_root / "docs"
        docs.mkdir()
        escaped = docs / "escaped.md"
        escaped.write_text("\x01" * 2_000, encoding="utf-8")

        evidence = self.execute("read", {"path": "docs/escaped.md"})["evidence"]

        self.assertGreater(evidence["length"], 0)
        self.assertLessEqual(
            len(json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))),
            workspace_tools._MAX_READ_EVIDENCE_CHARS,
        )
        encoded_evidence = json.dumps(
            evidence,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self.assertLessEqual(
            len(json.dumps(encoded_evidence, ensure_ascii=False)),
            workspace_tools._MAX_READ_EVIDENCE_CHARS,
        )
        self.assertEqual(
            evidence["content"],
            escaped.read_bytes()[: evidence["nextOffset"]].decode("utf-8"),
        )

        empty = docs / "empty.md"
        empty.write_bytes(b"")
        empty_evidence = self.execute("read", {"path": "docs/empty.md"})["evidence"]
        self.assertEqual(
            (
                empty_evidence["bytes"],
                empty_evidence["offset"],
                empty_evidence["length"],
                empty_evidence["nextOffset"],
                empty_evidence["eof"],
                empty_evidence["truncated"],
            ),
            (0, 0, 0, 0, True, False),
        )

    def test_workspace_read_fails_closed_when_path_identity_changes_during_read(self) -> None:
        target = self.project_root / "docs" / "race.md"
        target.parent.mkdir()
        target.write_text("stable\n", encoding="utf-8")
        real_stat = os.stat
        calls = 0

        def changed_second_stat(path, *, follow_symlinks=True):
            nonlocal calls
            value = real_stat(path, follow_symlinks=follow_symlinks)
            calls += 1
            if calls == 2:
                return SimpleNamespace(
                    st_dev=value.st_dev,
                    st_ino=value.st_ino + 1,
                    st_mode=value.st_mode,
                    st_nlink=value.st_nlink,
                )
            return value

        with patch.object(workspace_tools.os, "stat", changed_second_stat):
            with self.assertRaisesRegex(
                workspace_tools._WorkspaceTaskError,
                "workspace_read_identity_changed",
            ):
                workspace_tools._read_text_file(target)

    @unittest.skipUnless(os.name == "nt", "Windows directory-handle pinning")
    def test_workspace_ancestor_handles_block_directory_swap_during_operation(self) -> None:
        target = self.project_root / "docs" / "nested" / "race.md"
        target.parent.mkdir(parents=True)
        target.write_text("stable\n", encoding="utf-8")

        with workspace_tools._pinned_workspace_ancestors(self.project_root, target):
            with self.assertRaises(OSError):
                (self.project_root / "docs").rename(self.project_root / "moved")

        self.assertTrue(target.is_file())

    @unittest.skipUnless(os.name == "nt", "Windows directory-handle pinning")
    def test_workspace_read_rejects_pinned_ancestor_path_mismatch(self) -> None:
        target = self.project_root / "docs" / "nested" / "race.md"
        target.parent.mkdir(parents=True)
        target.write_text("stable\n", encoding="utf-8")
        real_info = workspace_tools._pinned_windows_directory_info
        calls = 0

        def mismatched_info(handle: int):
            nonlocal calls
            opened_path, attributes = real_info(handle)
            calls += 1
            if calls == 2:
                return workspace_tools._normalized_windows_path(
                    self.artifacts_root / "outside"
                ), attributes
            return opened_path, attributes

        with patch.object(
            workspace_tools,
            "_pinned_windows_directory_info",
            side_effect=mismatched_info,
        ):
            result = self.execute("read", {"path": "docs/nested/race.md"})

        self.assertEqual(result["code"], "workspace_path_identity_changed")
        self.assertFalse(result["executed"])

    def test_workspace_apply_fails_closed_when_ancestor_pin_cannot_be_held(self) -> None:
        (self.project_root / "docs").mkdir()
        with patch.object(
            workspace_tools,
            "_pinned_workspace_ancestors",
            side_effect=workspace_tools._WorkspaceTaskError(
                "workspace_path_identity_changed"
            ),
        ):
            result = workspace_tools._apply_staged_workspace_edit(
                project_root=self.project_root,
                stage={"path": "docs/new.py"},
                approval_id="approval-1",
                run_command=self.run_command,
                external_tracked_paths=frozenset(),
            )

        self.assertEqual(result["code"], "workspace_path_identity_changed")
        self.assertFalse(result["executed"])

    def test_workspace_read_chunk_args_and_utf8_offsets_are_exact(self) -> None:
        target = self.project_root / "docs" / "utf8.md"
        target.parent.mkdir()
        target.write_text("가나다라마바사\n" * 100, encoding="utf-8")
        sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
        valid = {
            "path": "docs/utf8.md",
            "offset": 3,
            "length": 512,
            "expectedSha256": sha256,
        }

        self.assertEqual(self.execute("read", valid)["code"], "workspace_read_completed")
        for args, code in (
            ({**valid, "offset": 1}, "workspace_read_offset_invalid"),
            ({**valid, "offset": -1}, "workspace_args_invalid"),
            ({**valid, "offset": True}, "workspace_args_invalid"),
            ({**valid, "length": 0}, "workspace_args_invalid"),
            ({**valid, "length": True}, "workspace_args_invalid"),
            ({**valid, "length": 10_000}, "workspace_args_invalid"),
            ({**valid, "expectedSha256": sha256.upper()}, "workspace_args_invalid"),
            ({"path": "docs/utf8.md", "offset": 3}, "workspace_args_invalid"),
            ({**valid, "extra": "field"}, "workspace_args_invalid"),
        ):
            with self.subTest(args=args):
                result = self.execute("read", args)
                self.assertEqual(result["code"], code)

    def test_workspace_read_continuation_args_are_hmac_bound_on_host_queue(self) -> None:
        target = self.project_root / "docs" / "long.md"
        target.parent.mkdir()
        target.write_text("queue-bound\n" * 200, encoding="utf-8")
        sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
        args = {
            "path": "docs/long.md",
            "offset": 0,
            "length": 512,
            "expectedSha256": sha256,
        }
        request = self.request(tool="read", args=args)

        response = self.handle(request)
        self.assertEqual(response["result"]["code"], "workspace_read_completed")
        self.assertEqual(response["result"]["evidence"]["sha256"], sha256)

        tampered = {**request, "args": {**args, "offset": 1}}
        rejected = self.handle(tampered)
        self.assertEqual(rejected["result"]["code"], "workspace_request_args_mismatch")

    def test_search_marks_incomplete_when_eligible_files_are_not_searched(self) -> None:
        docs = self.project_root / "docs"
        docs.mkdir()
        (docs / "large.md").write_text(
            "needle\n" + ("x" * (workspace_tools._MAX_SEARCH_FILE_BYTES + 1)),
            encoding="utf-8",
        )
        (docs / "non-utf8.md").write_bytes(b"needle\xff")
        unreadable = docs / "unreadable.md"
        unreadable.write_text("needle\n", encoding="utf-8")
        real_read = workspace_tools._read_text_file

        def fail_one_read(path: Path, *, project_root: Path | None = None):
            if path == unreadable:
                raise OSError("unreadable")
            return real_read(path, project_root=project_root)

        with patch.object(
            workspace_tools,
            "_read_text_file",
            side_effect=fail_one_read,
        ):
            result = self.execute("search", {"path": "docs", "query": "needle"})

        self.assertEqual(result["code"], "workspace_search_completed")
        self.assertEqual(result["evidence"]["matches"], [])
        self.assertTrue(result["evidence"]["truncated"])

    def test_search_marks_incomplete_when_traversal_or_file_cap_skips_files(self) -> None:
        docs = self.project_root / "docs"
        for relative in ("a/first.md", "b/second.md"):
            target = docs / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("needle\n", encoding="utf-8")

        with patch.object(workspace_tools, "_MAX_SEARCH_DIRECTORIES", 1):
            traversal_limited = self.execute(
                "search",
                {"path": "docs", "query": "needle"},
            )
        with (
            patch.object(workspace_tools, "_MAX_SEARCH_FILES", 1),
            patch.object(workspace_tools, "_MAX_SEARCH_DIRECTORIES", 8),
        ):
            file_limited = self.execute(
                "search",
                {"path": "docs", "query": "needle"},
            )

        self.assertTrue(traversal_limited["evidence"]["truncated"])
        self.assertTrue(file_limited["evidence"]["truncated"])

    def test_escape_sensitive_and_symlink_paths_are_rejected(self) -> None:
        outside = Path(self.temp_dir.name) / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        (self.project_root / ".env").write_text("SECRET=private", encoding="utf-8")

        for path, code in (
            ("../outside.txt", "workspace_path_outside_root"),
            (".env", "workspace_sensitive_path_denied"),
            ("runtime_artifacts/state.json", "workspace_sensitive_path_denied"),
            ("bot_memory/state.json", "workspace_sensitive_path_denied"),
            ("guild_settings/state.json", "workspace_sensitive_path_denied"),
            ("bot_profiles/state.json", "workspace_sensitive_path_denied"),
            ("logs/runtime.log", "workspace_sensitive_path_denied"),
            ("docs/99_PROJECT_INBOX.md", "workspace_sensitive_path_denied"),
            ("untracked-private/state.json", "workspace_path_not_allowed"),
            ("module.py:private", "workspace_path_invalid"),
            ("CON.txt", "workspace_path_invalid"),
        ):
            with self.subTest(path=path):
                result = self.execute("read", {"path": path})
                self.assertFalse(result["executed"])
                self.assertEqual(result["code"], code)

        link = self.project_root / "docs" / "link.txt"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            return
        result = self.execute("read", {"path": "docs/link.txt"})
        self.assertFalse(result["executed"])
        self.assertEqual(result["code"], "workspace_symlink_denied")

    def test_microsoft_auth_cache_paths_are_never_read_or_discovered(self) -> None:
        cache_paths = (
            "external/mindcraft/keys.json",
            "external/mindcraft/_tmp_ms_profiles/account_live-cache.json",
            "external/mindcraft_evelyn/tmp-ms-profile-123/account_xbl-cache.json",
        )
        for relative in cache_paths:
            target = self.project_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("PRIVATE_AUTH_CANARY", encoding="utf-8")

        for relative in cache_paths:
            with self.subTest(relative=relative):
                result = self.execute("read", {"path": relative})
                self.assertFalse(result["executed"])
                self.assertEqual(result["code"], "workspace_sensitive_path_denied")

        listed = self.execute(
            "list",
            {"path": "external", "recursive": True},
        )
        searched = self.execute(
            "search",
            {"path": "external", "query": "PRIVATE_AUTH_CANARY"},
        )
        self.assertNotIn("tmp-ms-profile", json.dumps(listed["evidence"]))
        self.assertNotIn("_tmp_ms_profiles", json.dumps(listed["evidence"]))
        self.assertNotIn("keys.json", json.dumps(listed["evidence"]))
        self.assertEqual(searched["evidence"].get("matches", []), [])

    def test_mindcraft_runtime_private_paths_are_never_read_or_discovered(self) -> None:
        private_paths = (
            "external/mindcraft/bots/evelyn/memory.json",
            "external/mindcraft/code_records/session.json",
            "external/mindcraft/experiments/run.json",
            "external/mindcraft/node_modules.bak_before_link/pkg/index.js",
            "external/mindcraft/results/private.json",
            "external/mindcraft/server_data/world.json",
            "external/mindcraft/services/viaproxy/saves.json",
            "external/mindcraft/wandb/run.json",
            "external/mindcraft/andy_private.json",
            "external/mindcraft/scratch.js",
        )
        for relative in private_paths:
            target = self.project_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("PRIVATE_RUNTIME_CANARY", encoding="utf-8")
            result = self.execute("read", {"path": relative})
            self.assertFalse(result["executed"])
            self.assertEqual(result["code"], "workspace_sensitive_path_denied")

        listed = self.execute(
            "list",
            {"path": "external/mindcraft", "recursive": True},
        )
        searched = self.execute(
            "search",
            {"path": "external/mindcraft", "query": "PRIVATE_RUNTIME_CANARY"},
        )
        rendered = json.dumps(listed["evidence"])
        self.assertNotIn("PRIVATE_RUNTIME_CANARY", rendered)
        self.assertEqual(searched["evidence"].get("matches", []), [])

    def test_external_reads_only_admit_git_tracked_files_and_ancestor_dirs(self) -> None:
        self.init_git()
        nested = self.project_root / "external" / "mindcraft"
        nested.mkdir(parents=True)
        self.assertEqual(
            subprocess.run(
                ["git", "init", "-q"], cwd=nested, check=False
            ).returncode,
            0,
        )
        tracked_nested = nested / "README.md"
        untracked_nested = nested / "temp_repro_manual.mjs"
        tracked_nested.write_text("TRACKED_EXTERNAL_CANARY\n", encoding="utf-8")
        untracked_nested.write_text("UNTRACKED_EXTERNAL_CANARY\n", encoding="utf-8")
        self.assertEqual(
            subprocess.run(
                ["git", "add", "README.md"], cwd=nested, check=False
            ).returncode,
            0,
        )
        sibling = self.project_root / "external" / "mindcraft_evelyn"
        sibling.mkdir()
        tracked_sibling = sibling / "tracked.mjs"
        untracked_sibling = sibling / "evelyn-ms-code.mjs"
        tracked_sibling.write_text("TRACKED_SIBLING_CANARY\n", encoding="utf-8")
        untracked_sibling.write_text("UNTRACKED_SIBLING_CANARY\n", encoding="utf-8")
        self.assertEqual(self.git("add", "external/mindcraft_evelyn/tracked.mjs").returncode, 0)

        def execute(tool: str, args: dict) -> dict:
            return execute_workspace_task_tool(
                project_root=self.project_root,
                tool=tool,
                args=args,
                run_command=subprocess.run,
            )

        self.assertEqual(
            execute("read", {"path": "external/mindcraft/README.md"})["code"],
            "workspace_read_completed",
        )
        self.assertEqual(
            execute("read", {"path": "external/mindcraft_evelyn/tracked.mjs"})["code"],
            "workspace_read_completed",
        )
        for denied_path in (
            "external/mindcraft/temp_repro_manual.mjs",
            "external/mindcraft_evelyn/evelyn-ms-code.mjs",
        ):
            with self.subTest(path=denied_path):
                denied = execute("read", {"path": denied_path})
                self.assertEqual(denied["code"], "workspace_external_untracked_denied")
                diff = execute("diff", {"paths": [denied_path]})
                self.assertEqual(diff["code"], "workspace_external_untracked_denied")
        listed = execute("list", {"path": "external", "recursive": True})
        rendered = json.dumps(listed["evidence"])
        self.assertIn("external/mindcraft/README.md", rendered)
        self.assertIn("external/mindcraft_evelyn/tracked.mjs", rendered)
        self.assertNotIn("temp_repro_manual", rendered)
        self.assertNotIn("evelyn-ms-code", rendered)
        searched = execute(
            "search",
            {"path": "external", "query": "UNTRACKED_"},
        )
        self.assertEqual(searched["evidence"]["matches"], [])

    def test_candidate_tree_manifest_excludes_all_untracked_and_sensitive_files(self) -> None:
        self.init_git()
        tracked = self.project_root / "docs" / "tracked.md"
        untracked = self.project_root / "docs" / "untracked-secret.md"
        inbox = self.project_root / "docs" / "99_PROJECT_INBOX.md"
        tracked.parent.mkdir()
        tracked.write_text("TRACKED\n", encoding="utf-8")
        untracked.write_text("UNTRACKED-SECRET\n", encoding="utf-8")
        inbox.write_text("PRIVATE-INBOX\n", encoding="utf-8")
        self.assertEqual(self.git("add", "docs/tracked.md", "docs/99_PROJECT_INBOX.md").returncode, 0)

        manifest = build_workspace_tracked_manifest(self.project_root)
        before = workspace_tree_digest(
            self.project_root,
            external_tracked_paths=manifest,
        )
        untracked.write_text("CHANGED-UNTRACKED-SECRET\n", encoding="utf-8")
        after_untracked = workspace_tree_digest(
            self.project_root,
            external_tracked_paths=manifest,
        )
        tracked.write_text("CHANGED-TRACKED\n", encoding="utf-8")
        after_tracked = workspace_tree_digest(
            self.project_root,
            external_tracked_paths=manifest,
        )

        self.assertIn("docs", manifest)
        self.assertIn("docs/tracked.md", manifest)
        self.assertNotIn("docs/untracked-secret.md", manifest)
        self.assertNotIn("docs/99_PROJECT_INBOX.md", manifest)
        self.assertEqual(before, after_untracked)
        self.assertNotEqual(before, after_tracked)

    def test_hardlinks_are_denied_for_read_discovery_diff_and_edit(self) -> None:
        outside = Path(self.temp_dir.name) / "outside-private.txt"
        outside.write_text("OUTSIDE_PRIVATE_CANARY\n", encoding="utf-8")
        linked = self.project_root / "docs" / "linked.md"
        linked.parent.mkdir()
        try:
            os.link(outside, linked)
        except OSError as exc:
            self.skipTest(f"hardlinks unavailable: {exc}")

        read = self.execute("read", {"path": "docs/linked.md"})
        diff = self.execute("diff", {"paths": ["docs/linked.md"]})
        listed = self.execute("list", {"path": "docs", "recursive": True})
        searched = self.execute(
            "search",
            {"path": "docs", "query": "OUTSIDE_PRIVATE_CANARY"},
        )
        staged = self.stage(
            {
                "mode": "replace",
                "path": "docs/linked.md",
                "oldText": "OUTSIDE_PRIVATE_CANARY",
                "newText": "changed",
                "expectedSha256": hashlib.sha256(linked.read_bytes()).hexdigest(),
            }
        )

        self.assertEqual(read["code"], "workspace_hardlink_denied")
        self.assertEqual(diff["code"], "workspace_hardlink_denied")
        self.assertNotIn("linked.md", json.dumps(listed["evidence"]))
        self.assertEqual(searched["evidence"]["matches"], [])
        self.assertEqual(staged["code"], "workspace_hardlink_denied")
        self.assertEqual(outside.read_text(encoding="utf-8"), "OUTSIDE_PRIVATE_CANARY\n")

    def test_late_hardlink_blocks_an_already_staged_apply(self) -> None:
        self.init_git()
        target = self.project_root / "evelyn_core" / "module.py"
        target.parent.mkdir()
        target.write_text("before\n", encoding="utf-8")
        self.assertEqual(self.git("add", ".").returncode, 0)
        self.assertEqual(self.git("commit", "-qm", "base").returncode, 0)
        staged = self.stage(
            {
                "mode": "replace",
                "path": "evelyn_core/module.py",
                "oldText": "before",
                "newText": "after",
                "expectedSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )
        alias = Path(self.temp_dir.name) / "late-private-alias.py"
        try:
            os.link(target, alias)
        except OSError as exc:
            self.skipTest(f"hardlinks unavailable: {exc}")

        applied = self.mutate(self.mutation_request(staged["evidence"]))

        self.assertEqual(applied["result"]["code"], "workspace_hardlink_denied")
        self.assertEqual(target.read_text(encoding="utf-8"), "before\n")
        self.assertEqual(alias.read_text(encoding="utf-8"), "before\n")

    def test_python311_reparse_directory_is_never_followed_recursively(self) -> None:
        reparse = self.project_root / "evelyn_core" / "linked"
        reparse.mkdir(parents=True)
        (reparse / "private.py").write_text("junction-needle", encoding="utf-8")
        real_lstat = os.lstat

        def marked_lstat(path):
            found = real_lstat(path)
            if Path(path) == reparse:
                return SimpleNamespace(
                    st_mode=found.st_mode,
                    st_file_attributes=getattr(
                        stat,
                        "FILE_ATTRIBUTE_REPARSE_POINT",
                        0x400,
                    ),
                )
            return found

        with patch.object(workspace_tools.os, "lstat", side_effect=marked_lstat):
            listed = self.execute("list", {"path": ".", "recursive": True})
            searched = self.execute(
                "search",
                {"path": ".", "query": "junction-needle"},
            )

        rendered = json.dumps(listed["evidence"])
        self.assertNotIn("evelyn_core/linked", rendered)
        self.assertEqual(searched["evidence"]["matches"], [])

    def test_direct_edit_is_never_an_effect_boundary(self) -> None:
        target = self.project_root / "evelyn_core" / "module.py"
        target.parent.mkdir()
        target.write_text("before\n", encoding="utf-8")
        result = self.execute(
            "edit",
            {
                "mode": "replace",
                "path": "evelyn_core/module.py",
                "oldText": "before",
                "newText": "after",
                "expectedSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            },
        )
        self.assertEqual(result["code"], "workspace_host_authorization_required")
        self.assertEqual(target.read_text(encoding="utf-8"), "before\n")

    def test_direct_create_delete_and_arbitrary_fields_are_rejected(self) -> None:
        (self.project_root / "evelyn_core").mkdir()
        created = self.execute(
            "edit",
            {
                "mode": "create",
                "path": "evelyn_core/new.py",
                "newText": "value = 1\n",
            },
        )
        repeated = self.execute(
            "edit",
            {
                "mode": "create",
                "path": "evelyn_core/new.py",
                "newText": "value = 2\n",
            },
        )
        delete = self.execute("delete", {"path": "evelyn_core/new.py"})
        arbitrary = self.execute(
            "edit",
            {
                "mode": "create",
                "path": "evelyn_core/other.py",
                "newText": "x = 1\n",
                "command": "powershell.exe",
            },
        )

        self.assertEqual(created["code"], "workspace_host_authorization_required")
        self.assertEqual(repeated["code"], "workspace_host_authorization_required")
        self.assertEqual(delete["code"], "workspace_tool_not_allowed")
        self.assertEqual(arbitrary["code"], "workspace_args_invalid")
        self.assertFalse((self.project_root / "evelyn_core" / "new.py").exists())
        self.assertFalse((self.project_root / "evelyn_core" / "other.py").exists())

    def test_active_evaluator_and_authority_files_are_readable_but_not_editable(self) -> None:
        protected = (
            "evelyn_core/runtime/evelyn_core/task_loop_runtime.py",
            "evelyn_core/runtime/evelyn_core/workspace_task_tools.py",
            "evelyn_core/runtime/evelyn_core/host_supervisor.py",
            "evelyn_core/runtime/evelyn_core/autonomy_authorization.py",
            "evelyn_core/runtime/evelyn_core/autonomy_outcome_evidence.py",
            "tests/core/test_task_loop_runtime.py",
            "AGENTS.md",
            "evelyn_core/runtime/evelyn_core/active_evaluator.py",
            "evelyn_core/runtime/evelyn_core/permission_rules.json",
        )
        for name in protected:
            with self.subTest(name=name):
                target = self.project_root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("policy = 'fixed'\n", encoding="utf-8")
                read = self.execute("read", {"path": name})
                edited = self.execute(
                    "edit",
                    {
                        "mode": "replace",
                        "path": name,
                        "oldText": "fixed",
                        "newText": "weakened",
                        "expectedSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    },
                )
                self.assertTrue(read["verified"])
                self.assertEqual(edited["code"], "workspace_host_authorization_required")
                self.assertEqual(target.read_text(encoding="utf-8"), "policy = 'fixed'\n")

        created = self.execute(
            "edit",
            {
                "mode": "create",
                "path": "evelyn_core/runtime/evelyn_core/approval_policy.py",
                "newText": "ALLOW_ALL = True\n",
            },
        )
        self.assertEqual(created["code"], "workspace_host_authorization_required")
        self.assertFalse(
            (
                self.project_root
                / "evelyn_core"
                / "runtime"
                / "evelyn_core"
                / "approval_policy.py"
            ).exists()
        )

        inbox = self.project_root / "docs" / "99_PROJECT_INBOX.md"
        inbox.parent.mkdir(exist_ok=True)
        inbox.write_text("user-owned", encoding="utf-8")
        for tool, args in (
            ("read", {"path": "docs/99_PROJECT_INBOX.md"}),
            (
                "edit",
                {
                    "mode": "replace",
                    "path": "docs/99_PROJECT_INBOX.md",
                    "oldText": "user-owned",
                    "newText": "changed",
                    "expectedSha256": hashlib.sha256(inbox.read_bytes()).hexdigest(),
                },
            ),
        ):
            with self.subTest(tool=tool):
                result = self.execute(tool, args)
                self.assertEqual(
                    result["code"],
                    "workspace_sensitive_path_denied"
                    if tool == "read"
                    else "workspace_host_authorization_required",
                )

    def test_workspace_test_is_hard_blocked_without_isolated_sandbox(self) -> None:
        test_path = self.project_root / "tests" / "test_sample.py"
        test_path.parent.mkdir()
        test_path.write_text("import unittest\n", encoding="utf-8")
        result = self.execute(
            "test",
            {"runner": "python_unittest", "targets": ["tests/test_sample.py"]},
        )

        self.assertEqual(result["code"], "workspace_test_sandbox_required")
        self.assertFalse(result["executed"])
        self.assertEqual(self.commands, [])

    def test_workspace_pytest_is_also_blocked_before_process_creation(self) -> None:
        test_path = self.project_root / "tests" / "test_sample.py"
        test_path.parent.mkdir()
        test_path.write_text("def test_sample():\n    assert True\n", encoding="utf-8")

        result = self.execute(
            "test",
            {"runner": "python_pytest", "targets": ["tests/test_sample.py"]},
        )

        self.assertEqual(result["code"], "workspace_test_sandbox_required")
        self.assertEqual(self.commands, [])

    def test_workspace_test_timeout_hook_is_never_reached(self) -> None:
        test_path = self.project_root / "tests" / "test_sample.py"
        test_path.parent.mkdir()
        test_path.write_text("import unittest\n", encoding="utf-8")

        def timed_out(command, **kwargs):
            self.commands.append((list(command), dict(kwargs)))
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        result = execute_workspace_task_tool(
            project_root=self.project_root,
            tool="test",
            args={"runner": "python_unittest", "targets": ["tests/test_sample.py"]},
            run_command=timed_out,
        )

        self.assertTrue(result["attempted"])
        self.assertFalse(result["executed"])
        self.assertTrue(result["verified"])
        self.assertEqual(result["code"], "workspace_test_sandbox_required")
        self.assertEqual(self.commands, [])

    def test_diff_uses_fixed_git_argv_and_never_accepts_options_as_paths(self) -> None:
        target = self.project_root / "evelyn_core" / "module.py"
        target.parent.mkdir()
        target.write_text("value = 1\n", encoding="utf-8")
        result = self.execute("diff", {"paths": ["evelyn_core/module.py"]})

        command, kwargs = self.commands[0]
        self.assertEqual(
            command,
            [
                "git",
                "-c",
                "core.fsmonitor=false",
                "diff",
                "HEAD",
                "--no-ext-diff",
                "--no-textconv",
                "--binary",
                "--unified=3",
                "--",
                ":(literal)evelyn_core/module.py",
            ],
        )
        self.assertFalse(kwargs["shell"])
        self.assertTrue(result["verified"])
        self.assertTrue(result["evidence"]["truncated"])
        self.assertEqual(
            self.commands[1][0],
            [
                "git",
                "-c",
                "core.fsmonitor=false",
                "ls-files",
                "-z",
                "--",
                ":(literal)evelyn_core/module.py",
            ],
        )

        rejected = self.execute("diff", {"paths": ["--output=private.txt"]})
        broad = self.execute("diff", {"paths": ["."]})
        self.assertEqual(rejected["code"], "workspace_path_invalid")
        self.assertEqual(broad["code"], "workspace_diff_file_required")
        self.assertEqual(len(self.commands), 2)

    def test_diff_includes_staged_and_unstaged_changes_from_head(self) -> None:
        self.init_git()
        target = self.project_root / "docs" / "tracked.md"
        target.parent.mkdir()
        target.write_text("before-one\nbefore-two\n", encoding="utf-8")
        self.assertEqual(self.git("add", "docs/tracked.md").returncode, 0)
        self.assertEqual(self.git("commit", "-qm", "base").returncode, 0)
        target.write_text("staged-one\nbefore-two\n", encoding="utf-8")
        self.assertEqual(self.git("add", "docs/tracked.md").returncode, 0)
        target.write_text("staged-one\nworktree-two\n", encoding="utf-8")

        result = execute_workspace_task_tool(
            project_root=self.project_root,
            tool="diff",
            args={"paths": ["docs/tracked.md"]},
            run_command=subprocess.run,
            external_tracked_paths=frozenset(),
        )

        self.assertEqual(result["code"], "workspace_diff_completed")
        self.assertIn("-before-one", result["evidence"]["diff"])
        self.assertIn("+staged-one", result["evidence"]["diff"])
        self.assertIn("-before-two", result["evidence"]["diff"])
        self.assertIn("+worktree-two", result["evidence"]["diff"])
        self.assertFalse(result["evidence"]["truncated"])

    def test_diff_marks_untracked_requested_file_incomplete(self) -> None:
        self.init_git()
        tracked = self.project_root / "docs" / "tracked.md"
        tracked.parent.mkdir()
        tracked.write_text("base\n", encoding="utf-8")
        self.assertEqual(self.git("add", "docs/tracked.md").returncode, 0)
        self.assertEqual(self.git("commit", "-qm", "base").returncode, 0)
        untracked = self.project_root / "docs" / "untracked.md"
        untracked.write_text("untracked\n", encoding="utf-8")

        result = execute_workspace_task_tool(
            project_root=self.project_root,
            tool="diff",
            args={"paths": ["docs/untracked.md"]},
            run_command=subprocess.run,
            external_tracked_paths=frozenset(),
        )

        self.assertEqual(result["code"], "workspace_diff_completed")
        self.assertEqual(result["evidence"]["diff"], "")
        self.assertTrue(result["evidence"]["truncated"])

    def test_expired_and_unexpected_requests_never_execute(self) -> None:
        expired = self.handle(
            self.request(tool="list", args={"path": ".", "recursive": False}),
            at=1011.0,
        )
        unexpected = self.handle(
            self.request(
                tool="list",
                args={"path": ".", "recursive": False},
                command="whoami",
            )
        )
        oversized_binding = self.handle(
            self.request(
                tool="list",
                args={"path": ".", "recursive": False},
                taskId="private" * 100_000,
            )
        )

        self.assertEqual(expired["schema"], WORKSPACE_TASK_RESPONSE_SCHEMA)
        self.assertEqual(expired["result"]["code"], "workspace_request_expired")
        self.assertFalse(expired["result"]["executed"])
        self.assertEqual(unexpected["result"]["code"], "workspace_request_invalid")
        self.assertLessEqual(
            len(json.dumps(oversized_binding, ensure_ascii=False).encode("utf-8")),
            WORKSPACE_TASK_MAX_RESPONSE_BYTES,
        )
        self.assertNotIn("privateprivate", json.dumps(oversized_binding))
        self.assertEqual(self.commands, [])

    def test_request_auth_args_restart_and_replay_are_fail_closed(self) -> None:
        base = self.request(
            tool="list",
            args={"path": ".", "recursive": False},
        )
        self.assertNotIn(AUTH_TOKEN, json.dumps(base))
        forged = {**base, "authTag": "0" * 64}
        wrong_args = {**base, "args": {"path": "docs", "recursive": False}}
        pre_restart = self.request(
            tool="list",
            args={"path": ".", "recursive": False},
            issuedAt=998.0,
        )
        old_instance = self.request(
            tool="list",
            args={"path": ".", "recursive": False},
            hostInstanceId="host-previous-instance",
        )
        consumed: dict[str, float] = {}
        first = self.handle(base, consumed=consumed)
        replay = self.handle(base, consumed=consumed)

        self.assertEqual(self.handle(forged)["result"]["code"], "workspace_request_auth_invalid")
        self.assertEqual(self.handle(wrong_args)["result"]["code"], "workspace_request_args_mismatch")
        self.assertEqual(self.handle(pre_restart)["result"]["code"], "workspace_request_pre_restart")
        self.assertEqual(self.handle(old_instance)["result"]["code"], "workspace_request_invalid")
        self.assertEqual(first["result"]["outcome"], "succeeded")
        self.assertEqual(replay["result"]["code"], "workspace_request_replayed")

    def test_host_supervisor_dispatches_workspace_schema_and_preserves_repair_schema(self) -> None:
        supervisor = HostSupervisor(
            project_root=self.project_root,
            artifacts_root=self.artifacts_root,
            run_command=self.run_command,
            now=lambda: 1001.0,
            retention_reporter=FakeRetentionReporter(),
            process_owner=FakeProcessOwner(),
            bridge_lock_probe=lambda: True,
            voice_capture_auth_token="workspace-task-test-token-0123456789",
            workspace_task_auth_token=AUTH_TOKEN,
        )
        self.addCleanup(supervisor._workspace_query_worker.close)
        self.addCleanup(supervisor._workspace_test_worker.close)
        request_path = supervisor.requests_dir / "request-1.json"
        atomic_json_write(
            request_path,
            self.request(
                tool="list",
                args={"path": ".", "recursive": False},
                hostInstanceId=supervisor.host_instance_id,
                issuedAt=1001.0,
            ),
        )

        supervisor.process_request_queue()

        response_path = supervisor.responses_dir / "request-1.json"
        deadline = time.monotonic() + 1.0
        while not response_path.exists() and time.monotonic() < deadline:
            supervisor.process_request_queue()
            time.sleep(0.001)
        response = json.loads(
            response_path.read_text(encoding="utf-8")
        )
        self.assertEqual(response["schema"], WORKSPACE_TASK_RESPONSE_SCHEMA)
        self.assertEqual(set(response["result"]), RESULT_KEYS)
        repair = supervisor.handle_request(
            {
                "schema": SUPERVISOR_REQUEST_SCHEMA,
                "requestId": "repair-1",
                "operation": "preview",
                "actionId": "start_tts",
                "previewToken": "",
                "requestedAt": 1001.0,
            }
        )
        self.assertNotEqual(repair["schema"], WORKSPACE_TASK_RESPONSE_SCHEMA)
        self.assertTrue(repair["ok"])

    def test_host_supervisor_removes_expired_request_and_response_orphans(self) -> None:
        supervisor = HostSupervisor(
            project_root=self.project_root,
            artifacts_root=self.artifacts_root,
            now=lambda: 2000.0,
            retention_reporter=FakeRetentionReporter(),
            process_owner=FakeProcessOwner(),
            bridge_lock_probe=lambda: True,
            workspace_task_auth_token=AUTH_TOKEN,
        )
        self.addCleanup(supervisor._workspace_query_worker.close)
        self.addCleanup(supervisor._workspace_test_worker.close)
        request = self.request(
            tool="list",
            args={"path": ".", "recursive": False},
            hostInstanceId=supervisor.host_instance_id,
            issuedAt=1900.0,
            expiresAt=1930.0,
        )
        response = sign_payload(
            {
                "schema": WORKSPACE_TASK_RESPONSE_SCHEMA,
                **{
                    key: request[key]
                    for key in (
                        "hostInstanceId",
                        "requestId",
                        "taskId",
                        "grantId",
                        "actionRunId",
                        "stepId",
                        "surface",
                        "tool",
                        "argsHash",
                        "issuedAt",
                        "expiresAt",
                    )
                },
                "respondedAt": 1901.0,
                "result": {
                    "attempted": False,
                    "executed": False,
                    "observed": True,
                    "verified": True,
                    "outcome": "blocked",
                    "code": "old",
                    "summary": "old",
                    "evidence": {},
                },
            },
            domain=WORKSPACE_TASK_RESPONSE_AUTH_DOMAIN,
        )
        atomic_json_write(supervisor.requests_dir / "request-1.json", request)
        atomic_json_write(supervisor.responses_dir / "request-1.json", response)
        atomic_json_write(
            supervisor.mutation_requests_dir / "mutation-expired.json",
            {
                "schema": WORKSPACE_MUTATION_REQUEST_SCHEMA,
                "hostInstanceId": supervisor.host_instance_id,
                "expiresAt": 1930.0,
            },
        )
        atomic_json_write(
            supervisor.mutation_responses_dir / "mutation-old-boot.json",
            {
                "schema": WORKSPACE_MUTATION_RESPONSE_SCHEMA,
                "hostInstanceId": "host-previous-boot",
                "expiresAt": 2030.0,
            },
        )

        supervisor.process_request_queue()

        self.assertEqual(list(supervisor.requests_dir.glob("*.json")), [])
        self.assertEqual(list(supervisor.responses_dir.glob("*.json")), [])
        self.assertEqual(list(supervisor.mutation_requests_dir.glob("*.json")), [])
        self.assertEqual(list(supervisor.mutation_responses_dir.glob("*.json")), [])

    def test_client_reuses_supervisor_queue_without_retry(self) -> None:
        supervisor = HostSupervisor(
            project_root=self.project_root,
            artifacts_root=self.artifacts_root,
            run_command=self.run_command,
            retention_reporter=FakeRetentionReporter(),
            process_owner=FakeProcessOwner(),
            bridge_lock_probe=lambda: True,
            voice_capture_auth_token="workspace-task-test-token-0123456789",
            workspace_task_auth_token=AUTH_TOKEN,
        )
        self.addCleanup(supervisor._workspace_query_worker.close)
        self.addCleanup(supervisor._workspace_test_worker.close)
        atomic_json_write(
            supervisor.status_path,
            {
                "schema": SUPERVISOR_STATUS_SCHEMA,
                "hostInstanceId": supervisor.host_instance_id,
                "workspaceTaskAuthReady": True,
                "heartbeatAt": time.time(),
            },
        )
        client = WorkspaceTaskHostClient(
            root=self.artifacts_root,
            timeout_sec=1.0,
            auth_token=AUTH_TOKEN,
        )
        self.assertTrue(client.available())

        stop = threading.Event()

        def serve_once() -> None:
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline and not stop.is_set():
                if (
                    list(supervisor.requests_dir.glob("*.json"))
                    or supervisor._workspace_query_pending is not None
                ):
                    supervisor.process_request_queue()
                if list(supervisor.responses_dir.glob("*.json")):
                    return
                time.sleep(0.005)

        thread = threading.Thread(target=serve_once)
        thread.start()
        try:
            result = client.execute(
                "task-1",
                1,
                "list",
                {"path": ".", "recursive": False},
                grant_id="grant-1",
                action_run_id="action-1",
                surface="control_page",
            )
        finally:
            stop.set()
            thread.join(timeout=1.0)

        self.assertEqual(set(result), RESULT_KEYS)
        self.assertEqual(result["outcome"], "succeeded")
        self.assertEqual(list(supervisor.requests_dir.glob("*.json")), [])

    def test_client_is_unavailable_without_token_or_boot_instance(self) -> None:
        status_path = self.artifacts_root / "host_supervisor" / "status.json"
        atomic_json_write(
            status_path,
            {
                "schema": SUPERVISOR_STATUS_SCHEMA,
                "hostInstanceId": HOST_INSTANCE_ID,
                "workspaceTaskAuthReady": True,
                "heartbeatAt": time.time(),
            },
        )
        self.assertFalse(
            WorkspaceTaskHostClient(
                root=self.artifacts_root,
                auth_token="",
            ).available()
        )
        atomic_json_write(
            status_path,
            {"schema": SUPERVISOR_STATUS_SCHEMA, "heartbeatAt": time.time()},
        )
        self.assertFalse(
            WorkspaceTaskHostClient(
                root=self.artifacts_root,
                auth_token=AUTH_TOKEN,
            ).available()
        )

    def test_behavioral_stage_is_not_created_until_sandbox_canary_is_ready(self) -> None:
        status_path = self.artifacts_root / "host_supervisor" / "status.json"
        atomic_json_write(
            status_path,
            {
                "schema": SUPERVISOR_STATUS_SCHEMA,
                "hostInstanceId": HOST_INSTANCE_ID,
                "workspaceTaskAuthReady": True,
                "workspaceSandboxAuthReady": True,
                "workspaceSandboxReady": False,
                "heartbeatAt": time.time(),
            },
        )
        client = WorkspaceTaskHostClient(
            root=self.artifacts_root,
            timeout_sec=0.1,
            auth_token=AUTH_TOKEN,
            sandbox_auth_token=SANDBOX_TOKEN,
        )
        result = client.stage_edit(
            "task-1",
            1,
            {"mode": "create", "path": "evelyn_core/new.py", "newText": "x = 1\n"},
            grant_id="grant-1",
            action_run_id="action-1",
            surface="control_page",
            requires_sandbox_test=True,
        )

        self.assertFalse(client.sandbox_available())
        self.assertEqual(result["code"], "workspace_test_sandbox_unavailable")
        self.assertEqual(
            list((self.artifacts_root / "host_supervisor" / "requests").glob("*.json")),
            [],
        )

    def test_client_rejects_response_with_wrong_task_step_or_tool_binding(self) -> None:
        root = self.artifacts_root / "host_supervisor"
        atomic_json_write(
            root / "status.json",
            {
                "schema": SUPERVISOR_STATUS_SCHEMA,
                "hostInstanceId": HOST_INSTANCE_ID,
                "workspaceTaskAuthReady": True,
                "heartbeatAt": time.time(),
            },
        )
        client = WorkspaceTaskHostClient(
            root=self.artifacts_root,
            timeout_sec=1.0,
            auth_token=AUTH_TOKEN,
        )

        def forge_response() -> None:
            deadline = time.monotonic() + 1.0
            request_path = None
            while time.monotonic() < deadline and request_path is None:
                request_path = next(iter(client.requests_dir.glob("*.json")), None)
                time.sleep(0.005)
            self.assertIsNotNone(request_path)
            request = json.loads(request_path.read_text(encoding="utf-8"))
            response = sign_payload(
                {
                    "schema": WORKSPACE_TASK_RESPONSE_SCHEMA,
                    "hostInstanceId": request["hostInstanceId"],
                    "requestId": request["requestId"],
                    "taskId": "different-task",
                    "grantId": request["grantId"],
                    "actionRunId": request["actionRunId"],
                    "stepId": request["stepId"],
                    "surface": request["surface"],
                    "tool": request["tool"],
                    "argsHash": request["argsHash"],
                    "issuedAt": request["issuedAt"],
                    "expiresAt": request["expiresAt"],
                    "respondedAt": time.time(),
                    "result": {
                        "attempted": True,
                        "executed": True,
                        "observed": True,
                        "verified": True,
                        "outcome": "succeeded",
                        "code": "workspace_read_completed",
                        "summary": "forged",
                        "evidence": {},
                    },
                },
                domain=WORKSPACE_TASK_RESPONSE_AUTH_DOMAIN,
            )
            atomic_json_write(
                client.responses_dir / f"{request['requestId']}.json",
                response,
            )

        thread = threading.Thread(target=forge_response)
        thread.start()
        try:
            result = client.execute(
                "task-1",
                1,
                "read",
                {"path": "README.md"},
                grant_id="grant-1",
                action_run_id="action-1",
                surface="control_page",
            )
        finally:
            thread.join(timeout=1.0)

        self.assertEqual(result["code"], "workspace_response_invalid")

    def test_client_surfaces_old_host_workspace_protocol(self) -> None:
        root = self.artifacts_root / "host_supervisor"
        atomic_json_write(
            root / "status.json",
            {
                "schema": SUPERVISOR_STATUS_SCHEMA,
                "hostInstanceId": HOST_INSTANCE_ID,
                "workspaceTaskAuthReady": True,
                "heartbeatAt": time.time(),
            },
        )
        client = WorkspaceTaskHostClient(
            root=self.artifacts_root,
            timeout_sec=1.0,
            auth_token=AUTH_TOKEN,
        )

        def old_host_response() -> None:
            deadline = time.monotonic() + 1.0
            request_path = None
            while time.monotonic() < deadline and request_path is None:
                request_path = next(iter(client.requests_dir.glob("*.json")), None)
                time.sleep(0.005)
            self.assertIsNotNone(request_path)
            request = json.loads(request_path.read_text(encoding="utf-8"))
            atomic_json_write(
                client.responses_dir / f"{request['requestId']}.json",
                {
                    "schema": "host_supervisor.workspace-task.response.v1",
                },
            )

        thread = threading.Thread(target=old_host_response)
        thread.start()
        try:
            result = client.execute(
                "task-1",
                1,
                "read",
                {"path": "README.md"},
                grant_id="grant-1",
                action_run_id="action-1",
                surface="control_page",
            )
        finally:
            thread.join(timeout=1.0)

        self.assertEqual(result["code"], "workspace_host_protocol_mismatch")

    def test_outer_queue_token_cannot_forge_a_sandbox_success_response(self) -> None:
        root = self.artifacts_root / "host_supervisor"
        atomic_json_write(
            root / "status.json",
            {
                "schema": SUPERVISOR_STATUS_SCHEMA,
                "hostInstanceId": HOST_INSTANCE_ID,
                "workspaceTaskAuthReady": True,
                "workspaceSandboxAuthReady": True,
                "heartbeatAt": time.time(),
            },
        )
        client = WorkspaceTaskHostClient(
            root=self.artifacts_root,
            timeout_sec=1.0,
            auth_token=AUTH_TOKEN,
            sandbox_auth_token=SANDBOX_TOKEN,
        )

        def forge_outer_only() -> None:
            deadline = time.monotonic() + 1.0
            request_path = None
            while time.monotonic() < deadline and request_path is None:
                request_path = next(iter(client.requests_dir.glob("*.json")), None)
                time.sleep(0.005)
            self.assertIsNotNone(request_path)
            request = json.loads(request_path.read_text(encoding="utf-8"))
            result = {
                "attempted": True,
                "executed": True,
                "observed": True,
                "verified": True,
                "outcome": "succeeded",
                "code": "workspace_test_passed",
                "summary": "forged",
                "evidence": {
                    "stageId": request["candidateStageId"],
                    "candidatePath": "evelyn_core/module.py",
                    "candidateSha256": "1" * 64,
                    "baseTreeSha256": "2" * 64,
                    "candidateTreeSha256": "3" * 64,
                    "runner": "python_unittest",
                    "targets": ["tests/test_module.py"],
                },
            }
            response = sign_payload(
                {
                    "schema": WORKSPACE_TASK_RESPONSE_SCHEMA,
                    **{
                        key: request[key]
                        for key in (
                            "hostInstanceId",
                            "requestId",
                            "taskId",
                            "grantId",
                            "actionRunId",
                            "stepId",
                            "surface",
                            "tool",
                            "requiresSandboxTest",
                            "candidateStageId",
                            "argsHash",
                            "issuedAt",
                            "expiresAt",
                        )
                    },
                    "respondedAt": time.time(),
                    "result": result,
                    "sandboxAuthAlgorithm": "",
                    "sandboxAuthTag": "",
                },
                domain=WORKSPACE_TASK_RESPONSE_AUTH_DOMAIN,
            )
            atomic_json_write(
                client.responses_dir / f"{request['requestId']}.json",
                response,
            )

        thread = threading.Thread(target=forge_outer_only)
        thread.start()
        result = client.test_staged_candidate(
            "task-1",
            2,
            {"runner": "python_unittest", "targets": ["tests/test_module.py"]},
            stage_id="stage-1",
            grant_id="grant-1",
            action_run_id="action-1",
            surface="control_page",
        )
        thread.join(timeout=1.0)

        self.assertEqual(result["code"], "workspace_response_invalid")
        self.assertFalse(result["verified"])

    def test_client_rejects_forged_response_auth_tag(self) -> None:
        root = self.artifacts_root / "host_supervisor"
        atomic_json_write(
            root / "status.json",
            {
                "schema": SUPERVISOR_STATUS_SCHEMA,
                "hostInstanceId": HOST_INSTANCE_ID,
                "workspaceTaskAuthReady": True,
                "heartbeatAt": time.time(),
            },
        )
        client = WorkspaceTaskHostClient(
            root=self.artifacts_root,
            timeout_sec=1.0,
            auth_token=AUTH_TOKEN,
        )

        def forge_response() -> None:
            request_path = None
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline and request_path is None:
                request_path = next(iter(client.requests_dir.glob("*.json")), None)
                time.sleep(0.005)
            self.assertIsNotNone(request_path)
            request = json.loads(request_path.read_text(encoding="utf-8"))
            response = sign_payload(
                {
                    "schema": WORKSPACE_TASK_RESPONSE_SCHEMA,
                    **{
                        key: request[key]
                        for key in (
                            "hostInstanceId",
                            "requestId",
                            "taskId",
                            "grantId",
                            "actionRunId",
                            "stepId",
                            "surface",
                            "tool",
                            "argsHash",
                            "issuedAt",
                            "expiresAt",
                        )
                    },
                    "respondedAt": time.time(),
                    "result": {
                        "attempted": True,
                        "executed": True,
                        "observed": True,
                        "verified": True,
                        "outcome": "succeeded",
                        "code": "workspace_read_completed",
                        "summary": "forged",
                        "evidence": {},
                    },
                },
                domain=WORKSPACE_TASK_RESPONSE_AUTH_DOMAIN,
            )
            response["authTag"] = "0" * 64
            atomic_json_write(
                client.responses_dir / f"{request['requestId']}.json",
                response,
            )

        thread = threading.Thread(target=forge_response)
        thread.start()
        try:
            result = client.execute(
                "task-1",
                1,
                "read",
                {"path": "README.md"},
                grant_id="grant-1",
                action_run_id="action-1",
                surface="control_page",
            )
        finally:
            thread.join(timeout=1.0)

        self.assertEqual(result["code"], "workspace_response_invalid")
        self.assertFalse(result["verified"])

    def test_client_rejects_non_json_and_oversized_requests_before_queueing(self) -> None:
        status_path = self.artifacts_root / "host_supervisor" / "status.json"
        atomic_json_write(
            status_path,
            {
                "schema": SUPERVISOR_STATUS_SCHEMA,
                "hostInstanceId": HOST_INSTANCE_ID,
                "workspaceTaskAuthReady": True,
                "heartbeatAt": time.time(),
            },
        )
        client = WorkspaceTaskHostClient(
            root=self.artifacts_root,
            timeout_sec=1.0,
            auth_token=AUTH_TOKEN,
        )

        binding = {
            "grant_id": "grant-1",
            "action_run_id": "action-1",
            "surface": "control_page",
        }
        non_json = client.execute(
            "task-1", 1, "read", {"path": {"not-json"}}, **binding
        )
        oversized = client.execute(
            "task-1",
            2,
            "read",
            {"path": "x" * (WORKSPACE_TASK_MAX_REQUEST_BYTES + 1)},
            **binding,
        )

        self.assertEqual(non_json["code"], "workspace_request_invalid")
        self.assertEqual(oversized["code"], "workspace_request_too_large")
        requests_dir = self.artifacts_root / "host_supervisor" / "requests"
        self.assertEqual(list(requests_dir.glob("*.json")), [])

    def test_staged_replace_does_not_mutate_until_exact_one_shot_apply(self) -> None:
        self.init_git()
        target = self.project_root / "evelyn_core" / "module.py"
        target.parent.mkdir()
        target.write_text("before\n", encoding="utf-8")
        self.assertEqual(self.git("add", ".").returncode, 0)
        self.assertEqual(self.git("commit", "-qm", "base").returncode, 0)
        original_created_at = target.stat().st_ctime_ns
        staged = self.stage(
            {
                "mode": "replace",
                "path": "evelyn_core/module.py",
                "oldText": "before",
                "newText": "after",
                "expectedSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )

        self.assertEqual(staged["code"], "workspace_edit_staged")
        evidence = staged["evidence"]
        self.assertFalse(evidence["diffTruncated"])
        self.assertIn("-before", evidence["fullDiff"])
        self.assertIn("+after", evidence["fullDiff"])
        self.assertEqual(target.read_text(encoding="utf-8"), "before\n")
        consumed: dict[str, float] = {}
        request = self.mutation_request(evidence)
        applied = self.mutate(request, consumed=consumed)
        replay = self.mutate(request, consumed=consumed)

        self.assertEqual(applied["schema"], WORKSPACE_MUTATION_RESPONSE_SCHEMA)
        self.assertEqual(applied["result"]["code"], "workspace_edit_completed")
        self.assertEqual(
            applied["result"]["evidence"]["sha256"],
            evidence["candidateSha256"],
        )
        self.assertEqual(target.read_text(encoding="utf-8"), "after\n")
        if os.name == "nt":
            self.assertEqual(target.stat().st_ctime_ns, original_created_at)
        self.assertEqual(
            [path for path in target.parent.iterdir() if ".evelyn-" in path.name],
            [],
        )
        self.assertEqual(replay["result"]["code"], "workspace_mutation_replayed")
        self.assertNotIn(MUTATION_TOKEN, json.dumps(applied))

    def test_apply_rechecks_signed_grant_expiry_before_mutation(self) -> None:
        self.init_git()
        target = self.project_root / "evelyn_core" / "module.py"
        target.parent.mkdir()
        target.write_text("before\n", encoding="utf-8")
        self.assertEqual(self.git("add", ".").returncode, 0)
        self.assertEqual(self.git("commit", "-qm", "base").returncode, 0)
        staged = self.stage(
            {
                "mode": "replace",
                "path": "evelyn_core/module.py",
                "oldText": "before",
                "newText": "after",
                "expectedSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )

        result = self.mutate(
            self.mutation_request(
                staged["evidence"],
                grant_expires_at=1001.5,
            ),
            at=1002.0,
        )

        self.assertEqual(result["result"]["code"], "task_grant_expired")
        self.assertEqual(target.read_text(encoding="utf-8"), "before\n")
        self.assertIn(staged["evidence"]["stageId"], self.stages)

    def test_replace_rolls_back_instead_of_overwriting_a_concurrent_save(self) -> None:
        self.init_git()
        target = self.project_root / "evelyn_core" / "module.py"
        target.parent.mkdir()
        target.write_text("before\n", encoding="utf-8")
        self.assertEqual(self.git("add", ".").returncode, 0)
        self.assertEqual(self.git("commit", "-qm", "base").returncode, 0)
        staged = self.stage(
            {
                "mode": "replace",
                "path": "evelyn_core/module.py",
                "oldText": "before",
                "newText": "approved",
                "expectedSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )
        real_exchange = workspace_tools._atomic_replace_with_backup
        calls = 0

        def concurrent_save(path, candidate, backup):
            nonlocal calls
            calls += 1
            if calls == 1:
                Path(path).write_text("user-concurrent-save\n", encoding="utf-8")
            return real_exchange(path, candidate, backup)

        with patch.object(
            workspace_tools,
            "_atomic_replace_with_backup",
            side_effect=concurrent_save,
        ):
            result = self.mutate(self.mutation_request(staged["evidence"]))

        self.assertEqual(result["result"]["code"], "workspace_edit_base_changed")
        self.assertEqual(target.read_text(encoding="utf-8"), "user-concurrent-save\n")
        self.assertEqual(calls, 2)
        self.assertEqual(
            [path for path in target.parent.iterdir() if ".evelyn-" in path.name],
            [],
        )

    @unittest.skipUnless(os.name == "nt", "NTFS named streams are Windows-only")
    def test_named_streams_are_rejected_at_stage_and_rechecked_at_apply(self) -> None:
        self.init_git()
        target = self.project_root / "evelyn_core" / "module.py"
        target.parent.mkdir()
        target.write_text("before\n", encoding="utf-8")
        self.assertEqual(self.git("add", ".").returncode, 0)
        self.assertEqual(self.git("commit", "-qm", "base").returncode, 0)
        stream = Path(f"{target}:user-data")
        stream.write_bytes(b"user-owned-stream")
        denied = self.stage(
            {
                "mode": "replace",
                "path": "evelyn_core/module.py",
                "oldText": "before",
                "newText": "approved",
                "expectedSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )
        self.assertEqual(denied["code"], "workspace_nondefault_stream_denied")
        self.assertEqual(stream.read_bytes(), b"user-owned-stream")

        stream.unlink()
        staged = self.stage(
            {
                "mode": "replace",
                "path": "evelyn_core/module.py",
                "oldText": "before",
                "newText": "approved",
                "expectedSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )
        stream.write_bytes(b"late-user-owned-stream")
        applied = self.mutate(self.mutation_request(staged["evidence"]))
        self.assertEqual(
            applied["result"]["code"],
            "workspace_nondefault_stream_denied",
        )
        self.assertEqual(target.read_text(encoding="utf-8"), "before\n")
        self.assertEqual(stream.read_bytes(), b"late-user-owned-stream")

    def test_dirty_stage_requires_extra_ack_and_cancel_never_requires_it(self) -> None:
        self.init_git()
        target = self.project_root / "evelyn_core" / "module.py"
        target.parent.mkdir()
        target.write_text("base\n", encoding="utf-8")
        self.assertEqual(self.git("add", ".").returncode, 0)
        self.assertEqual(self.git("commit", "-qm", "base").returncode, 0)
        target.write_text("dirty\n", encoding="utf-8")
        staged = self.stage(
            {
                "mode": "replace",
                "path": "evelyn_core/module.py",
                "oldText": "dirty",
                "newText": "approved",
                "expectedSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )
        evidence = staged["evidence"]
        self.assertEqual(evidence["dirtyStatus"], "modified")
        self.assertEqual(evidence["gitStatus"], " M evelyn_core/module.py")
        self.assertTrue(evidence["tracked"])
        self.assertTrue(evidence["dirtyBaseAcknowledgementRequired"])
        self.assertIn("-dirty", evidence["fullDiff"])

        denied = self.mutate(self.mutation_request(evidence))
        self.assertEqual(
            denied["result"]["code"],
            "workspace_dirty_base_acknowledgement_required",
        )
        self.assertEqual(target.read_text(encoding="utf-8"), "dirty\n")
        cancelled = self.mutate(
            self.mutation_request(
                evidence,
                operation="cancel",
                request_id="mutation-cancel",
            )
        )
        self.assertEqual(cancelled["result"]["code"], "workspace_edit_stage_cancelled")
        self.assertEqual(self.stages, {})

    def test_post_exchange_failure_keeps_recovery_backup_and_never_succeeds(self) -> None:
        self.init_git()
        target = self.project_root / "evelyn_core" / "module.py"
        target.parent.mkdir()
        target.write_text("before\n", encoding="utf-8")
        base_bytes = target.read_bytes()
        self.assertEqual(self.git("add", ".").returncode, 0)
        self.assertEqual(self.git("commit", "-qm", "base").returncode, 0)
        staged = self.stage(
            {
                "mode": "replace",
                "path": "evelyn_core/module.py",
                "oldText": "before",
                "newText": "approved",
                "expectedSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )
        real_exchange = workspace_tools._atomic_replace_with_backup

        def fail_after_exchange(path, candidate, backup):
            real_exchange(path, candidate, backup)
            raise OSError("simulated post-exchange failure")

        with patch.object(
            workspace_tools,
            "_atomic_replace_with_backup",
            side_effect=fail_after_exchange,
        ):
            result = self.mutate(self.mutation_request(staged["evidence"]))

        self.assertEqual(result["result"]["code"], "workspace_edit_recovery_required")
        backups = [
            path
            for path in target.parent.iterdir()
            if ".evelyn-backup-" in path.name
        ]
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), base_bytes)
        blocked = self.stage(
            {
                "mode": "replace",
                "path": "evelyn_core/module.py",
                "oldText": "approved",
                "newText": "again",
                "expectedSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )
        self.assertEqual(blocked["code"], "workspace_edit_recovery_required")

    def test_backup_cleanup_failure_is_recovery_required_not_false_success(self) -> None:
        self.init_git()
        target = self.project_root / "evelyn_core" / "module.py"
        target.parent.mkdir()
        target.write_text("before\n", encoding="utf-8")
        base_bytes = target.read_bytes()
        self.assertEqual(self.git("add", ".").returncode, 0)
        self.assertEqual(self.git("commit", "-qm", "base").returncode, 0)
        staged = self.stage(
            {
                "mode": "replace",
                "path": "evelyn_core/module.py",
                "oldText": "before",
                "newText": "approved",
                "expectedSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )
        real_remove = workspace_tools._remove_workspace_exchange_path

        def fail_backup_cleanup(path):
            if ".evelyn-backup-" in Path(path).name:
                return False
            return real_remove(path)

        with patch.object(
            workspace_tools,
            "_remove_workspace_exchange_path",
            side_effect=fail_backup_cleanup,
        ):
            result = self.mutate(self.mutation_request(staged["evidence"]))

        self.assertEqual(result["result"]["code"], "workspace_edit_recovery_required")
        self.assertEqual(target.read_text(encoding="utf-8"), "approved\n")
        backups = [
            path
            for path in target.parent.iterdir()
            if ".evelyn-backup-" in path.name
        ]
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), base_bytes)

    def test_one_host_stage_slot_is_released_by_cancel_and_apply(self) -> None:
        self.init_git()
        directory = self.project_root / "evelyn_core"
        directory.mkdir()
        targets = [directory / f"module_{index}.py" for index in range(3)]
        for target in targets:
            target.write_text("before\n", encoding="utf-8")
        self.assertEqual(self.git("add", ".").returncode, 0)
        self.assertEqual(self.git("commit", "-qm", "base").returncode, 0)

        def stage_target(index: int) -> dict:
            target = targets[index]
            return self.stage(
                {
                    "mode": "replace",
                    "path": f"evelyn_core/module_{index}.py",
                    "oldText": "before",
                    "newText": "after",
                    "expectedSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                }
            )

        first = stage_target(0)
        self.assertEqual(first["code"], "workspace_edit_staged")
        self.assertEqual(stage_target(1)["code"], "workspace_edit_stage_capacity_reached")
        cancelled = self.mutate(
            self.mutation_request(
                first["evidence"],
                operation="cancel",
                request_id="mutation-cancel-slot",
            )
        )
        self.assertEqual(cancelled["result"]["code"], "workspace_edit_stage_cancelled")

        second = stage_target(1)
        self.assertEqual(second["code"], "workspace_edit_staged")
        applied = self.mutate(
            self.mutation_request(
                second["evidence"],
                request_id="mutation-apply-slot",
            )
        )
        self.assertEqual(applied["result"]["code"], "workspace_edit_completed")
        self.assertEqual(stage_target(2)["code"], "workspace_edit_staged")

    def test_stage_reports_staged_combined_and_untracked_status_exactly(self) -> None:
        self.init_git()
        target = self.project_root / "evelyn_core" / "module.py"
        target.parent.mkdir()
        target.write_text("base\n", encoding="utf-8")
        self.assertEqual(self.git("add", ".").returncode, 0)
        self.assertEqual(self.git("commit", "-qm", "base").returncode, 0)

        target.write_text("index\n", encoding="utf-8")
        self.assertEqual(self.git("add", "evelyn_core/module.py").returncode, 0)
        staged = self.stage(
            {
                "mode": "replace",
                "path": "evelyn_core/module.py",
                "oldText": "index",
                "newText": "candidate",
                "expectedSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )["evidence"]
        self.assertEqual(staged["dirtyStatus"], "staged")
        self.assertEqual(staged["gitStatus"], "M  evelyn_core/module.py")
        self.assertTrue(staged["tracked"])
        self.stages.clear()

        target.write_text("worktree\n", encoding="utf-8")
        combined = self.stage(
            {
                "mode": "replace",
                "path": "evelyn_core/module.py",
                "oldText": "worktree",
                "newText": "candidate",
                "expectedSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )["evidence"]
        self.assertEqual(combined["dirtyStatus"], "modified_and_staged")
        self.assertEqual(combined["gitStatus"], "MM evelyn_core/module.py")
        self.assertTrue(combined["tracked"])
        self.stages.clear()

        untracked_target = self.project_root / "evelyn_core" / "untracked.py"
        untracked_target.write_text("mine\n", encoding="utf-8")
        untracked = self.stage(
            {
                "mode": "replace",
                "path": "evelyn_core/untracked.py",
                "oldText": "mine",
                "newText": "candidate",
                "expectedSha256": hashlib.sha256(
                    untracked_target.read_bytes()
                ).hexdigest(),
            }
        )["evidence"]
        self.assertEqual(untracked["dirtyStatus"], "untracked")
        self.assertEqual(untracked["gitStatus"], "?? evelyn_core/untracked.py")
        self.assertFalse(untracked["tracked"])
        self.assertTrue(untracked["dirtyBaseAcknowledgementRequired"])

    def test_staged_create_is_atomic_and_replace_identity_drift_is_blocked(self) -> None:
        self.init_git()
        source = self.project_root / "evelyn_core" / "source.py"
        source.parent.mkdir()
        source.write_text("same bytes\n", encoding="utf-8")
        self.assertEqual(self.git("add", ".").returncode, 0)
        self.assertEqual(self.git("commit", "-qm", "base").returncode, 0)
        staged_replace = self.stage(
            {
                "mode": "replace",
                "path": "evelyn_core/source.py",
                "oldText": "same bytes",
                "newText": "changed",
                "expectedSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
        )
        replacement = source.with_name("replacement.tmp")
        replacement.write_bytes(source.read_bytes())
        os.replace(replacement, source)
        blocked = self.mutate(self.mutation_request(staged_replace["evidence"]))
        self.assertEqual(blocked["result"]["code"], "workspace_edit_base_changed")
        self.assertEqual(source.read_text(encoding="utf-8"), "same bytes\n")

        staged_create = self.stage(
            {
                "mode": "create",
                "path": "evelyn_core/new.py",
                "newText": "value = 1\n",
            },
            at=1003.0,
        )
        created = self.mutate(
            self.mutation_request(
                staged_create["evidence"],
                dirty_acknowledged=False,
                request_id="mutation-create",
            ),
            at=1004.0,
        )
        created_target = self.project_root / "evelyn_core" / "new.py"
        self.assertEqual(created["result"]["code"], "workspace_create_completed")
        self.assertEqual(created_target.read_bytes(), b"value = 1\n")
        self.assertEqual(
            hashlib.sha256(created_target.read_bytes()).hexdigest(),
            staged_create["evidence"]["candidateSha256"],
        )

    def test_stage_rejects_oversized_full_diff_and_trust_graph_paths(self) -> None:
        self.init_git()
        target = self.project_root / "evelyn_core" / "module.py"
        target.parent.mkdir()
        target.write_text("short\n", encoding="utf-8")
        self.assertEqual(self.git("add", ".").returncode, 0)
        self.assertEqual(self.git("commit", "-qm", "base").returncode, 0)
        too_large = self.stage(
            {
                "mode": "replace",
                "path": "evelyn_core/module.py",
                "oldText": "short",
                "newText": "x" * (WORKSPACE_EDIT_MAX_PREVIEW_BYTES + 1024),
                "expectedSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )
        self.assertEqual(too_large["code"], "workspace_edit_preview_too_large")
        self.assertEqual(self.stages, {})
        bounded = self.stage(
            {
                "mode": "replace",
                "path": "evelyn_core/module.py",
                "oldText": "short",
                "newText": "y" * (WORKSPACE_EDIT_MAX_PREVIEW_BYTES - 2048),
                "expectedSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )
        self.assertEqual(bounded["code"], "workspace_edit_staged")
        self.assertIn("fullDiff", bounded["evidence"])
        self.assertFalse(bounded["evidence"]["diffTruncated"])
        self.assertNotIn("preview", bounded["evidence"])
        self.stages.clear()

        escaped = self.stage(
            {
                "mode": "replace",
                "path": "evelyn_core/module.py",
                "oldText": "short",
                "newText": "\\" * (WORKSPACE_EDIT_MAX_PREVIEW_BYTES - 4096),
                "expectedSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )
        self.assertEqual(escaped["code"], "workspace_edit_preview_too_large")
        self.assertEqual(self.stages, {})

        protected = (
            "main.py",
            ".env.example",
            "docker-compose.fast-control.yml",
            "docker/requirements-gpu.txt",
            "docker/workspace_test_runner.py",
            "evelyn_core/start_local.bat",
            "docs/index.html",
            "docs/assets/evelyn-ui-action.js",
            "docs/assets/evelyn-task-approval.js",
            "docs/assets/evelyn-task-approval.css",
            "evelyn_core/runtime/evelyn_core/control_page_http.py",
            "evelyn_core/runtime/evelyn_core/workspace_test_sandbox.py",
            "evelyn_core/runtime/evelyn_core/text.py",
            "evelyn_core/runtime/evelyn_core/memory_exposure.py",
            "evelyn_core/runtime/evelyn_core/turn_lifecycle.py",
            "evelyn_core/runtime/evelyn_core/runtime_health.py",
            "evelyn_core/runtime/evelyn_core/fast_context_contract.py",
            "evelyn_core/runtime/evelyn_core/search_tools.py",
            "evelyn_core/runtime/evelyn_core/voice_orchestration.py",
            "evelyn_core/runtime/evelyn_core/voice_route_execution.py",
            "evelyn_core/runtime/evelyn_core/cognitive_policy_state.py",
            "evelyn_core/runtime/evelyn_core/memory.py",
            "evelyn_core/runtime/hmac.py",
            "evelyn_core/runtime/aiohttp.py",
            "evelyn_core/runtime/evelyn_core/skills/registry.py",
            "tests/runtime/test_untrusted.py",
            "external/mindcraft_evelyn/tests/evaluator.mjs",
            "external/mindcraft_evelyn/src/widget.test.mjs",
            "external/mindcraft_evelyn/src/widget_test.py",
            "external/mindcraft_evelyn/src/test_widget.py",
        )
        for relative in protected:
            with self.subTest(relative=relative):
                protected_target = self.project_root / relative
                protected_target.parent.mkdir(parents=True, exist_ok=True)
                protected_target.write_text("before\n", encoding="utf-8")
                result = self.stage(
                    {
                        "mode": "replace",
                        "path": relative,
                        "oldText": "before",
                        "newText": "bypass",
                        "expectedSha256": hashlib.sha256(
                            protected_target.read_bytes()
                        ).hexdigest(),
                    }
                )
                self.assertEqual(result["code"], "workspace_authority_edit_denied")
                self.assertEqual(protected_target.read_text(encoding="utf-8"), "before\n")

    def test_mutation_hmac_is_separate_from_read_queue_hmac(self) -> None:
        self.init_git()
        target = self.project_root / "evelyn_core" / "module.py"
        target.parent.mkdir()
        target.write_text("before\n", encoding="utf-8")
        staged = self.stage(
            {
                "mode": "replace",
                "path": "evelyn_core/module.py",
                "oldText": "before",
                "newText": "after",
                "expectedSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )
        forged = self.mutation_request(staged["evidence"], token=AUTH_TOKEN)
        result = self.mutate(forged)

        self.assertEqual(result["result"]["code"], "workspace_mutation_auth_invalid")
        self.assertEqual(target.read_text(encoding="utf-8"), "before\n")
        self.assertEqual(len(self.stages), 1)

    def test_stage_and_mutation_clients_use_separate_host_queues(self) -> None:
        self.init_git()
        target = self.project_root / "evelyn_core" / "module.py"
        target.parent.mkdir()
        target.write_text("before\n", encoding="utf-8")
        self.assertEqual(self.git("add", ".").returncode, 0)
        self.assertEqual(self.git("commit", "-qm", "base").returncode, 0)
        supervisor = HostSupervisor(
            project_root=self.project_root,
            artifacts_root=self.artifacts_root,
            run_command=subprocess.run,
            retention_reporter=FakeRetentionReporter(),
            process_owner=FakeProcessOwner(),
            bridge_lock_probe=lambda: True,
            workspace_task_auth_token=AUTH_TOKEN,
            workspace_mutation_auth_token=MUTATION_TOKEN,
            workspace_sandbox_auth_token=SANDBOX_TOKEN,
            workspace_test_sandbox=FakeWorkspaceTestSandbox(),
        )
        atomic_json_write(supervisor.status_path, supervisor.status())
        task_client = WorkspaceTaskHostClient(
            root=self.artifacts_root,
            timeout_sec=1.0,
            auth_token=AUTH_TOKEN,
            sandbox_auth_token=SANDBOX_TOKEN,
        )
        mutation_client = WorkspaceMutationHostClient(
            root=self.artifacts_root,
            timeout_sec=3.0,
            auth_token=MUTATION_TOKEN,
        )
        self.assertTrue(task_client.available())
        self.assertTrue(mutation_client.available())

        def serve_once(directory: Path) -> threading.Thread:
            def run() -> None:
                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline:
                    if list(directory.glob("*.json")):
                        supervisor.process_request_queue()
                        return
                    time.sleep(0.005)

            thread = threading.Thread(target=run)
            thread.start()
            return thread

        def stage_after_unlink(request: dict, **kwargs) -> dict:
            self.assertEqual(list(supervisor.requests_dir.glob("*.json")), [])
            return handle_workspace_task_request(request, **kwargs)

        thread = serve_once(supervisor.requests_dir)
        with patch(
            "evelyn_core.host_supervisor.handle_workspace_task_request",
            side_effect=stage_after_unlink,
        ):
            staged = task_client.stage_edit(
                "task-1",
                1,
                {
                    "mode": "replace",
                    "path": "evelyn_core/module.py",
                    "oldText": "before",
                    "newText": "after",
                    "expectedSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                },
                grant_id="grant-1",
                action_run_id="action-1",
                surface="control_page",
            )
        thread.join(timeout=1.0)
        self.assertEqual(staged["code"], "workspace_edit_staged")
        response_keys: set[str] = set()

        def collect_keys(value) -> None:
            if isinstance(value, dict):
                response_keys.update(str(key) for key in value)
                for item in value.values():
                    collect_keys(item)
            elif isinstance(value, list):
                for item in value:
                    collect_keys(item)

        collect_keys(staged)
        self.assertTrue({"args", "oldText", "newText"}.isdisjoint(response_keys))
        evidence = staged["evidence"]
        claim = {
            "approvalId": "approval-1",
            "claimId": "claim-1",
            "stageId": evidence["stageId"],
            "hostInstanceId": evidence["hostInstanceId"],
            "taskId": "task-1",
            "grantId": "grant-1",
            "grantExpiresAt": time.time() + 2.0,
            "actionRunId": "action-1",
            "stepId": 1,
            "surface": "control_page",
            "tool": "edit",
            "argsHash": evidence["argsHash"],
            "baseSha256": evidence["baseSha256"],
            "candidateSha256": evidence["candidateSha256"],
            "previewDigest": evidence["previewDigest"],
            "dirtyBaseAcknowledged": False,
        }
        mutation_requests: list[dict] = []

        def apply_after_unlink(request: dict, **kwargs) -> dict:
            self.assertEqual(list(supervisor.requests_dir.glob("*.json")), [])
            mutation_requests.append(dict(request))
            return handle_workspace_mutation_request(request, **kwargs)

        thread = serve_once(supervisor.mutation_requests_dir)
        with patch(
            "evelyn_core.host_supervisor.handle_workspace_mutation_request",
            side_effect=apply_after_unlink,
        ):
            applied = mutation_client.apply(claim)
        thread.join(timeout=1.0)

        self.assertEqual(applied["code"], "workspace_edit_completed")
        self.assertEqual(len(mutation_requests), 1)
        self.assertEqual(
            mutation_requests[0]["grantExpiresAt"],
            claim["grantExpiresAt"],
        )
        self.assertEqual(
            mutation_requests[0]["expiresAt"],
            claim["grantExpiresAt"],
        )
        self.assertEqual(target.read_text(encoding="utf-8"), "after\n")
        self.assertEqual(list(supervisor.mutation_requests_dir.glob("*.json")), [])

        thread = serve_once(supervisor.requests_dir)
        with patch(
            "evelyn_core.host_supervisor.handle_workspace_task_request",
            side_effect=stage_after_unlink,
        ):
            staged_for_cancel = task_client.stage_edit(
                "task-1",
                1,
                {
                    "mode": "replace",
                    "path": "evelyn_core/module.py",
                    "oldText": "after",
                    "newText": "must-not-apply",
                    "expectedSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                },
                grant_id="grant-1",
                action_run_id="action-1",
                surface="control_page",
            )
        thread.join(timeout=1.0)
        self.assertEqual(staged_for_cancel["code"], "workspace_edit_staged")
        cancel_evidence = staged_for_cancel["evidence"]

        thread = serve_once(supervisor.mutation_requests_dir)
        with patch(
            "evelyn_core.host_supervisor.handle_workspace_mutation_request",
            side_effect=apply_after_unlink,
        ):
            cancelled = mutation_client.cancel_stage(
                cancel_evidence,
                task_id="task-1",
                grant_id="grant-1",
                action_run_id="action-1",
                step_id=1,
            )
        thread.join(timeout=1.0)

        self.assertEqual(cancelled["code"], "workspace_edit_stage_cancelled")
        self.assertEqual(len(mutation_requests), 2)
        cancel_request = mutation_requests[-1]
        self.assertEqual(cancel_request["operation"], "cancel")
        self.assertGreater(
            cancel_request["grantExpiresAt"],
            cancel_request["issuedAt"],
        )
        self.assertLessEqual(
            cancel_request["grantExpiresAt"] - cancel_request["issuedAt"],
            mutation_client.timeout_sec,
        )
        self.assertNotIn(cancel_evidence["stageId"], supervisor._workspace_edit_stages)
        self.assertEqual(target.read_text(encoding="utf-8"), "after\n")

    def test_shared_queue_never_dispatches_edit_or_test(self) -> None:
        test_path = self.project_root / "tests" / "test_side_effect.py"
        test_path.parent.mkdir()
        test_path.write_text("raise AssertionError('must not run')\n", encoding="utf-8")
        edit = self.handle(
            self.request(
                tool="edit",
                args={
                    "mode": "create",
                    "path": "evelyn_core/created.py",
                    "newText": "value = 1\n",
                },
            ),
        )
        test = self.handle(
            self.request(
                tool="test",
                args={
                    "runner": "python_pytest",
                    "targets": ["tests/test_side_effect.py"],
                },
                requiresSandboxTest=True,
                candidateStageId="stage-missing",
            ),
        )

        self.assertEqual(
            edit["result"]["code"],
            "workspace_host_authorization_required",
        )
        self.assertEqual(
            test["result"]["code"],
            "workspace_test_sandbox_required",
        )
        self.assertFalse((self.project_root / "evelyn_core" / "created.py").exists())
        self.assertEqual(self.commands, [])

    def test_behavioral_stage_flag_is_sandbox_signed_and_discard_is_exact(self) -> None:
        self.init_git()
        target = self.project_root / "evelyn_core" / "module.py"
        target.parent.mkdir()
        target.write_text("before\n", encoding="utf-8")
        self.assertEqual(self.git("add", ".").returncode, 0)
        self.assertEqual(self.git("commit", "-qm", "base").returncode, 0)
        args = {
            "mode": "replace",
            "path": "evelyn_core/module.py",
            "oldText": "before",
            "newText": "after",
            "expectedSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }
        request = self.request(
            tool="edit",
            args=args,
            requiresSandboxTest=True,
        )
        forged = {
            key: value
            for key, value in request.items()
            if key not in {"authAlgorithm", "authTag"}
        }
        forged["requiresSandboxTest"] = False
        forged = sign_payload(forged, domain=WORKSPACE_TASK_REQUEST_AUTH_DOMAIN)
        rejected = self.handle(forged, staged_edits=self.stages, sandbox_ready=True)
        unready = self.handle(request, staged_edits=self.stages)
        staged = self.handle(request, staged_edits=self.stages, sandbox_ready=True)

        self.assertEqual(rejected["result"]["code"], "workspace_sandbox_auth_invalid")
        self.assertEqual(unready["result"]["code"], "workspace_test_sandbox_unavailable")
        self.assertEqual(staged["result"]["code"], "workspace_edit_staged")
        evidence = staged["result"]["evidence"]
        self.assertNotIn("requiresSandboxTest", evidence)
        self.assertTrue(self.stages[evidence["stageId"]]["requiresSandboxTest"])
        discard = self.handle(
            self.request(
                tool="test",
                args={"runner": "discard", "targets": []},
                stepId=2,
                actionRunId="action-2",
                requiresSandboxTest=True,
                candidateStageId=evidence["stageId"],
            ),
            at=1002.0,
            staged_edits=self.stages,
        )
        self.assertEqual(discard["result"]["code"], "workspace_edit_stage_cancelled")
        self.assertEqual(self.stages, {})
        self.assertEqual(target.read_text(encoding="utf-8"), "before\n")

    def test_exact_literal_stage_can_be_discarded_only_by_its_original_step(self) -> None:
        self.init_git()
        target = self.project_root / "evelyn_core" / "module.py"
        target.parent.mkdir()
        target.write_text("before\n", encoding="utf-8")
        staged = self.stage(
            {
                "mode": "replace",
                "path": "evelyn_core/module.py",
                "oldText": "before",
                "newText": "after",
                "expectedSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
        )
        stage_id = staged["evidence"]["stageId"]
        wrong = self.handle(
            self.request(
                tool="test",
                args={"runner": "discard", "targets": []},
                requestId="literal-discard-wrong",
                actionRunId="different-action",
                requiresSandboxTest=True,
                candidateStageId=stage_id,
            ),
            staged_edits=self.stages,
        )
        exact = self.handle(
            self.request(
                tool="test",
                args={"runner": "discard", "targets": []},
                requestId="literal-discard-exact",
                requiresSandboxTest=True,
                candidateStageId=stage_id,
            ),
            staged_edits=self.stages,
        )

        self.assertEqual(wrong["result"]["code"], "workspace_test_stage_binding_mismatch")
        self.assertEqual(exact["result"]["code"], "workspace_edit_stage_cancelled")
        self.assertEqual(self.stages, {})
        self.assertEqual(target.read_text(encoding="utf-8"), "before\n")

    def test_every_nonpass_test_terminal_disposes_the_exact_stage(self) -> None:
        self.init_git()
        target = self.project_root / "evelyn_core" / "module.py"
        target.parent.mkdir()
        target.write_text("before\n", encoding="utf-8")
        self.assertEqual(self.git("add", ".").returncode, 0)
        self.assertEqual(self.git("commit", "-qm", "base").returncode, 0)
        edit_args = {
            "mode": "replace",
            "path": "evelyn_core/module.py",
            "oldText": "before",
            "newText": "after",
            "expectedSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }

        terminals = (
            {
                "attempted": True,
                "executed": False,
                "observed": True,
                "verified": True,
                "outcome": "blocked",
                "code": "workspace_test_capacity_reached",
                "summary": "blocked",
                "evidence": {},
            },
            {
                "attempted": True,
                "executed": True,
                "observed": False,
                "verified": False,
                "outcome": "outcome_unverified",
                "code": "workspace_test_outcome_unverified",
                "summary": "unverified",
                "evidence": {},
            },
        )
        for index, terminal in enumerate(terminals, 1):
            with self.subTest(code=terminal["code"]):
                staged = stage_workspace_edit(
                    project_root=self.project_root,
                    args=edit_args,
                    task_id="task-1",
                    grant_id="grant-1",
                    action_run_id=f"edit-action-{index}",
                    step_id=index,
                    surface="control_page",
                    host_instance_id=HOST_INSTANCE_ID,
                    stages=self.stages,
                    requires_sandbox_test=True,
                    now=lambda: 1000.0,
                )
                stage_id = staged["evidence"]["stageId"]
                response = self.handle(
                    self.request(
                        tool="test",
                        args={"runner": "python_unittest", "targets": ["tests/test_module.py"]},
                        requestId=f"test-request-{index}",
                        stepId=index + 2,
                        actionRunId=f"test-action-{index}",
                        requiresSandboxTest=True,
                        candidateStageId=stage_id,
                    ),
                    at=1002.0,
                    staged_edits=self.stages,
                    workspace_test_executor=lambda **_: terminal,
                )
                self.assertEqual(response["result"]["code"], terminal["code"])
                self.assertEqual(self.stages, {})

        staged = stage_workspace_edit(
            project_root=self.project_root,
            args=edit_args,
            task_id="task-1",
            grant_id="grant-1",
            action_run_id="edit-action-unavailable",
            step_id=5,
            surface="control_page",
            host_instance_id=HOST_INSTANCE_ID,
            stages=self.stages,
            requires_sandbox_test=True,
            now=lambda: 1000.0,
        )
        stage_id = staged["evidence"]["stageId"]
        unavailable = self.handle(
            self.request(
                tool="test",
                args={"runner": "python_unittest", "targets": ["tests/test_module.py"]},
                requestId="test-request-unavailable",
                stepId=6,
                actionRunId="test-action-unavailable",
                requiresSandboxTest=True,
                candidateStageId=stage_id,
            ),
            at=1002.0,
            staged_edits=self.stages,
        )
        self.assertEqual(unavailable["result"]["code"], "workspace_test_sandbox_required")
        self.assertEqual(self.stages, {})

    def test_runner_report_cannot_claim_semantic_verification(self) -> None:
        self.init_git()
        target = self.project_root / "evelyn_core" / "module.py"
        target.parent.mkdir()
        target.write_text("before\n", encoding="utf-8")
        staged = stage_workspace_edit(
            project_root=self.project_root,
            args={
                "mode": "replace",
                "path": "evelyn_core/module.py",
                "oldText": "before",
                "newText": "after",
                "expectedSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            },
            task_id="task-1",
            grant_id="grant-1",
            action_run_id="edit-action",
            step_id=1,
            surface="control_page",
            host_instance_id=HOST_INSTANCE_ID,
            stages=self.stages,
            requires_sandbox_test=True,
            now=lambda: 1000.0,
        )
        stage_id = staged["evidence"]["stageId"]

        def forged_semantic_pass(*, stage, args, external_tracked_paths):
            del external_tracked_paths
            result = sandbox_pass(stage, args, frozenset())
            result["evidence"]["semanticVerified"] = True
            return result

        response = self.handle(
            self.request(
                tool="test",
                args={"runner": "python_unittest", "targets": ["tests/test_module.py"]},
                requestId="semantic-forgery",
                actionRunId="test-action",
                stepId=2,
                requiresSandboxTest=True,
                candidateStageId=stage_id,
            ),
            at=1002.0,
            staged_edits=self.stages,
            workspace_test_executor=forged_semantic_pass,
        )

        self.assertEqual(response["result"]["code"], "workspace_test_binding_invalid")
        self.assertEqual(self.stages, {})

    def test_only_sandbox_test_requests_have_the_extended_signed_ttl(self) -> None:
        self.assertEqual(
            WorkspaceTaskHostClient(
                root=self.artifacts_root,
                auth_token=AUTH_TOKEN,
                sandbox_auth_token=SANDBOX_TOKEN,
            ).timeout_sec,
            38.0,
        )
        test_response = self.handle(
            self.request(
                tool="test",
                args={
                    "runner": "python_unittest",
                    "targets": ["tests/test_module.py"],
                },
                requestId="long-test-request",
                requiresSandboxTest=True,
                candidateStageId="stage-missing",
                issuedAt=1000.0,
                expiresAt=1040.0,
            ),
            at=1001.0,
            staged_edits={},
        )
        edit_response = self.handle(
            self.request(
                tool="edit",
                args={
                    "mode": "create",
                    "path": "evelyn_core/new.py",
                    "newText": "value = 1\n",
                },
                requestId="long-edit-request",
                issuedAt=1000.0,
                expiresAt=1040.0,
            ),
            at=1001.0,
            staged_edits={},
            sandbox_ready=True,
        )

        self.assertEqual(test_response["result"]["code"], "workspace_edit_stage_unavailable")
        self.assertEqual(edit_response["result"]["code"], "workspace_request_time_invalid")

    def test_behavioral_apply_requires_the_exact_tested_tree(self) -> None:
        self.init_git()
        target = self.project_root / "evelyn_core" / "module.py"
        target.parent.mkdir()
        target.write_text("before\n", encoding="utf-8")
        test_target = self.project_root / "tests" / "test_module.py"
        test_target.parent.mkdir()
        test_target.write_text("import unittest\n", encoding="utf-8")
        self.assertEqual(self.git("add", ".").returncode, 0)
        self.assertEqual(self.git("commit", "-qm", "base").returncode, 0)
        manifest = build_workspace_tracked_manifest(self.project_root)
        edit_args = {
            "mode": "replace",
            "path": "evelyn_core/module.py",
            "oldText": "before",
            "newText": "after",
            "expectedSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }
        staged = stage_workspace_edit(
            project_root=self.project_root,
            args=edit_args,
            task_id="task-1",
            grant_id="grant-1",
            action_run_id="action-1",
            step_id=1,
            surface="control_page",
            host_instance_id=HOST_INSTANCE_ID,
            stages=self.stages,
            requires_sandbox_test=True,
            run_command=subprocess.run,
            now=lambda: 1000.0,
        )
        evidence = staged["evidence"]
        untested = self.mutate(
            self.mutation_request(evidence, request_id="mutation-untested"),
            external_tracked_paths=manifest,
        )
        self.assertEqual(untested["result"]["code"], "workspace_sandbox_test_required")
        stage = self.stages[evidence["stageId"]]
        digests = workspace_stage_tree_digests(
            self.project_root,
            stage=stage,
            workspace_tracked_paths=manifest,
        )
        stage["testedBaseTreeSha256"] = digests["baseTreeSha256"]
        stage["testedCandidateTreeSha256"] = digests["candidateTreeSha256"]
        stage["testedRunner"] = "python_unittest"
        stage["testedTargets"] = ("tests/test_module.py",)
        stage["testedTestsRun"] = 1
        stage["testedSemanticVerified"] = False
        applied = self.mutate(
            self.mutation_request(evidence, request_id="mutation-tested"),
            external_tracked_paths=manifest,
        )
        self.assertEqual(applied["result"]["code"], "workspace_edit_completed")
        self.assertIs(applied["result"]["evidence"]["semanticVerified"], False)
        self.assertEqual(target.read_text(encoding="utf-8"), "after\n")

        staged = stage_workspace_edit(
            project_root=self.project_root,
            args={
                **edit_args,
                "oldText": "after",
                "newText": "final",
                "expectedSha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            },
            task_id="task-1",
            grant_id="grant-1",
            action_run_id="action-1",
            step_id=1,
            surface="control_page",
            host_instance_id=HOST_INSTANCE_ID,
            stages=self.stages,
            requires_sandbox_test=True,
            run_command=subprocess.run,
            now=lambda: 1003.0,
        )
        stale_evidence = staged["evidence"]
        stale_stage = self.stages[stale_evidence["stageId"]]
        digests = workspace_stage_tree_digests(
            self.project_root,
            stage=stale_stage,
            workspace_tracked_paths=manifest,
        )
        stale_stage["testedBaseTreeSha256"] = digests["baseTreeSha256"]
        stale_stage["testedCandidateTreeSha256"] = digests["candidateTreeSha256"]
        stale_stage["testedRunner"] = "python_unittest"
        stale_stage["testedTargets"] = ("tests/test_module.py",)
        stale_stage["testedTestsRun"] = 1
        stale_stage["testedSemanticVerified"] = False
        test_target.write_text("import unittest\n# changed after test\n", encoding="utf-8")
        stale = self.mutate(
            self.mutation_request(
                stale_evidence,
                request_id="mutation-stale-tree",
                dirty_acknowledged=True,
            ),
            at=1004.0,
            external_tracked_paths=manifest,
        )
        self.assertEqual(stale["result"]["code"], "workspace_test_tree_stale")
        self.assertEqual(target.read_text(encoding="utf-8"), "after\n")

    def test_host_list_search_and_diff_workers_do_not_block_the_main_queue(self) -> None:
        for tool, args in (
            ("list", {"path": ".", "recursive": True}),
            ("search", {"path": ".", "query": "needle"}),
            ("diff", {"paths": ["README.md"]}),
        ):
            with self.subTest(tool=tool):
                started = threading.Event()
                release = threading.Event()
                calls: list[str] = []

                def slow_execute(**kwargs) -> dict:
                    calls.append(kwargs["tool"])
                    started.set()
                    release.wait(2.0)
                    return {
                        "attempted": True,
                        "executed": True,
                        "observed": True,
                        "verified": True,
                        "outcome": "succeeded",
                        "code": f"workspace_{tool}_completed",
                        "summary": "completed",
                        "evidence": {"truncated": False},
                    }

                supervisor = HostSupervisor(
                    project_root=self.project_root,
                    artifacts_root=self.artifacts_root / tool,
                    run_command=self.run_command,
                    now=lambda: 1001.0,
                    retention_reporter=FakeRetentionReporter(),
                    process_owner=FakeProcessOwner(),
                    bridge_lock_probe=lambda: True,
                    workspace_task_auth_token=AUTH_TOKEN,
                    workspace_test_sandbox=FakeWorkspaceTestSandbox(),
                )
                self.addCleanup(supervisor._workspace_test_worker.close)
                self.addCleanup(supervisor._workspace_query_worker.close)
                self.addCleanup(release.set)
                request_id = f"slow-{tool}"
                request = self.request(
                    tool=tool,
                    args=args,
                    requestId=request_id,
                    hostInstanceId=supervisor.host_instance_id,
                    issuedAt=1001.0,
                )
                atomic_json_write(
                    supervisor.requests_dir / f"{request_id}.json",
                    request,
                )
                response_path = supervisor.responses_dir / f"{request_id}.json"

                with (
                    patch.object(
                        workspace_tools,
                        "execute_workspace_task_tool",
                        side_effect=slow_execute,
                    ),
                    patch(
                        "evelyn_core.host_supervisor.atomic_json_write",
                        wraps=atomic_json_write,
                    ) as write_response,
                ):
                    began = time.monotonic()
                    supervisor.process_request_queue()
                    elapsed = time.monotonic() - began

                    self.assertLess(elapsed, 0.5)
                    self.assertTrue(started.wait(1.0))
                    self.assertEqual(calls, [tool])
                    self.assertIsNotNone(supervisor._workspace_query_pending)
                    self.assertFalse(response_path.exists())

                    atomic_json_write(
                        supervisor.requests_dir / f"{request_id}.json",
                        request,
                    )
                    supervisor.process_request_queue()
                    self.assertEqual(calls, [tool])
                    self.assertFalse(response_path.exists())

                    release.set()
                    deadline = time.monotonic() + 1.0
                    while not response_path.exists() and time.monotonic() < deadline:
                        supervisor.process_request_queue()
                        time.sleep(0.001)
                    self.assertTrue(response_path.exists())
                    response = json.loads(response_path.read_text(encoding="utf-8"))
                    self.assertEqual(
                        response["result"]["code"],
                        f"workspace_{tool}_completed",
                    )
                    self.assertEqual(response["requestId"], request_id)
                    self.assertEqual(response["argsHash"], request["argsHash"])
                    self.assertTrue(
                        workspace_tools._workspace_task_payload_is_authentic(
                            response,
                            auth_token=AUTH_TOKEN,
                            domain=WORKSPACE_TASK_RESPONSE_AUTH_DOMAIN,
                        )
                    )
                    for _ in range(3):
                        supervisor.process_request_queue()
                    writes = [
                        call
                        for call in write_response.call_args_list
                        if call.args and call.args[0] == response_path
                    ]
                    self.assertEqual(len(writes), 1)

    def test_host_query_worker_rejects_a_second_request_while_busy(self) -> None:
        started = threading.Event()
        release = threading.Event()
        calls: list[str] = []

        def slow_execute(**kwargs) -> dict:
            calls.append(kwargs["tool"])
            started.set()
            release.wait(2.0)
            return {
                "attempted": True,
                "executed": True,
                "observed": True,
                "verified": True,
                "outcome": "succeeded",
                "code": "workspace_search_completed",
                "summary": "completed",
                "evidence": {"truncated": False},
            }

        supervisor = HostSupervisor(
            project_root=self.project_root,
            artifacts_root=self.artifacts_root / "busy",
            run_command=self.run_command,
            now=lambda: 1001.0,
            retention_reporter=FakeRetentionReporter(),
            process_owner=FakeProcessOwner(),
            bridge_lock_probe=lambda: True,
            workspace_task_auth_token=AUTH_TOKEN,
            workspace_test_sandbox=FakeWorkspaceTestSandbox(),
        )
        self.addCleanup(supervisor._workspace_test_worker.close)
        self.addCleanup(supervisor._workspace_query_worker.close)
        self.addCleanup(release.set)
        first = self.request(
            tool="search",
            args={"path": ".", "query": "needle"},
            requestId="query-first",
            hostInstanceId=supervisor.host_instance_id,
            issuedAt=1001.0,
        )
        second = self.request(
            tool="diff",
            args={"paths": ["README.md"]},
            requestId="query-second",
            hostInstanceId=supervisor.host_instance_id,
            actionRunId="action-2",
            stepId=2,
            issuedAt=1001.0,
        )

        with patch.object(
            workspace_tools,
            "execute_workspace_task_tool",
            side_effect=slow_execute,
        ):
            atomic_json_write(supervisor.requests_dir / "query-first.json", first)
            supervisor.process_request_queue()
            self.assertTrue(started.wait(1.0))

            atomic_json_write(supervisor.requests_dir / "query-second.json", second)
            began = time.monotonic()
            supervisor.process_request_queue()
            elapsed = time.monotonic() - began

            self.assertLess(elapsed, 0.5)
            self.assertEqual(calls, ["search"])
            busy = json.loads(
                (supervisor.responses_dir / "query-second.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                busy["result"]["code"],
                "workspace_query_capacity_reached",
            )
            self.assertEqual(busy["result"]["outcome"], "blocked")
            self.assertEqual(busy["requestId"], second["requestId"])
            self.assertEqual(busy["actionRunId"], second["actionRunId"])
            self.assertEqual(busy["stepId"], second["stepId"])
            self.assertEqual(busy["tool"], second["tool"])
            self.assertEqual(busy["argsHash"], second["argsHash"])
            self.assertTrue(
                workspace_tools._workspace_task_payload_is_authentic(
                    busy,
                    auth_token=AUTH_TOKEN,
                    domain=WORKSPACE_TASK_RESPONSE_AUTH_DOMAIN,
                )
            )

            release.set()
            deadline = time.monotonic() + 1.0
            first_path = supervisor.responses_dir / "query-first.json"
            while not first_path.exists() and time.monotonic() < deadline:
                supervisor.process_request_queue()
                time.sleep(0.001)
            self.assertTrue(first_path.exists())
            self.assertEqual(calls, ["search"])

    def test_host_worker_is_nonblocking_and_replay_does_not_execute_twice(self) -> None:
        sandbox = FakeWorkspaceTestSandbox(ready=True, result=sandbox_pass)
        supervisor, staged = self.behavioral_supervisor(sandbox)
        request = self.submit_behavioral_test(supervisor, staged["stageId"])

        self.assertTrue(sandbox.started.wait(0.5))
        self.assertEqual(sandbox.calls, 1)
        self.assertIsNotNone(supervisor._workspace_test_pending)
        self.assertFalse((supervisor.responses_dir / "test-request.json").exists())

        atomic_json_write(supervisor.requests_dir / "test-request.json", request)
        supervisor.process_request_queue()
        replay = json.loads(
            (supervisor.responses_dir / "test-request.json").read_text(encoding="utf-8")
        )
        self.assertEqual(replay["result"]["code"], "workspace_request_replayed")
        self.assertEqual(sandbox.calls, 1)

        sandbox.release.set()
        deadline = time.monotonic() + 1.0
        while supervisor._workspace_test_pending is not None and time.monotonic() < deadline:
            supervisor.process_request_queue()
            time.sleep(0.001)
        self.assertIsNone(supervisor._workspace_test_pending)
        self.assertEqual(sandbox.calls, 1)
        self.assertIs(
            supervisor._workspace_edit_stages[staged["stageId"]][
                "testedSemanticVerified"
            ],
            False,
        )

    def test_discard_during_test_suppresses_pass_and_never_resurrects_stage(self) -> None:
        sandbox = FakeWorkspaceTestSandbox(ready=True, result=sandbox_pass)
        supervisor, staged = self.behavioral_supervisor(sandbox)
        stage_id = staged["stageId"]
        self.submit_behavioral_test(supervisor, stage_id)
        self.assertTrue(sandbox.started.wait(0.5))

        discard = self.request(
            tool="test",
            args={"runner": "discard", "targets": []},
            requestId="discard-request",
            hostInstanceId=supervisor.host_instance_id,
            actionRunId="discard-action",
            stepId=3,
            requiresSandboxTest=True,
            candidateStageId=stage_id,
            issuedAt=1001.0,
            expiresAt=1010.0,
        )
        atomic_json_write(supervisor.requests_dir / "discard-request.json", discard)
        supervisor.process_request_queue()
        cancelled = json.loads(
            (supervisor.responses_dir / "discard-request.json").read_text(encoding="utf-8")
        )
        self.assertEqual(cancelled["result"]["code"], "workspace_edit_stage_cancelled")
        self.assertNotIn(stage_id, supervisor._workspace_edit_stages)

        sandbox.release.set()
        deadline = time.monotonic() + 1.0
        while supervisor._workspace_test_pending is not None and time.monotonic() < deadline:
            supervisor.process_request_queue()
            time.sleep(0.001)
        response = json.loads(
            (supervisor.responses_dir / "test-request.json").read_text(encoding="utf-8")
        )
        self.assertNotEqual(response["result"]["code"], "workspace_test_passed")
        self.assertNotIn(stage_id, supervisor._workspace_edit_stages)

    def test_pass_response_write_failure_invalidates_the_exact_stage(self) -> None:
        sandbox = FakeWorkspaceTestSandbox(ready=True, result=sandbox_pass)
        supervisor, staged = self.behavioral_supervisor(sandbox)
        stage_id = staged["stageId"]
        self.submit_behavioral_test(supervisor, stage_id)
        self.assertTrue(sandbox.started.wait(0.5))
        sandbox.release.set()
        deadline = time.monotonic() + 1.0
        while supervisor._workspace_test_worker.busy and time.monotonic() < deadline:
            time.sleep(0.001)
        with patch(
            "evelyn_core.host_supervisor.atomic_json_write",
            side_effect=OSError("response unavailable"),
        ):
            supervisor._poll_workspace_test_response()

        self.assertIsNone(supervisor._workspace_test_pending)
        self.assertNotIn(stage_id, supervisor._workspace_edit_stages)
        self.assertEqual(supervisor.last_error, "workspace_test_response_write_failed")


if __name__ == "__main__":
    unittest.main()
