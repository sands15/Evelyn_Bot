from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.conversation_observability_composition import (
    ConversationObservabilityComposition,
    ConversationObservabilityCompositionDeps,
)


class ConversationObservabilityCompositionTests(unittest.TestCase):
    def build_composition(self, **overrides):
        question_deps = object()
        question_state_deps = object()
        scope_registry = Mock(cancelled_stale_turn_count=4)
        metrics_store = Mock()
        values = dict(
            question_policy=lambda: question_deps,
            question_policy_state=lambda: question_state_deps,
            turn_scope_registry=scope_registry,
            turn_stage_metrics={"turn-1": {"stt": 12.0}},
            model_call_metrics_store=metrics_store,
            write_turn_trace_event=Mock(),
            turn_trace_json_log=True,
            bottleneck_events={"slow"},
            summary_events={"summary"},
            console_only_stt_and_reply=False,
            voice_bottleneck_logs=True,
            voice_trace_all_events=False,
            turn_trace_log_dir=REPO_ROOT / "logs",
            turn_trace_file_lock=object(),
            original_print=Mock(),
            trace_print=Mock(),
            monotonic=Mock(return_value=10.0),
            now=Mock(return_value=20.0),
            benchmark_log_path=REPO_ROOT / "benchmark.jsonl",
            project_root=REPO_ROOT,
            log=Mock(),
            record_turn_stage_metric=Mock(),
            summarize_voice_p95_metrics=Mock(return_value={"p95": 123.0}),
            get_search_followup_queued_count=Mock(return_value=3),
            build_rejected_voice_turn=Mock(),
        )
        values.update(overrides)
        deps = ConversationObservabilityCompositionDeps(**values)
        return ConversationObservabilityComposition(deps), deps, question_deps, question_state_deps

    def test_turn_trace_uses_explicit_configuration(self) -> None:
        composition, deps, *_ = self.build_composition()

        composition.log_turn_event("voice_ingress", turn_id="turn-1")

        deps.write_turn_trace_event.assert_called_once_with(
            "voice_ingress",
            {"turn_id": "turn-1"},
            turn_trace_json_log=True,
            bottleneck_events={"slow"},
            summary_events={"summary"},
            console_only_stt_and_reply=False,
            voice_bottleneck_logs=True,
            voice_trace_all_events=False,
            log_dir=REPO_ROOT / "logs",
            file_lock=deps.turn_trace_file_lock,
            original_print=deps.original_print,
            trace_print=deps.trace_print,
        )

    def test_voice_validation_observer_receives_same_event_payload(self) -> None:
        observer = Mock()
        composition, *_ = self.build_composition(voice_validation_observer=observer)

        composition.log_turn_event(
            "voice_turn_summary",
            turn_id="turn-1",
            validation_session_id="validation-1",
        )

        observer.assert_called_once_with(
            "voice_turn_summary",
            {
                "turn_id": "turn-1",
                "validation_session_id": "validation-1",
            },
        )

    def test_voice_validation_observer_failure_logs_only_exception_type(self) -> None:
        private_error = "PRIVATE_VALIDATION_OBSERVER C:/secret/voice-event.jsonl"
        observer = Mock(side_effect=RuntimeError(private_error))
        composition, deps, *_ = self.build_composition(
            voice_validation_observer=observer
        )

        composition.log_turn_event(
            "voice_turn_summary",
            turn_id="turn-1",
            playback_failed=True,
        )

        deps.original_print.assert_called_once_with(
            "[VOICE VALIDATION OBSERVER ERROR] errorType=RuntimeError"
        )
        self.assertNotIn(private_error, repr(deps.original_print.call_args_list))
        deps.write_turn_trace_event.assert_called_once()

    def test_validation_attempt_token_is_only_visible_to_internal_observer(self) -> None:
        observer = Mock()
        composition, deps, *_ = self.build_composition(
            voice_validation_observer=observer
        )
        payload = {
            "turn_id": "turn-1",
            "validation_attempt_id": "attempt-private-1",
            "meta": {
                "validationAttemptId": "attempt-private-2",
                "safe": "kept",
                "nested": [
                    {
                        "validation_attempt_id": "attempt-private-3",
                        "attemptId": "public-retry-2",
                        "count": 1,
                    }
                ],
            },
        }

        composition.log_turn_event("voice_turn_summary", **payload)

        observer.assert_called_once_with("voice_turn_summary", payload)
        writer_payload = deps.write_turn_trace_event.call_args.args[1]
        self.assertNotIn("validation_attempt_id", writer_payload)
        self.assertEqual(writer_payload["meta"]["safe"], "kept")
        self.assertNotIn("validationAttemptId", writer_payload["meta"])
        self.assertNotIn(
            "validation_attempt_id",
            writer_payload["meta"]["nested"][0],
        )
        self.assertEqual(
            writer_payload["meta"]["nested"][0]["attemptId"],
            "public-retry-2",
        )
        self.assertEqual(payload["validation_attempt_id"], "attempt-private-1")

    def test_turn_scope_and_model_metric_adapters_share_injected_stores(self) -> None:
        composition, deps, *_ = self.build_composition()
        scope = object()
        task = object()
        deps.turn_scope_registry.replace_room_scope.return_value = "old"

        self.assertEqual(
            composition.replace_room_turn_scope("room-1", scope, cancel_old=False), "old"
        )
        composition.detach_task(scope, task)
        composition.record_model_call_metric(
            model_role="main",
            purpose="answer",
            hot_path=True,
            success=True,
            latency_ms=25.0,
            first_token_ms=5.0,
        )

        deps.turn_scope_registry.replace_room_scope.assert_called_once_with(
            "room-1", scope, cancel_old=False
        )
        deps.turn_scope_registry.detach_task.assert_called_once_with(scope, task)
        deps.model_call_metrics_store.record_model_call.assert_called_once_with(
            model_role="main",
            purpose="answer",
            hot_path=True,
            success=True,
            latency_ms=25.0,
            first_token_ms=5.0,
        )

    def test_question_adapters_use_correct_live_dependency_factory(self) -> None:
        composition, _, question_deps, question_state_deps = self.build_composition()
        route = object()

        with patch(
            "evelyn_core.conversation_observability_composition.user_wants_direct_answer_from_runtime",
            return_value=True,
        ) as direct, patch(
            "evelyn_core.conversation_observability_composition.apply_fast_path_question_policy_from_runtime",
            return_value=(route, True),
        ) as apply_policy:
            self.assertTrue(composition.user_wants_direct_answer("바로 답해"))
            self.assertEqual(
                composition.apply_fast_path_question_policy(
                    route,
                    user_text="질문",
                    session_key="session-1",
                    route_meta_question_policy={"ask": False},
                ),
                (route, True),
            )

        direct.assert_called_once_with("바로 답해", deps=question_deps)
        apply_policy.assert_called_once_with(
            route,
            user_text="질문",
            session_key="session-1",
            route_meta_question_policy={"ask": False},
            deps=question_state_deps,
        )

    def test_p95_summary_reads_live_counters(self) -> None:
        composition, deps, *_ = self.build_composition()

        self.assertEqual(composition.summarize_p95_metrics(), {"p95": 123.0})

        deps.summarize_voice_p95_metrics.assert_called_once_with(
            deps.turn_stage_metrics,
            search_followup_queued_count=3,
            cancelled_stale_turn_count=4,
        )

    def test_all_moved_public_signatures_match_previous_main(self) -> None:
        mapping = {
            "log_turn_event": "log_turn_event",
            "record_model_call_trace": "record_model_call_trace",
            "record_context_pipeline_benchmark": "record_context_pipeline_benchmark",
            "merge_log_event_payload": "merge_log_event_payload",
            "replace_room_turn_scope": "replace_room_turn_scope",
            "get_room_turn_scope": "get_room_turn_scope",
            "_attach_current_task": "attach_current_task",
            "_detach_task": "detach_task",
            "create_turn_scoped_task": "create_turn_scoped_task",
            "clear_room_turn_scope": "clear_room_turn_scope",
            "record_turn_stage": "record_turn_stage",
            "record_model_call_metric": "record_model_call_metric",
            "replay_model_call_metrics_from_turn_trace": "replay_model_call_metrics_from_turn_trace",
            "ensure_model_call_metrics_replayed": "ensure_model_call_metrics_replayed",
            "record_turn_path_summary": "record_turn_path_summary",
            "summarize_turn_path_metrics": "summarize_turn_path_metrics",
            "summarize_model_call_metrics": "summarize_model_call_metrics",
            "normalize_question_policy_mapping": "normalize_question_policy_mapping",
            "extract_question_policy_from_route_meta": "extract_question_policy_from_route_meta",
            "user_wants_direct_answer": "user_wants_direct_answer",
            "user_frustration_with_questions": "user_frustration_with_questions",
            "is_continuable_technical_topic": "is_continuable_technical_topic",
            "question_cooldown_hit": "question_cooldown_hit",
            "apply_fast_path_question_policy": "apply_fast_path_question_policy",
            "record_question_trace": "record_question_trace",
            "summarize_question_metrics": "summarize_question_metrics",
            "proactive_question_scope_candidates": "proactive_question_scope_candidates",
            "record_session_question_asked": "record_session_question_asked",
            "resolve_pending_proactive_question_for_turn": "resolve_pending_proactive_question_for_turn",
            "select_and_mark_proactive_question": "select_and_mark_proactive_question",
            "maybe_append_proactive_question": "maybe_append_proactive_question",
            "summarize_p95_metrics": "summarize_p95_metrics",
            "new_turn_metrics": "new_turn_metrics",
            "mark_turn_stage": "mark_turn_stage",
            "register_drop_reason": "register_drop_reason",
        }
        old_tree = ast.parse(
            subprocess.check_output(
                ["git", "show", "1bb370a:main.py"], text=True, encoding="utf-8"
            )
        )
        new_tree = ast.parse(
            (RUNTIME_ROOT / "evelyn_core" / "conversation_observability_composition.py")
            .read_text(encoding="utf-8")
        )
        old_functions = {
            node.name: node
            for node in old_tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        composition_class = next(
            node
            for node in new_tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "ConversationObservabilityComposition"
        )
        new_methods = {
            node.name: node
            for node in composition_class.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

        def signature(node, *, method=False):
            positional = [arg.arg for arg in node.args.posonlyargs + node.args.args]
            if method:
                positional = positional[1:]
            return (
                isinstance(node, ast.AsyncFunctionDef),
                positional,
                [ast.unparse(default) for default in node.args.defaults],
                node.args.vararg.arg if node.args.vararg else None,
                [
                    (arg.arg, None if default is None else ast.unparse(default))
                    for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults)
                ],
                node.args.kwarg.arg if node.args.kwarg else None,
            )

        mismatches = []
        for old_name, new_name in mapping.items():
            old_signature = signature(old_functions[old_name])
            new_signature = signature(new_methods[new_name], method=True)
            if old_signature != new_signature:
                mismatches.append((old_name, old_signature, new_signature))

        self.assertEqual(mismatches, [])

    def test_main_binds_composition_before_consumers(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        runtime_source = (
            RUNTIME_ROOT / "evelyn_core" / "conversation_observability_composition.py"
        ).read_text(encoding="utf-8")

        composition_index = source.index(
            "conversation_observability_composition = ConversationObservabilityComposition("
        )
        autonomy_index = source.index(
            "autonomy_runtime_composition = AutonomyRuntimeComposition("
        )
        continuity_index = source.index(
            "voice_barge_in_continuity_tracker = VoiceBargeInContinuityTracker("
        )
        self.assertLess(composition_index, autonomy_index)
        self.assertLess(composition_index, continuity_index)
        self.assertIn(
            "configure_tts_playback_logging(log_turn_event)", source
        )
        self.assertIn(
            "register_drop_reason = conversation_observability_composition.register_drop_reason",
            source,
        )
        self.assertNotIn("globals()", runtime_source)


if __name__ == "__main__":
    unittest.main()
