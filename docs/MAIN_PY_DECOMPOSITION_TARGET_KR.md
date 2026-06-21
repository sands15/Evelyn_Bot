# main.py 분리 목표

Last reviewed: 2026-06-20

## 목표

`main.py`는 이블린의 전체 구현 파일이 아니라 런타임 엔트리포인트와 배선 파일이어야 한다.
최종 목표는 `main.py`를 대략 1,500~2,500줄 이하로 줄이고, 기능별 규칙과 상태를 모듈로 이동하는 것이다.

## 최종 책임 경계

- `main.py`
  - 환경 경로/설정 초기화
  - Discord bot 생성과 이벤트/명령 decorator 등록
  - 주요 모듈 의존성 wiring
  - local-only 또는 Discord runtime 진입

- `runtime_lifecycle.py`
  - 재시작, 종료, 런처 선택, PowerShell stop helper 호출

- `control_page_runtime_health.py`
  - 8799 Control Page와 8798 Bot API 계약 해석
  - `runtime.serviceHealth` 요약과 Control Page용 진단 payload

- `evelyn_core.runtime.evelyn_core.control_page_http`
  - Control Page JSON 응답, no-store 파일/바이너리 응답, CORS, asset path guard, health payload.

- `evelyn_core.runtime.evelyn_core.control_page.*`
  - aiohttp route, Control Page state payload, command catalog, tool registry, UI contracts

- `evelyn_core.runtime.evelyn_core.voice.*`
  - 음성 파이프라인 상태, 바리인 연속성, session/room ownership, ingress, reply flow, debug audio

- `evelyn_core.runtime.evelyn_core.observability.*`
  - turn trace, metric 집계, p95, log event formatting

- `evelyn_core.runtime.evelyn_core.memory.*`
  - session key/history/follow-up target, memory vault bridge

- `evelyn_core.runtime.evelyn_core.minecraft.*`
  - Control Page용 Minecraft/Voyager snapshot cache, autonomy command handling

## 분리 원칙

- `main.py`에는 오래 사는 상태 dict를 두지 않는다.
- `main.py`에는 비즈니스 규칙과 판정 로직을 두지 않는다.
- Discord decorator/event handler는 남겨도 내부는 모듈 함수 호출로 얇게 유지한다.
- Control Page, Voice, Runtime, Minecraft/Voyager는 명확한 payload/contract로 연결한다.
- 기존 문자열 기반 `main.py` 테스트는 점진적으로 모듈 단위 테스트로 옮긴다.
- 한 번에 대규모 이동하지 않고, 작고 검증 가능한 단위로 이동한다.

## 구현 순서

1. `voice_barge_in_continuity.py`
   - 5회 연속 바리인 상태, reason 분류, summary/detail formatting, reset, snapshot, event logging.
2. `control_page.commands` / `control_page.tools`
   - Control Page command catalog, cheap command routing, tool registry, high-risk command guard.
3. `control_page.state`
   - `/api/control-page/state` payload 조립과 boot/status summary.
4. `voice.pipeline_state` / `voice.debug_audio`
   - `voicePipeline` snapshot, counters, debug audio write/trim worker.
5. `memory.session_memory`
   - session key, conversation history, follow-up target, room/person memory key.
6. Discord command handlers thinning
   - command decorator는 유지하되 내부 구현은 모듈 API로 이동.

## 1차 구현 범위

현재 1차 범위는 바리인 연속성 분리다.

- 새 모듈: `evelyn_core/runtime/evelyn_core/voice_barge_in_continuity.py`
- `main.py`에 남는 것:
  - tracker 생성
  - 기존 함수명 호환 wrapper
  - 실제 voice/Control Page 호출부
- 검증:
  - 새 단위 테스트
  - `py_compile`
  - `tests/voice`
  - 관련 `tests/runtime`, `tests/ui`, `tests/core`

## 2026-06-19 진행 상태

완료된 분리:

- `voice_barge_in_continuity.py`
  - 바리인 연속성 tracker, reason/label, snapshot, Control Page 표시 payload.
- `control_page_tools.py`
  - Control Page command catalog, cheap/router tool decision, restart/status 질문 guard, UI action mapping, tool risk policy.
- `control_page_state.py`
  - Control Page local/guild state payload/view builder, runtime/voice/minecraft/boot payload builder, local/guild status text formatter, route query/body parser, chat refresh plan, memory vault open payload/reply helper, Control Page command/tool execution reply formatter, chat log/UI command store, Minecraft snapshot cache store, runtime services cache/payload store, welcome text sanitizer.
  - Control Page chat/shutdown/memory-note route orchestration helpers, memory vault open fallback helpers, Discord-required reply, and shutdown tool fallback policy with live dependencies injected from `main.py`.
- `control_page_state_handler.py`
  - Control Page `/api/control-page/state` live dependency collection and local/guild state orchestration, with live runtime callbacks/config injected from `main.py`.
- `voice_pipeline_state.py`
  - voice pipeline counters, last channel state file, failure state, Control Page voice pipeline snapshot.
- `voice_debug_audio.py`
  - debug wav 저장, stem 관리, 오래된 파일 정리.
- `local_mic_state.py`
  - local mic runtime state, input mode normalization, 상태 문자열.
- `observability_metrics.py`
  - 평균/p95/rate helper, turn-path metric summary, voice p95 summary, model-call metric replay/summary, question metric summary.
- `question_policy_state.py`
  - 질문 정책 정규화, fast-path 질문 제한, session question cooldown/state, proactive question selection wrapper.
- `route_fallback_policy.py`
  - LLM route fallback 이름 정규화, voice context 강제 조건, main/sub_hint/sub_wait fallback route 판정.
- `tool_awareness_policy.py`
  - Main response guidance용 tool awareness context, marker 기반 search/runtime/minecraft tool shortlist, route availability callback contract.
- `local_tool_diagnostic_context.py`
  - local tool diagnostic 요청 감지, 후보 파일 경로, 진단 snippet line matching/rendering.
- `memory_context_state.py`
  - layered memory context row merge, memory row line formatting, memory-vault recall을 포함한 최종 memory context 조립/rendering.
- `memory_layers.py`
  - guild/room/person/session memory layer I/O 수집.
- `memory_llm_context.py`
  - cognitive/long-term memory LLM prompt 조립, compact retry prompt, layered summary, scope target 계산.
- `memory_update_policy.py`
  - memory summary/writebehind 실행 여부 정책, vision context memory redaction, raw turn row/scope label 조립, raw transcript/vault mirror 기록, memory writer decision payload 조립, writebehind scheduling plan.
- `memory_writeback_state.py`
  - long-term memory LLM writebehind orchestration, context overflow compact retry, summary/facts/questions writeback, vault mirror, proactive question promotion.
- `cognitive_policy_state.py`
  - fast-path cognitive state 생성, ask confidence gating, cognitive action short-circuit response, layered/cached cognitive state lookup, fallback/final state 보정.
- `autonomy_observation_state.py`
  - 자율 엔진 기본 observation payload 계산, 최근 user text 선택, cognitive refresh 필요성/vision/local mic 활동 판정, autonomy executor summary/status/recent-context payload.
- `response_output_policy.py`
  - response action tag parsing, friend-style output normalization, model output sanitize, reasoning answer extraction/meta-line filtering, display cleanup, Minecraft leak suppression, simple local chat fast-path.
- `search_followup_policy.py`
  - search promise 감지, search answer source stripping, 짧은 search/weather follow-up query classifier.
- `search_query_context.py`
  - 최근 user search context 후보 수집, generic search follow-up 해소, 날씨 위치 문맥 보강, 짧은 query memory fallback.
- `session_memory_state.py`
  - session key, follow-up target, turn/segment id, user text turn start, assistant/command/tool text turn finish, active session snapshot, conversation history, router history rendering, persona status hint.
- `room_speaker_activity.py`
  - voice room 최근 발화자 pruning, activity decay, active speaker 선택.
- `turn_trace.py`
  - turn summary payload에 더해 JSONL trace writer와 console fallback까지 포함.
- `turn_lifecycle.py`
  - `TurnScope`에 더해 room turn scope registry, stale turn cancel count, scoped task attach/detach/create/clear helper.
- `discord_ingress.py`
  - text/voice ingress key builder, Discord message/thread context builder, reply-target check, attachment context builder, text message precheck/turn decision.
- `discord_text_turn.py`
  - Discord `on_message` text-turn orchestration, command-only/prefix precheck handoff, wake/reply/active-session gate, reply slot locking, text reply streaming, optional voice delivery, memory/search follow-up scheduling, assistant turn finalization, and text-turn summary/error logging with live dependencies injected from `main.py`.
- `discord_command_handlers.py`
  - Discord command decorator bodies for voice join/rejoin/leave, restart/shutdown/status/page/prefix, autonomy, Minecraft, channel settings, and guild reset. `main.py` keeps decorators and injects live callbacks/config.
- `discord_commands.py`
  - Discord command 권한/상태/접두사/채널 목록/도움말/자율상태/마인크래프트 명령/길드 초기화 응답 formatter.
- `discord_settings.py`
  - Discord command prefix와 observe/command-only channel 설정 I/O, channel id normalization, prefix validation/cache write-through.
- `search_tools.py`
  - DuckDuckGo/API/HTML search, weather query normalization, wttr weather result, search result rendering.
- `runtime_status_context.py`
  - 런타임 상태 문맥용 URL port 추출, TCP probe, 로그 tail/오류 compact, 최근 런타임 오류 수집, GPU VRAM/OOM 상태 답변.
- `control_page_runtime_probe.py`
  - Control Page runtime services TCP/HTTP probe orchestration, Bot API state probing, Codex gateway health 판정.
- `control_page_http.py`
  - Control Page JSON 응답, no-store cache header, CORS middleware, asset path guard, Bot API health payload.
- `minecraft_runtime_snapshot.py`
  - Minecraft runtime snapshot freshness/status fields, Voyager status/observation merge, inventory/position normalization, Control Page recent activity extraction, Main LLM용 Minecraft 상태 요약.
- `minecraft_assets.py`
  - Minecraft jar asset bytes/json reader, item/model texture path normalization, model parent/alias texture resolution, Control Page item icon jar discovery/cache loader.
- `voice_route_execution.py`
  - voice route action 실행, skill dispatch/follow-up execution, Minecraft/vision/runtime/local-tool routing callbacks.
- `llm_context_assembly.py`
  - Main LLM messages/context assembly, runtime/memory/cognitive/local-tool/Minecraft/tool-awareness context 조립.
- `main_llm_runtime.py`
  - Main LLM one-shot call, tool result synthesis, promised-search escalation and synthesis answer drift guard.
- `voice_response_runtime.py`
  - first/follow-up response split, low-latency first response LLM call, follow-up response LLM call, duplicate follow-up suppression.
- `voice_stream_chunks.py`
  - streaming speech chunker construction, streamed delta/flush question filtering, delivery-plan TTS chunk emission.
- `cognitive_state_runtime.py`
  - cognitive state refresh runtime orchestration, layered scope writeback, background task cleanup.
- `memory_update_runtime.py`
  - memory writer decision 기록과 writebehind scheduling runtime orchestration.
- `search_followup_runtime.py`
  - promised/proactive search follow-up scheduling, singleflight cancellation, cognitive completion state writeback, Discord/TTS delivery, memory update scheduling.

현재 `main.py`에 남은 주요 다음 후보:

- voice answer payload assembly와 delivery planning side-effect wiring
  - answer payload 자체의 primitive는 모듈화되어 있지만, voice path별 delivery orchestration 일부는 아직 `main.py`에 있음.
- memory vault bridge와 cognitive update wiring
- autonomy executor의 남은 side-effect wiring
- remaining voice pipeline side-effect wiring
  - STT/route/LLM/TTS 연결부와 speaker/session state mutation 일부가 아직 `main.py`에 있음.
