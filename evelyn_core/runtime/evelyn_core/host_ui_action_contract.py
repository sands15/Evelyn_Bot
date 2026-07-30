from __future__ import annotations

import re


HOST_UI_ACTION_REQUEST_SCHEMA = "host_ui_action.request.v2"
HOST_UI_ACTION_RESPONSE_SCHEMA = "host_ui_action.response.v2"
HOST_UI_ACTION_STATUS_SCHEMA = "host_ui_action.status.v1"
HOST_UI_ACTION_REQUEST_KEYS = frozenset(
    {
        "schema",
        "requestId",
        "createdAt",
        "expiresAt",
        "operation",
        "action",
        "elementId",
        "postcondition",
        "confirmToken",
    }
)
HOST_UI_ACTION_RESPONSE_KEYS = frozenset(
    {
        "schema",
        "requestId",
        "createdAt",
        "expiresAt",
        "ok",
        "operation",
        "errorCode",
        "targets",
        "preview",
        "result",
    }
)
HOST_UI_ACTION_REQUEST_ID_RE = re.compile(r"^[0-9a-f]{32}$")
HOST_UI_ACTION_ELEMENT_ID_RE = re.compile(r"^[0-9a-f]{20}$")
HOST_UI_ACTION_CONFIRM_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
HOST_UI_ACTION_MAX_REQUEST_BYTES = 8192
HOST_UI_ACTION_MAX_RESPONSE_BYTES = 32768
HOST_UI_ACTION_REQUEST_TTL_SEC = 15.0
HOST_UI_ACTION_RESPONSE_TTL_SEC = 30.0
HOST_UI_ACTION_MAX_RESPONSE_AGE_SEC = 10.0


__all__ = [
    "HOST_UI_ACTION_CONFIRM_TOKEN_RE",
    "HOST_UI_ACTION_ELEMENT_ID_RE",
    "HOST_UI_ACTION_MAX_REQUEST_BYTES",
    "HOST_UI_ACTION_MAX_RESPONSE_AGE_SEC",
    "HOST_UI_ACTION_MAX_RESPONSE_BYTES",
    "HOST_UI_ACTION_REQUEST_ID_RE",
    "HOST_UI_ACTION_REQUEST_KEYS",
    "HOST_UI_ACTION_REQUEST_SCHEMA",
    "HOST_UI_ACTION_REQUEST_TTL_SEC",
    "HOST_UI_ACTION_RESPONSE_KEYS",
    "HOST_UI_ACTION_RESPONSE_SCHEMA",
    "HOST_UI_ACTION_RESPONSE_TTL_SEC",
    "HOST_UI_ACTION_STATUS_SCHEMA",
]
