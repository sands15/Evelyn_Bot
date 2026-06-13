# Tool Awareness Judgment Blueprint

Temporary implementation blueprint for the tool-awareness judgment layer.

## Problem

Evelyn currently has several LLM/runtime layers:

- router LLM
- main LLM
- skill executor
- delivery/follow-up paths

Those layers can disagree about what tools exist and which phase the current reply is in. The most visible failure is:

1. the main LLM says a preface such as "I'll look it up",
2. the reply is treated as a final answer,
3. the promised tool result is skipped or delivered through a weak background path.

## Design

Add a lightweight tool-awareness judgment layer before main LLM generation.

The layer must:

1. render a short, runtime-derived list of tools available to the current source;
2. include concrete usage rules for search/weather/current-info requests;
3. tell the main LLM that it must not claim to use tools as a final answer;
4. keep the actual executor as the source of truth, not the model's memory;
5. expose metadata for tests and debugging.

## Tool Selection Rules

For now, expose only compact high-value tools:

- `search`: current info, weather, prices, news, web lookup, and explicit "find/search/check" requests.
- `minecraft.status`: Minecraft/Voyager status or inventory-related requests when the route exists.
- `runtime.status`: Evelyn runtime, voice, model, and service status requests when the route exists.

Do not dump every registered skill into the prompt. The main LLM should receive a small shortlist.

## Main LLM Contract

When a user request needs a tool:

- do not answer with only "I'll check";
- do not pretend the tool has already run;
- either route the turn to the tool executor or produce a short preface that the runtime can escalate;
- after a tool result exists, give a final answer grounded in that result.

## Promise Escalation

If the main LLM still outputs a search promise such as "I'll search/check/look it up":

1. treat that answer as `tool_request: search`;
2. run the search executor using the original user request and recent context;
3. synthesize the tool result through the main LLM final-answer phase;
4. return that final answer instead of the promise text.

This must work even when realtime mode would normally skip background search follow-up.

## Implementation Points

1. Add `build_tool_awareness_context(...)` in `main.py`.
2. Append its output to `build_main_response_guidance(...)` so every main LLM turn sees the same contract.
3. Add `resolve_promised_search_final_answer(...)` near the search/follow-up helpers.
4. Call the resolver after `ask_llm_once`, `ask_llm_streaming`, `stream_text_reply`, and control-page main replies before finalizing history/session state.
5. Keep existing direct `search_then_answer` route behavior, but make fallback promise escalation equally reliable.

## Tests

Add focused tests that verify:

1. tool-awareness context lists search and forbids final promise-only answers;
2. realtime mode does not skip promise-based search scheduling;
3. promise escalation returns synthesized search output instead of the original promise.

