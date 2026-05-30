# Evelyn Dialogue UX and Fast Path Plan

Date: 2026-05-28

This document fixes the next architecture direction for Evelyn's live conversation loop. TTS generation and streaming internals are intentionally out of scope because the current TTS path is working well enough for live use. TTS should be changed only after a concrete real-use failure is reported.

## 1. Conversation UX Goal

Evelyn should not be optimized as a fast command assistant first. The target is a character that feels naturally present with the user.

Primary UX target:

> Evelyn is a naturally present companion character, not a fast service assistant.

This changes the optimization criteria. Latency matters, but the first priority is preserving presence, naturalness, and continuity.

Good behavior:

- Responds naturally when called.
- Does not repeat the same fixed phrase too often.
- Avoids assistant/service tone.
- Keeps casual Korean speech style unless explicitly asked otherwise.
- Gives short responses for light turns.
- Uses questions only when a question is actually needed.
- Keeps Minecraft/task status consistent with the live runtime state.
- Keeps visible text and spoken text cleanly separated.

Bad behavior:

- "네, 부르셨어요" style service responses.
- Long explanations for a simple call.
- Repeating the same wake response every time.
- Switching into polite assistant language.
- Asking unnecessary confirmation questions.
- Mixing stale Minecraft/runtime state into casual replies.

The working goal is not simply "faster Evelyn". It is "Evelyn that remains believable while responding quickly."

## 2. Turn-Type Engine Separation

Not every user turn should go through the main LLM. Light turns should use a fast path. The main LLM should be reserved for turns that actually require open-ended reasoning or natural generation.

Target flow:

```text
STT result
  -> quality gate
  -> turn classifier
     -> instant reply path
     -> cached audio fast path
     -> runtime status path
     -> Minecraft action/planning path
     -> main conversation LLM path
     -> search/long-answer path
     -> repair/clarification path
```

Initial turn classes:

- `wake_call`: "이블린", "야", "뭐해" when used as a call.
- `casual_check`: "있어?", "듣고 있어?", "괜찮아?"
- `short_confirm`: "응", "아니", "그래", "해줘"
- `runtime_status`: "지금 뭐 하고 있어?", "마크 상태 어때?"
- `minecraft_command`: "나무 캐", "철 찾자", "횃불 만들어"
- `conversation`: normal chat, feeling, preference, discussion.
- `knowledge_or_search`: long explanation or external information.
- `repair`: misrecognition, failed action, unclear intent.

Engine assignment:

- `wake_call`: fast path first.
- `casual_check`: fast path or tiny router-generated response.
- `short_confirm`: local dialogue state resolver.
- `runtime_status`: live state formatter, optionally with short generation.
- `minecraft_command`: Minecraft planner/action path.
- `conversation`: main LLM.
- `knowledge_or_search`: search/context path plus main LLM.
- `repair`: clarification path.

### Cached Audio Fast Path

For highly predictable wake/call turns, text generation and TTS generation can both be bypassed.

Example:

- User says: "이블린"
- Expected fixed response: "응, 왜 불렀어?"

Instead of:

```text
STT -> router -> main LLM -> TTS -> playback
```

use:

```text
STT -> wake_call classifier -> pre-rendered audio file -> playback
```

This is more efficient because the response is semantically fixed and does not require fresh model work.

Recommended cached assets:

- `wake_call_default`: "응, 왜 불렀어?"
- `wake_call_soft`: "응, 듣고 있어."
- `wake_call_busy`: "응, 잠깐만. 지금 처리 중이야."
- `casual_check_ready`: "응, 여기 있어."

Important constraints:

- Do not change the TTS engine to implement this.
- Generate/cache the audio assets separately.
- Playback should choose from cached assets only when the turn is confidently classified.
- Avoid one single response forever. Keep a small set of pre-rendered variants to reduce repetition.
- If context matters, fall back to runtime status or main LLM instead of using a cached phrase.

Cached audio should be treated as a playback optimization layer, not as a TTS rewrite.

Current implementation:

- `main.py` keeps the existing wake-only canned reply path.
- `EVELYN_CANNED_WAKE_REPLY_TEXT` defaults to `응, 왜 불렀어?`.
- `EVELYN_CANNED_WAKE_REPLY_AUDIO` defaults to `assets/audio_cache/wake_call_default.wav`.
- `speak_answer()` checks for that exact canned answer before creating a fresh TTS stream.
- If the wav file exists, Evelyn plays it through `CachedWaveAudioSource`.
- If the wav file is missing, Evelyn silently falls back to the existing TTS generation path.
- `EVELYN_CACHED_AUDIO_ENABLED=false` disables the cached audio layer.

## 3. Measurable Evaluation Loop

Conversation changes should be measured. "It feels faster" is not enough for the next stage.

Each live turn should record timing fields:

- `turn_id`
- `session_key`
- `audio_start`
- `stt_final_time`
- `classifier_start`
- `classifier_end`
- `router_start`
- `router_end`
- `main_request_start`
- `main_first_token`
- `main_done`
- `tts_request_start`
- `tts_first_pcm`
- `playback_start`
- `playback_done`
- `selected_path`
- `turn_type`
- `reply_source`

Example report:

```text
turn_type=wake_call
selected_path=cached_audio_fast_path
STT final: 420 ms
classifier: 8 ms
audio load: 4 ms
playback start: 455 ms
total perceived delay: 455 ms
```

The metrics should make it clear where delay is coming from:

- STT delay
- classifier/router delay
- main LLM first-token delay
- TTS first-PCM delay
- playback queue delay

### UX Regression Set

Maintain a small fixed sample set for style checks. These are not only latency tests. They are character and behavior tests.

Initial samples:

- "이블린"
- "이블린 지금 뭐 해?"
- "야 뭐해"
- "듣고 있어?"
- "아까 뭐 하고 있었지?"
- "나무 캐자"
- "왜 멈췄어?"
- "그거 말고 철 찾자"
- "괜찮아?"
- "좀 자연스럽게 말해봐"
- "지금 마크에서 뭐 하고 있어?"
- "아니 그건 하지마"

Check each sample for:

- Polite/service tone leak.
- Repeated fixed phrase.
- Overlong answer.
- Unnecessary question.
- Wrong `[질문]` handling.
- Wrong OmniVoice tag handling.
- Minecraft/runtime state contradiction.
- Incorrect use of cached audio when context required a real answer.

The target is a small automated or semi-automated report that can be run after dialogue changes.

## 5. Memory and Persona Context Structure

Prompt context should be layered. Do not put every rule and state blob into every prompt.

Storage, indexing, cache invalidation, and vault organization details belong in
`docs/EVELYN_MEMORY_VAULT_ARCHITECTURE.md`. This section only defines which
memory/persona layers should enter dialogue prompts.

### Stable System Layer

This should change rarely and sit at the cache-friendly front of the prompt:

- Evelyn identity.
- Casual Korean speech style.
- Forbidden assistant/service phrases.
- Output format rules.
- OmniVoice tag rules.
- Visible text vs TTS text policy.

### Turn-Local Dynamic Layer

This changes often but should stay short:

- Current turn type.
- User's exact utterance.
- Whether the user is calling Evelyn, asking status, or giving a task.
- Recent assistant reply summary to avoid repetition.
- Current conversational mood or relationship hint.

### Runtime State Layer

Include only when needed:

- Voice session state.
- Active room owner.
- Minecraft current task.
- Minecraft inventory/location/status.
- Error/recovery status.
- TTS queue status if relevant.

### Retrieved Memory Layer

Include only when relevant:

- Long-term user preferences.
- Project decisions.
- Recent task history.
- Minecraft strategy preferences.
- Prior unresolved issues.

Target prompt composition:

```text
stable system prompt
+ short turn style hint
+ relevant runtime state only
+ relevant retrieved memory only
+ user message
```

This supports both latency and character consistency:

- Stable prompt prefix improves prompt-cache reuse.
- Dynamic context stays short.
- Irrelevant state does not confuse the response.
- Persona is consistent without stuffing every memory into the prompt.

## Implementation Order

Recommended next implementation sequence:

1. Add explicit turn classifier labels and logging.
2. Implement `wake_call` fast path.
3. Add pre-rendered cached audio playback for high-confidence wake calls.
4. Add latency trace aggregation by turn type and selected path.
5. Add the small UX regression set.
6. Split main prompt assembly into stable, dynamic, runtime, and retrieved layers.

Do not modify TTS generation or streaming internals in this phase.

Current code progress:

- `evelyn_core/runtime/evelyn_core/voice_pipeline.py` now classifies voice turns into `wake_call`, `casual_check`, `short_confirm`, `runtime_status`, `minecraft_command`, `conversation`, `knowledge_or_search`, or `repair`.
- `VoiceReplyRequest` now carries `turn_type`, `selected_path`, and `reply_source`.
- `voice_orchestration.py` writes those fields into `metrics.meta` after the reply gate.
- `main.py` includes `turn_type`, `selected_path`, and `reply_source` in voice bottleneck logs and JSON turn events.
- `main.py` aggregates per-path timing samples in `turn_path_metrics` and exposes the summary through the voice pipeline snapshot as `turnPathMetrics`.
- `build_main_response_guidance()` includes the classified `turn_type` in the short dynamic guidance layer.
- `tests/test_dialogue_turn_classifier.py` stores the first dialogue UX regression sample set for classifier behavior.
