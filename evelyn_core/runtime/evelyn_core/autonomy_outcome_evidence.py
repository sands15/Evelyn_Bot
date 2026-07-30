from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping

from .autonomy_authorization import MINECRAFT_AUTONOMY_ACTIONS


AUTONOMY_OUTCOME_EVIDENCE_POLICY_SCHEMA = (
    "autonomy.outcome-evidence-policy.v1"
)
AUTONOMY_SUCCESS_STATUSES = frozenset({"ok", "done", "completed"})

_action_evidence_codes: dict[str, frozenset[str]] = {
    "assistant:check_status": frozenset({"status_snapshot_built"}),
    "assistant:refresh_cognitive_state": frozenset(
        {"cognitive_state_updated"}
    ),
    "assistant:summarize_notifications": frozenset(
        {"summary_payload_built"}
    ),
    "assistant:summarize_recent_context": frozenset(
        {"recent_context_payload_built"}
    ),
    "assistant:send_followup": frozenset({"discord_send_completed"}),
    "assistant:maybe_ping_user": frozenset(
        {
            "discord_send_completed",
            "proactive_gate_completed",
        }
    ),
    "assistant:idle": frozenset({"no_side_effect_required"}),
}
for _minecraft_action in MINECRAFT_AUTONOMY_ACTIONS:
    _action_evidence_codes[_minecraft_action] = frozenset(
        {
            f"{_minecraft_action.replace(':', '_')}_completed",
        }
    )

_minecraft_skip_evidence = {
    "minecraft:equip_shield": "inventory_absence_verified",
    "minecraft:avoid_hazard": "hazard_absence_verified",
    "minecraft:retreat": "hostile_absence_verified",
    "minecraft:melee_attack": "target_absence_verified",
    "minecraft:eat_if_low": "food_absence_verified",
    "minecraft:consume_food": "food_absence_verified",
    "minecraft:heal_or_regroup": "food_absence_verified",
}
for _minecraft_action, _evidence_code in _minecraft_skip_evidence.items():
    _action_evidence_codes[_minecraft_action] = frozenset(
        {
            *_action_evidence_codes[_minecraft_action],
            _evidence_code,
        }
    )

AUTONOMY_ACTION_EVIDENCE_CODES: Mapping[str, frozenset[str]] = (
    MappingProxyType(_action_evidence_codes)
)


def expected_autonomy_evidence_codes(action: Any) -> frozenset[str]:
    return AUTONOMY_ACTION_EVIDENCE_CODES.get(
        str(action or "").strip(),
        frozenset(),
    )


def autonomy_outcome_verified(
    action: Any,
    result: Any,
) -> bool:
    if not isinstance(result, dict):
        return False
    status = str(result.get("status") or "").strip().lower()
    evidence_code = str(result.get("evidence_code") or "").strip()
    return bool(
        status in AUTONOMY_SUCCESS_STATUSES
        and result.get("verified") is True
        and evidence_code
        in expected_autonomy_evidence_codes(action)
    )


__all__ = [
    "AUTONOMY_ACTION_EVIDENCE_CODES",
    "AUTONOMY_OUTCOME_EVIDENCE_POLICY_SCHEMA",
    "AUTONOMY_SUCCESS_STATUSES",
    "autonomy_outcome_verified",
    "expected_autonomy_evidence_codes",
]
