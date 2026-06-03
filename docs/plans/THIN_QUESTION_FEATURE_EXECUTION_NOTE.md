# Evelyn Thin Question Feature Execution Note

작성 기준: 2026-06-02

## 목적

질문 기능을 별도 상시 LLM이나 대형 정책 객체로 만들지 않고, 기존 응답 hot path에 얇게 붙인다.

## 1차 적용 범위

- `RouteDecision`에 질문 제어 필드 추가
  - `ask_mode`
  - `max_question_count`
  - `question_hint`
  - `question_reason`
  - `question_source`
- Router가 이미 호출되는 turn에서만 질문 필드를 같이 받을 수 있도록 Router schema 확장
- Router가 생략되는 fast-path turn은 cheap rule로 질문 허용 여부를 판단
- Main LLM prompt에 `[QUESTION_HINT]` 블록 추가
- 최종 답변에서 `?`/`？` 기준 질문 문장을 0~1개로 제한
- `question_trace` turn event 기록
- control page state에 `runtime.questionMetrics` 추가
- control page diagnostics UI에 질문 metric grid 추가

## 안전 기준

- 질문 기능 때문에 Router 호출률을 늘리지 않는다.
- 기본값은 `ask_mode=none`, `max_question_count=0`이다.
- 사용자가 직접 답변, 완료 보고, 여부만 대답, 짧은 답변을 요구하면 질문을 금지한다.
- 질문은 최대 1개만 허용한다.
- cooldown 기본값:
  - `QUESTION_MIN_TURN_GAP=3`
  - `QUESTION_MIN_SECONDS_GAP=60`
  - `QUESTION_MAX_PER_10_TURNS=3`
  - `QUESTION_DISABLE_AFTER_FRUSTRATION_SEC=300`
- 전체 기능 off 스위치:
  - `QUESTION_FEATURE_ENABLED=false`

## 아직 하지 않는 것

- `QuestionQueue`
- `ProactiveQuestionEngine`
- 질문 답변의 `preference_candidate` memory write-behind 연결
- 질문 품질 기반 durable fact 승격
- TTS streaming chunk 단위의 실시간 질문 제거

위 항목은 질문 trace와 control page metric을 며칠 본 뒤 별도 단계로 판단한다.

## 검증

- `py -3 -m py_compile C:\Evelyn\main.py C:\Evelyn\evelyn_core\runtime\evelyn_core\voice_pipeline.py`
- `node --check C:\Evelyn\docs\assets\evelyn-page.js`
- `py -3 -m unittest tests.test_route_policy tests.test_turn_trace_summary tests.test_query_intents`
- `py -3 -m unittest tests.test_voice_turn_orchestrator tests.test_turn_budget`

모두 통과.

## Live verification

2026-06-02 local-only runtime에서 control page chat으로 확인했다.

- Direct-answer turn
  - `ask_mode=none`
  - `question_added=false`
  - `question_reason=direct_answer_requested`
- Technical follow-up turn
  - `ask_mode=topic_continue`
  - `question_added=true`
  - final question count 1
- Immediate next technical turn
  - cooldown hit
  - `ask_mode=none`
  - `question_reason=question_cooldown`
- Forced multi-question removal
  - input asked the model to output three question sentences
  - first run exposed a bug: `question_removed=true` was counted, but all-question removal fell back to the original answer
  - fixed by returning a safe non-question fallback when every sentence is removed
  - retest result: final reply `응, 알겠어.`, `question_removed=1`, final question count 0
- Off switch
  - restarted with `QUESTION_FEATURE_ENABLED=false`
  - technical follow-up input did not add a question
  - final question count 0
- Control page payload/UI assets
  - `/api/control-page/state` exposes `runtime.questionMetrics`
  - served HTML contains `question-added-rate`, `question-removed-count`, `question-ask-mode`
  - served JS contains `runtime.questionMetrics`, `topAskMode`, `questionAddedRate`

Remaining verification gap:

- browser-rendered visual screenshot
- voice/TTS streaming chunk behavior, because final answer shaping is verified but live streamed chunks may already have been emitted before final shaping
