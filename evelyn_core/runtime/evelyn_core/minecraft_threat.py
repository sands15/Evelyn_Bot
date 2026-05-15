from __future__ import annotations

from typing import Any

EMERGENCY_THREAT_SCORE = 85.0
NEAR_INTERRUPT_THREAT_SCORE = 65.0
PREPARE_THREAT_SCORE = 50.0
TRACK_THREAT_SCORE = 25.0
NEAR_INTERRUPT_DISTANCE = 8.0


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except Exception:
        return default
    return number if number == number else default


def threat_assessment(obs: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(obs, dict):
        return {}
    assessment = obs.get("threat_assessment")
    return assessment if isinstance(assessment, dict) else {}


def hostile_threats(obs: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(obs, dict):
        return []
    direct = obs.get("hostile_threats")
    if isinstance(direct, list):
        return [item for item in direct if isinstance(item, dict)]
    assessment = threat_assessment(obs)
    nested = assessment.get("hostiles")
    if isinstance(nested, list):
        return [item for item in nested if isinstance(item, dict)]
    nearest = obs.get("nearest_hostile")
    if isinstance(nearest, dict) and "threat_score" in nearest:
        return [nearest]
    return []


def highest_threat(obs: dict[str, Any] | None) -> dict[str, Any] | None:
    assessment = threat_assessment(obs)
    nested = assessment.get("highest_threat")
    if isinstance(nested, dict):
        return nested
    threats = hostile_threats(obs)
    if threats:
        return max(threats, key=lambda item: (_as_float(item.get("threat_score")), -_as_float(item.get("distance"), 999.0)))
    nearest = obs.get("nearest_hostile") if isinstance(obs, dict) else None
    return nearest if isinstance(nearest, dict) else None


def highest_threat_score(obs: dict[str, Any] | None) -> float:
    if not isinstance(obs, dict):
        return 0.0
    if "highest_threat_score" in obs:
        return _as_float(obs.get("highest_threat_score"), 0.0)
    assessment = threat_assessment(obs)
    if "highest_threat_score" in assessment:
        return _as_float(assessment.get("highest_threat_score"), 0.0)
    threat = highest_threat(obs)
    return _as_float(threat.get("threat_score"), 0.0) if isinstance(threat, dict) else 0.0


def threat_count(obs: dict[str, Any] | None, *, min_score: float = PREPARE_THREAT_SCORE) -> int:
    assessment = threat_assessment(obs)
    if min_score == PREPARE_THREAT_SCORE and "threat_hostiles_nearby" in assessment:
        return int(_as_float(assessment.get("threat_hostiles_nearby"), 0.0))
    return sum(1 for item in hostile_threats(obs) if _as_float(item.get("threat_score"), 0.0) >= min_score)


def has_interrupting_threat(obs: dict[str, Any] | None) -> bool:
    score = highest_threat_score(obs)
    threat = highest_threat(obs)
    distance = _as_float(threat.get("distance"), 999.0) if isinstance(threat, dict) else 999.0
    return score >= EMERGENCY_THREAT_SCORE or (score >= NEAR_INTERRUPT_THREAT_SCORE and distance <= NEAR_INTERRUPT_DISTANCE)


def has_survival_threat(obs: dict[str, Any] | None) -> bool:
    return has_interrupting_threat(obs) or highest_threat_score(obs) >= PREPARE_THREAT_SCORE


def threat_distance(obs: dict[str, Any] | None, default: float = 999.0) -> float:
    threat = highest_threat(obs)
    return _as_float(threat.get("distance"), default) if isinstance(threat, dict) else default
