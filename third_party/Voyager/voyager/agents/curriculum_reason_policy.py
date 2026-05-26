from __future__ import annotations


class CurriculumReasonPolicy:
    def __init__(
        self,
        *,
        action_generation_failure_reason,
        reset_only_loop_failure_reason,
        search_failure_reasons,
        local_search_failure_snippets,
        local_search_exhausted_reason,
    ):
        self.action_generation_failure_reason = str(action_generation_failure_reason)
        self.reset_only_loop_failure_reason = str(reset_only_loop_failure_reason)
        self.search_failure_reasons = set(search_failure_reasons or set())
        self.local_search_failure_snippets = tuple(local_search_failure_snippets or ())
        self.local_search_exhausted_reason = str(local_search_exhausted_reason)

    def extract_failure_reason(self, info):
        if not isinstance(info, dict):
            return "Unknown", ""

        explicit_failure_reason = info.get("failure_reason")
        if explicit_failure_reason:
            text = str(explicit_failure_reason).strip()
            if text:
                reason = text.splitlines()[0][:160]
                return self.canonicalize_failure_reason(reason, text[:300], info)

        for key in ("error", "reset_error"):
            value = info.get(key)
            if value:
                text = str(value).strip()
                if text:
                    reason = text.splitlines()[0][:160]
                    return self.canonicalize_failure_reason(reason, text[:300], info)

        critic_result = info.get("critic_result") if isinstance(info.get("critic_result"), dict) else None
        if isinstance(critic_result, dict) and not bool(critic_result.get("success")):
            reason_code = str(critic_result.get("reason_code") or "").strip()
            critique_text = str(critic_result.get("critique") or "").strip()
            if reason_code:
                return self.canonicalize_failure_reason(reason_code, critique_text[:300], info)

        completion_reason = str(info.get("completion_reason") or "").strip()
        critique = str(info.get("critique") or "").strip()
        if completion_reason and completion_reason not in {"critic_success", "world_effect_verified", "retrying", "preflight_success"} and critique:
            critique_head = critique.splitlines()[0][:160]
            if self.looks_like_non_runtime_failure_instruction(critique_head, critique) or self.looks_like_prompt_or_conversation_dump(critique_head, critique):
                return self.canonicalize_failure_reason(completion_reason, critique[:300], info)

        if critique:
            reason = critique.splitlines()[0][:160]
            return self.canonicalize_failure_reason(reason, critique[:300], info)

        return self.canonicalize_failure_reason("Unknown", "", info)

    def canonicalize_failure_reason(self, reason, evidence, info=None):
        reason_text = str(reason or "Unknown").strip() or "Unknown"
        evidence_text = str(evidence or "").strip()
        combined = f"{reason_text}\n{evidence_text}".lower()
        completion_reason = ""
        if isinstance(info, dict):
            completion_reason = str(info.get("completion_reason") or "").strip()
            error_type = str(info.get("error_type") or "").strip()
            if error_type and any(
                token in combined
                for token in (
                    "codex/action",
                    "500 server error",
                    "http://127.0.0.1:8787",
                    "codex gateway",
                    "action llm",
                )
            ):
                return self.action_generation_failure_reason, evidence_text or reason_text
        if completion_reason == self.action_generation_failure_reason:
            return self.action_generation_failure_reason, evidence_text or reason_text
        if any(
            token in combined
            for token in (
                "500 server error",
                "codex/action",
                "http://127.0.0.1:8787",
                "codex gateway failed",
                "action llm",
            )
        ):
            return self.action_generation_failure_reason, evidence_text or reason_text
        if completion_reason == self.reset_only_loop_failure_reason:
            return self.reset_only_loop_failure_reason, evidence_text or reason_text
        if reason_text in self.search_failure_reasons:
            return reason_text, evidence_text or reason_text
        if completion_reason in self.search_failure_reasons:
            return completion_reason, evidence_text or reason_text
        if any(snippet in combined for snippet in self.local_search_failure_snippets):
            return self.local_search_exhausted_reason, evidence_text or reason_text
        if reason_text == "Unknown" and completion_reason:
            return completion_reason, evidence_text
        if self.looks_like_non_runtime_failure_instruction(reason_text, evidence_text):
            fallback_reason = completion_reason or "action_or_critic_output_not_normalized"
            return fallback_reason, evidence_text[:300] or reason_text[:300]
        if self.looks_like_prompt_or_conversation_dump(reason_text, evidence_text):
            fallback_reason = completion_reason or "action_or_critic_output_not_normalized"
            return fallback_reason, evidence_text[:300]
        return reason_text, evidence_text

    def looks_like_non_runtime_failure_instruction(self, reason_text, evidence_text):
        combined = f"{reason_text}\n{evidence_text}".strip().lower()
        if not combined:
            return False
        instruction_starts = (
            "to complete the task",
            "you need to ",
            "if you want to be sure",
            "get at least ",
            "ensure you have ",
            "smelt the remaining ",
        )
        if any(combined.startswith(prefix) for prefix in instruction_starts):
            return True
        if len(reason_text) >= 120 and any(token in combined for token in ("you have ", "after ", "then ", "verify that ")):
            return True
        return False

    def looks_like_prompt_or_conversation_dump(self, reason_text, evidence_text):
        combined = f"{reason_text}\n{evidence_text}".strip().lower()
        if not combined:
            return False
        suspicious_snippets = (
            "you are a helpful assistant that writes mineflayer javascript code",
            "here are some useful programs written with mineflayer apis",
            "completed tasks so far:",
            "failed tasks that are too hard:",
        )
        if any(snippet in combined for snippet in suspicious_snippets):
            return True
        if combined.startswith("(") and "mineflayer javascript code" in combined:
            return True
        if len(reason_text) >= 140 and any(token in reason_text.lower() for token in ("you are ", "inventory", "nearby blocks", "task:")):
            return True
        return False
