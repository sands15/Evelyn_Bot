# Routing Helper Structure

## Role
- Shared route shaping and LLM-response parsing helpers.
- This package is **not** a registered skill.
- It exists as a helper/service package imported directly by `main.py`.

## Current Files
- `__init__.py`
  - Re-exports helper functions from `voice_llm.py`.
  - Does not register with `skill_registry`.
- `voice_llm.py`
  - Houses shared parsing/building helpers extracted from `main.py`.

## Core Functions in `voice_llm.py`
### `build_main_llm_payload(...)`
Builds the normalized request payload for the main LLM.

### `extract_main_llm_answer_from_choice(...)`
Pulls the final answer from a single choice object.

### `decode_sse_stream_line(...)`
Parses one SSE line from the streaming response.

### `build_route_decision_from_state(...)`
Converts cognitive state + policy response into a `RouteDecision`.
Handles special routing like `search_then_answer -> search_executor` and non-default custom actions.

### `should_await_user_reply_for_route(...)`
Computes whether a route/action should leave the session waiting for the user.

## Notes
- This package is intentionally helper-shaped, not skill-shaped.
- The actual executable skills live in `conversation`, `search`, `delivery`, `minecraft`, or future domain packages.
