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

## 일회성 capability

발급 token은 `secrets` 기반의 불투명 무작위 값이며 메타데이터를 인코딩하지
않는다. 기본 TTL은 10초다. token record는 다음 값에 묶인다.

- `bridgeInstanceId`
- `turnId`
- mode별 canonical `forwardText`의 SHA-256. 일반 `wake_entry`는 호출어를
  제거하고 validation은 정확한 시험 문장을 보존할 수 있다.
- admission mode (`wake_entry`, `followup`, `validation`)
- validation session/step/attempt/attemptId 전체 binding

원문 STT나 `forwardText`는 token record에 저장하지 않는다. bridge는 STT 직후
발급받고, 실제 chat dispatch 시점에 5초 이상 지난 capability는 같은 turn과
text/binding으로 재발급해 이전 token을 원자적으로 폐기한다. Bot API는 10초
안에 `forwardText`와 모든 binding을 다시 비교해 한 번만 소비한다. 이 갱신은
TTS 준비 지연을 위한 것이며 accepted turn을 늘리지 않는다.

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
   `shouldProcess`, 양수 journal generation과 Local Voice binding을 검증한다.
3. 신규 claim일 때만 token 제거, replay ledger, follow-up lease와 accepted count를
   확정한다.

journal write 실패나 receipt 불일치는 token, replay ledger, follow-up lease와
accepted count를 바꾸지 않아 같은 token으로 재시도할 수 있다. journal에 이미
같은 turn이 있으면 capability와 replay ledger만 terminal 처리한다. 이 suppressed
duplicate는 accepted turn으로 세지 않고 기존 follow-up lease를 열거나 연장하거나
닫지 않으며, Fast Control은 기존 pending/completed redelivery 계약으로 409를
반환한다. stream과 non-stream은 같은 typed transaction과 explicit preclaim receipt를
사용한다.

이 경계가 닫는 범위는 정확히 token `consume -> durable claim` 창이다. token 발급
응답 뒤 Bridge의 chat 요청 전에 Bot API가 재시작하면 process-local token이 사라지고
아직 journal이 없는 창은 남아 있다. validation retry/abort와 journal `fsync` 사이의
cross-process attempt lease도 별도 계약이 필요하다.

## 개인정보와 관측

manager는 프로세스 메모리에만 존재하며 재시작 시 복구하지 않는다. 공개
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

## 현재 검증 범위

pure admission manager와 Bot API/Bridge의 합성 회귀, 공개 Control Page source
경계는 로컬 소스 테스트 대상으로 포함한다. 실제 journal claim 직후 subprocess를
강제 종료해 재시작 시 accepted pending 하나와 자동 대화 재실행 0을 확인했고,
새 manager의 recovered duplicate 억제와 raw journal I/O 실패 후 같은 token 재시도도
검증했다. 관련 Local Voice/Fast Control/ingress/continuity 179개와 전체 discover
2,841개가 실패 없이 통과했다. 그러나 실제 마이크·스피커에서
10개 accepted turn, 2개 barge-in, 15초 silence를 연속 수행한 live local E2E는
아직 사용자 청취 확인과 함께 실행하지 않았다. 따라서 이 문서는 live hardware
완료 증거가 아니라 현재 코드의 fail-closed 계약이다.
