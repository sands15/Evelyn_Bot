# Local Voice Admission Contract

Document status: **Current source contract**
Last reviewed: 2026-08-02 KST

로컬 마이크 캡처 동의는 오디오를 읽을 수 있다는 허가일 뿐, 주변의 모든 말을
Evelyn 명령으로 실행해도 된다는 허가가 아니다. 이 문서는 Windows Local I/O
Bridge의 STT 결과가 Bot API의 대화·도구·변경성 side effect에 들어가기 전에
통과해야 하는 process-local admission 경계를 정의한다.

## 신뢰 경계

- 공개 Control Page의 `/api/control-page/chat`은 source를 항상
  `control_page`로 고정한다.
- 브라우저가 `source=local_bridge`를 주장하거나 `admissionToken`,
  `bridgeInstanceId`, `validation`, `validationBinding`을 넣으면
  `unsupported_chat_source`로 거부하고 Bot API 프록시를 호출하지 않는다.
- 공개 프록시는 브라우저가 넣은 admission처럼 보이는 임의 header를 Bot API로
  전달하지 않는다. 실제 capability 전달 계약은 JSON body다.
- 정상 로컬 음성 경로에서 Local I/O Bridge는 내부 Bot API의
  `POST /api/local-voice/admission`에서 capability를 발급받는다. 이 경계는
  주변 발화와 브라우저 source spoof를 막는 계약이며, 같은 호스트의 악성
  프로세스까지 인증하는 OS 보안 경계는 아니다.
- production 발급·소비는 bearer-authenticated Bridge heartbeat의 현재
  `bridgeInstanceId`, 물리 mic ready/active 상태와 content-free capture-consent
  fence를 함께 요구한다. Bot API는 Host HMAC 키를 받지 않으며, Bridge가 검증해
  보고한 SHA-256 fence digest를 Host lease와 Control Page의 durable consent state에
  3자 일치시킨다.
- `source=local_bridge`인 일반·stream chat은 capability binding 검증과 durable
  ingress claim을 같은 admission transaction에서 통과한 뒤에만 user row 기록,
  planner, LLM 요청, TTS 또는 변경성 side effect를 시작한다. 어느 쪽이든
  실패하면 fail-closed다.

## 호출어와 follow-up 상태

초기 상태는 `dormant`다. 일반 발화는 정규화된 문장의 정확한 맨 앞에
`이블린`이 있어야 한다. 다음 문자가 공백, 허용 구두점 또는 문장 끝일 때만
호출어로 인정한다. 문장 중간의 `이블린`과 `이블린아` 같은 접두 유사어는
호출로 인정하지 않는다.

호출어가 있는 capability를 실제로 소비하면 같은 bridge instance에서 45초
follow-up 창이 열린다. 그 안의 일반 후속 발화는 호출어 없이 허용되고, 성공한
소비가 창을 다시 45초로 갱신한다. mic-off, bridge instance 교체, 명시적 reset,
만료된 창은 active 상태와 아직 소비하지 않은 capability를 폐기한다.

shutdown·restart, mic on/off, Minecraft 시작·연결 해제·goal 변경과 그 한국어
동등 표현은 고영향 변경 의도다. 이들은 follow-up 창 안에서도 매번 새
`이블린` 호출을 요구한다.

호출어만 말한 `이블린`도 음성 P0의 실제 턴이므로 빈 문자열로 바꾸지 않고
canonical `이블린`을 downstream text로 사용한다.

## Validation 예외

`voice-p0.v1`에는 호출어가 없는 시험 문장이 있으므로, 현재 local validation
step에 정확히 묶인 발화만 좁게 예외 처리한다.

- binding은 `sessionId`, `stepId`, 양의 `attempt`, `attemptId`가 모두 현재
  active step과 일치해야 한다.
- admission 발급 전에 해당 STT 결과가 그 step의 기대 문장에 대해 keyword
  전부 일치 또는 정규화 유사도 0.70 이상이어야 한다.
- silence step, transcript 불일치, stale·부분·모순 binding은 capability를
  발급하지 않는다.
- capability 소비 시 같은 binding이 여전히 현재인지 다시 확인한다.
- validation 예외도 고영향 변경 의도의 fresh-wake 요구를 우회하지 못한다.
- validation capability 소비는 일반 45초 follow-up 창을 열거나 갱신하지
  않는다.
- admission 발급, durable claim과 HTTP 응답 terminal은 attempt별 cross-process
  OS lease로 retry/abort/confirm과 직렬화한다. lease는 JSON·stream 성공뿐 아니라
  409/503 응답도 실제 HTTP EOF 또는 terminal write failure까지 유지한다.

## Capture-consent fence

Bridge의 heartbeat는 exact watchdog schema, fresh checked time, physical mic 상태와
lowercase SHA-256 fence digest를 bearer-authenticated 내부 상태로만 받는다. 공개
Control Page projection에서는 raw Bridge instance와 fence digest를 제거한다.

Bot API는 다음을 모두 만족할 때만 새 capability를 발급하거나 기존 capability를
claim한다.

- 현재 Bridge instance이고 heartbeat/watchdog가 stale하지 않다.
- pending mic OFF, restart, shutdown이 없고 mic가 ready/active다.
- watchdog이 `authorized`, content-free, not-stopped 상태다.
- Host lease와 durable consent state가 모두 `enabling|active`, 미만료이고 Bridge가
  보고한 fence digest와 정확히 일치한다.

발급은 durable reservation 전과 후에 fence를 다시 확인한다. reservation 직후
동의가 바뀌면 그 exact row를 삭제하고 token을 반환하지 않는다. 소비는 durable
claim 직전에 다시 확인한다. 누락·손상·stale·불일치·예외는 모두 fail-closed이며,
기존 capability와 reservation을 exact revoke한다. revoke를 durable하게 증명하지
못하면 409 성공처럼 축소하지 않고 content-free 503을 반환한다.

Control Page의 모든 durable consent generation 변경과 Bot API의 마지막 fence
확인+reservation/claim은 stable `voice_capture_consent/claim_lease.lock`으로
직렬화한다. Bot이 먼저 잠그면 이미 선형화된 journal 동작이 끝난 뒤 철회가
commit되고, 철회가 먼저 잠그면 Bot은 새 원문을 journal에 쓰기 전에 고정 503으로
닫힌다. 이 lock은 owner credential이나 원문을 담지 않는 1-byte OS lock이며
retention이 삭제하지 않는다.

## 일회성 capability

발급 token은 `secrets` 기반의 불투명 무작위 값이며 메타데이터를 인코딩하지
않는다. 기본 TTL은 10초다. token record는 다음 값에 묶인다.

- `bridgeInstanceId`
- `turnId`
- mode별 canonical `forwardText`의 SHA-256. 일반 `wake_entry`는 호출어를
  제거하고 validation은 정확한 시험 문장을 보존할 수 있다.
- admission mode (`wake_entry`, `followup`, `validation`)
- validation session/step/attempt/attemptId 전체 binding
- 발급 시점의 lowercase SHA-256 capture-consent fence digest

원문 STT나 `forwardText`는 token record에 저장하지 않는다. bridge는 STT 직후
발급받고, 실제 chat dispatch 시점에 5초 이상 지난 capability는 같은 turn과
text/binding으로 재발급해 이전 token을 원자적으로 폐기한다. Bot API는 10초
안에 `forwardText`와 모든 binding을 다시 비교해 한 번만 소비한다. 이 갱신은
TTS 준비 지연을 위한 것이며 accepted turn을 늘리지 않는다.

production 발급은 token을 응답하기 전에 capture fence digest를 포함한 v2
deterministic ingress turn, text hash, reservation reference와 TTL만 content-free
journal row로 durable reserve한다. raw token, STT, `forwardText`, validation prompt는
reservation에 저장하지 않는다.
재발급, mic-off, consent fence 상실, Bridge 교체와 reset은 해당 reservation을
exact batch revoke한 뒤에만 process-local capability를 폐기한다.

한 token이 소비된 `(bridgeInstanceId, turnId)`는 120초 replay ledger에 남는다.
그동안 같은 turn의 재발급은 `local_voice_turn_already_consumed`, 이미 소비한
token의 재사용은 `admission_token_reused`로 거부되어 stream 실패 뒤 non-stream
fallback이 같은 side effect를 두 번 만들지 못한다. 누락, 만료, 재사용,
bridge/turn/text/binding 불일치와 소비 시점의 stale validation은 모두 고정
reason code로 거부한다.

## Durable ingress 원자성

정상 Local Voice 경로는 admission token을 먼저 소비하고 나중에 journal을 쓰지
않는다. manager lock 안에서 token, bridge, turn, canonical text, validation과
follow-up 조건을 모두 검증한 뒤 다음 순서로 처리한다.

1. canonical `[bridgeInstanceId, turnId]`를 source delivery ID로 durable ingress
   journal에 claim한다.
2. frozen receipt의 schema, deterministic entry ID, text hash, phase/disposition,
   `shouldProcess`, 양수 journal generation과 Local Voice binding을 검증한다. durable
   reservation이 있으면 `reservation_verified=true`, exact reservation reference와
   exact ingress turn ID도 모두 필수다.
3. 신규 claim일 때만 token 제거, replay ledger, follow-up lease와 accepted count를
   확정한다.

journal write 실패나 receipt 불일치는 token, replay ledger, follow-up lease와
accepted count를 바꾸지 않아 같은 token으로 재시도할 수 있다. journal에 이미
같은 turn이 있으면 capability와 replay ledger만 terminal 처리한다. 이 suppressed
duplicate는 accepted turn으로 세지 않고 기존 follow-up lease를 열거나 연장하거나
닫지 않으며, Fast Control은 기존 pending/completed redelivery 계약으로 409를
반환한다. stream과 non-stream은 같은 typed transaction과 explicit preclaim receipt를
사용한다.

claim이 durable commit된 뒤의 validation event write는 관측 경계다. 이 쓰기가
실패해도 예외 메시지나 transcript를 노출하지 않고 오류 type만 기록하며, 이미
commit된 claim을 503과 live token으로 되돌리지 않는다. token은 terminalize되고 같은
요청의 LLM·claim은 다시 실행되지 않는다. `turn_accepted` 증거가 없으므로 현재
validation attempt는 이를 성공으로 판정할 수 없다.

Bot API가 token 응답 뒤 chat 요청 전에 재시작해도 Bridge가 제시한 raw token과
exact bridge/turn/text/mode/validation binding으로 token digest와 reservation
reference를 다시 계산한다. 이 proof에는 현재 capture-consent fence digest도
포함되므로 이전 세대 A의 row는 재활성화된 세대 B에서 exact-match되지 않는다.
현재 fence와 validation binding을 먼저 확인하고, journal의 exact unexpired
reservation을 claim한 receipt가 있어야만 recovered capability를 소비한다.
mic-off·명시 철회·restart·shutdown은 process-local token 목록과 별개로 Fast Control
scope의 모든 `reserved` row를 한 번 더 durable purge한다. claimed/completed row와
다른 scope는 건드리지 않는다. reservation 누락·만료·불일치, stale capture context나
stale validation은 대화를 실행하지 않는다.

따라서 현재 source는 `reserve -> token response -> restart -> claim -> consume`과
`consume -> durable claim`의 crash-loss 창을 함께 닫는다. claim이 이미 존재하면
replay-only로 terminalize하고 accepted count나 follow-up을 새로 열지 않는다.

## 개인정보와 관측

manager의 live token/replay/follow-up 상태는 프로세스 메모리에만 존재한다. 재시작
복구 근거는 manager snapshot이 아니라 위 content-free durable reservation과 요청의
exact binding이다. 공개
`local_voice.admission.status.v1`에는 active 여부, mode, accepted/rejected
count, 마지막 고정 reason code와 `contentFree=true`만 포함한다. manager는 raw
token 대신 SHA-256 digest만 process memory에 보관하며 이를 영속화하거나 공개하지
않는다. manager의 token record와 공개 admission status에는 다음 값을 저장하지
않는다.

- raw audio, PCM, 오디오 경로
- 원문 STT, transcript, prompt, `forwardText`
- raw admission token
- validation attempt ID와 기대 문장

검증 마법사가 현재 말할 문장을 표시해야 하므로 진행 중인 validation session
artifact에는 suite의 기대 prompt가 존재한다. 반면 validation event와 최종
report에는 raw transcript·audio·prompt를 저장하지 않는다.

validation LLM 요청은 system prompt와 현재 user text만 사용하는 격리 payload이며
memory/history/tool/search/vision context를 사용하지 않는다. assistant 원문은 normal
chat history, checkpoint나 replay에 저장하지 않고 SHA-256 terminal marker와
non-replayable receipt만 남긴다. accepted user text는 exact ingress claim 계약 때문에
bounded journal TTL 동안만 존재하며 session history나 report로 복제하지 않는다.

## 현재 검증 범위

pure admission manager와 Bot API/Bridge의 합성 회귀, 공개 Control Page source,
durable reservation/restart, consent generation 교체, revoke/claim 선형화 race와 실제
HTTP EOF lease 경계는 로컬
소스 테스트 대상으로 포함한다. journal reserve·claim·head write 경계의 오류와
강제 종료를 재현해 자동 대화 재실행 0, exact duplicate 억제, raw 오류 없는 재시도와
reservation revoke를 검증했다. 그러나 실제 마이크·스피커에서
10개 accepted turn, 2개 barge-in, 15초 silence를 연속 수행한 live local E2E는
아직 사용자 청취 확인과 함께 실행하지 않았다. 따라서 이 문서는 live hardware
완료 증거가 아니라 현재 코드의 fail-closed 계약이다.
