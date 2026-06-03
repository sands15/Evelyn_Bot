# Evelyn Local-Only And LLM Topology Execution Blueprint

작성 기준: 2026-06-02

## 목적

Evelyn을 Discord에 고정된 봇에서 로컬 실행 가능한 assistant runtime으로 분리하고, LLM topology 변경을 감으로 판단하지 않도록 turn 단위 model-call trace를 남긴다.

이 문서는 한 번에 대규모 분리를 끝내기 위한 문서가 아니라, 현재 `main.py`와 이미 분리된 `discord_*`, `voice_*`, `turn_trace` 모듈을 기준으로 안전하게 진행할 순서를 정의한다.

## 현재 판단

- 완전 단일 LLM 전환은 아직 이르다.
- 조건부 멀티 LLM은 유지하되, 명확한 일반 대화의 hot path는 Main LLM 중심으로 짧게 유지한다.
- Router, Summary/Sub, Cognitive blocking은 실제 호출률과 지연을 먼저 계측한 뒤 제한한다.
- Discord는 기능으로는 유지하되, core runtime의 필수 부팅 조건에서 제거한다.

## 1차 완료 범위

### Local-only boot

- `DISCORD_ENABLED=false`이면 Discord token 검사와 `bot.run()`을 건너뛴다.
- control page는 Discord guild 없이 `control-page:local` 세션으로 동작한다.
- 로컬 세션은 `guild_id=0`을 사용해 기존 Discord guild memory와 섞이지 않게 한다.
- `start_local.bat`는 Main/Router/Sub/TTS를 띄운 뒤 local-only `main.py`를 실행한다.
- local mic는 아직 Discord user target에 묶여 있으므로 1차에서는 기본 비활성화한다.

### Model call trace

모든 LLM 역할 호출은 `model_call` 이벤트로 별도 JSONL trace를 남긴다.

필수 필드:

- `model_role`: `main`, `router`, `summary`
- `purpose`: `main_response`, `route`, `cognitive`, `memory_summary`
- `hot_path`: 사용자 응답 전 blocking 여부
- `success`: 성공 여부
- `latency_ms`: 호출 전체 지연
- `first_token_ms`: Main streaming first token 지연, 해당 시에만
- `turn_id`, `session_key`, `source`, `guild_id`
- `error`: 실패 시 짧은 오류

이 이벤트는 summary payload에 끼워 넣기보다 원시 event로 남긴다. 그래야 과거처럼 p95 summary만 남고 호출률/평균 지연을 복원하지 못하는 문제를 피한다.

재시작 후에도 지표가 바로 비어 보이지 않도록 control page state를 만들 때 최근 `logs/turn_trace/*.jsonl`에서 `model_call` 이벤트를 한 번 replay한다. 예전 trace처럼 `model_call`이 없는 파일은 denominator로 쓰지 않는다.

### Control page runtime metrics

`/api/control-page/state`의 `runtime.modelCallMetrics`에 rolling 집계를 노출한다.

- `routerRouteCallRate`: completed turn summary 대비 Router route 호출률
- `routerAvgLatencyMs`, `routerP95LatencyMs`: Router route 평균/p95 지연
- `mainFirstTokenAvgMs`, `mainFirstTokenP95Ms`: Main streaming first token 평균/p95
- `summaryHotPathRate`: Summary/Sub 호출 중 hot path 비율
- `cognitiveBlockingRate`: completed turn summary 대비 cognitive blocking 비율
- `byPurpose`: `modelRole + purpose + hotPath` 단위의 세부 count/latency

## 진행 순서

1. local-only boot path 추가 - 완료
2. control page local chat path 추가 - 완료
3. `model_call` trace helper 추가 - 완료
4. Router LLM route/cognitive 호출에 purpose 구분 추가 - 완료
5. Summary/Sub write-behind 호출에 purpose 구분 추가 - 완료
6. Main LLM streaming 호출에 request latency와 first token latency 기록 - 완료
7. control page runtime payload에 rolling model-call 집계 노출 - 완료
8. control page UI에 model-call metric grid 추가 - 완료
9. 최근 turn trace JSONL의 `model_call` replay 집계 추가 - 완료
10. control page local chat에도 `text_turn_summary` denominator 추가 - 완료
11. tests/py_compile로 regression 확인 - 완료
12. 실제 로컬모드로 몇 턴 실행해 Router call rate, avg/p95 latency, Summary hot-path rate, Cognitive blocking rate 검증 - 부분 완료
    - local-only boot, control API, local chat, `model_call`, `text_turn_summary` 저장은 확인됨.
    - 검증 중 pre-summary `model_call` 1건이 남아 있어 `modelCallCount`가 `turnSummaryCount`보다 1 크게 보일 수 있음.
    - Router/Summary/Cognitive 지표는 아직 해당 호출이 발생하지 않아 null/0 상태가 정상임.
13. control page Router 조건 1차 조정 - 완료
    - control page chat은 `source=control_page`로 trace에 남긴다.
    - 일반 로컬 채팅은 더 넓은 길이 범위에서 Main 직행 fast path를 탄다.
    - `검색 없이`, `찾지 말고`, `without search` 같은 부정형 검색 표현은 search/deep route trigger에서 제외한다.
    - 검증 결과 같은 부정형 검색 문장에서 `routerRouteCallCount`는 유지되고 `mainResponseCallCount`만 증가했다.

## 아직 하지 않는 것

- Discord dependency 제거
- runtime 중 Discord start/stop 토글
- local mic의 완전 로컬 STT/TTS playback route
- 새 `ModelCallPolicy` 대형 추상화 도입
- Router 호출 조건 대폭 변경
- model-call metric 차트화

이 항목들은 trace가 쌓인 뒤 별도 단계로 진행한다.

## 검증 기준

- `py_compile main.py` 통과
- turn trace에 `model_call` 이벤트가 남는다.
- Router route 호출과 cognitive 호출이 `purpose`로 구분된다.
- Summary/Sub write-behind는 `hot_path=false`로 기록된다.
- local-only mode에서 Discord token 없이 control page가 뜬다.
- 기존 Discord mode에서는 `DISCORD_ENABLED=true`일 때 기존 `bot.run()` 경로가 유지된다.
- control page local chat도 `model_call`과 `text_turn_summary`를 함께 남겨 rate denominator가 생긴다.

## 리스크와 대응

- `main.py`가 이미 여러 진행 중 변경을 포함한다.
  - 대응: 기존 변경을 되돌리지 않고, 좁은 helper와 call-site만 추가한다.
- `local mic`가 Discord target에 묶여 있다.
  - 대응: 1차 local mode에서는 `LOCAL_MIC_ENABLED=false`로 시작한다.
- Main LLM streaming first token은 여러 fallback 경로가 있다.
  - 대응: first chunk 이벤트와 final model_call 이벤트를 모두 남긴다.
- Summary/Sub hot path 여부는 호출 함수만으로 판단하기 어렵다.
  - 대응: 호출하는 쪽에서 `hot_path`를 명시적으로 넘긴다.
