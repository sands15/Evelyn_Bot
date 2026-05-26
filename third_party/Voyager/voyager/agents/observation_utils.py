from __future__ import annotations


def observe_payload(events):
    if not events:
        return {}
    try:
        event_type, payload = events[-1]
    except Exception:
        return {}
    if event_type != "observe" or not isinstance(payload, dict):
        return {}
    return payload


def payload_status(payload):
    status = payload.get("status") if isinstance(payload, dict) else None
    return status if isinstance(status, dict) else {}


def payload_inventory(payload):
    inventory = payload.get("inventory") if isinstance(payload, dict) else None
    return inventory if isinstance(inventory, dict) else {}


def payload_list(payload, key):
    value = payload.get(key) if isinstance(payload, dict) else None
    return value if isinstance(value, list) else []


def payload_dict(payload, key):
    value = payload.get(key) if isinstance(payload, dict) else None
    return value if isinstance(value, dict) else {}


def safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default
