# Runtime Error Observability Contract

Document status: **Current**
Last reviewed: 2026-08-12 KST

## Purpose

핵심 음성 런타임의 광범위한 예외 경계가 실패를 삼킨 뒤 운영자에게 보이지 않는
문제를 줄인다. Host Supervisor, Local I/O Bridge, Discord, Conversation
Continuity와 Fast Control Continuity는 상태 artifact에, STT, Vision, Codex Gateway, Mindcraft는 HTTP
health/status 응답에 프로세스 수명 기준 오류 카운터와 최근 오류 코드를
additive 필드로 기록한다.

## Heartbeat fields

각 상태 파일은 기존 스키마를 유지하면서 다음 필드를 추가한다.

```json
{
  "errorCount": 3,
  "lastErrorAt": 0,
  "lastErrorCode": "voice_rearm_failed",
  "lastErrorType": "TimeoutError",
  "errorCounters": {
    "voice_rearm_failed": 3
  }
}
```

- `errorCount`는 현재 프로세스가 시작된 뒤 관측한 오류 횟수다.
- `lastErrorAt`은 Unix epoch 초다.
- `lastErrorCode`는 고정된 소문자 코드다.
- `lastErrorType`은 예외 클래스 이름만 담는다.
- `errorCounters`는 고정 코드별 프로세스 수명 카운터다.
- 서비스 재시작 시 카운터는 0부터 다시 시작한다.
- 등록되지 않은 동적 오류 코드는 `runtime_error`로 축약해 경로·입력값 유출을 막는다.
- 예외 타입은 `Error` 또는 `Exception`으로 끝나는 클래스 이름 형식만 허용한다.
  이 형식이 아닌 legacy 문자열은 빈 값으로 축약한다.

대상 heartbeat:

- `runtime_artifacts/host_supervisor/status.json`
- `runtime_artifacts/local_bridge/status.json`
- `runtime_artifacts/discord/status.json`
- `runtime_artifacts/conversation_continuity/status.json`
- `runtime_artifacts/fast_control_continuity/status.json`

Discord `자율시작`의 engine 생성, 기존 loop cleanup, 새 loop start에서 발생한
일반 예외(`Exception`)는 `autonomy_start_failed`로 Discord owner counter에 기록한다.
관측 기록기 자체의 일반 예외는 명령의 승인 회수·고정 실패 응답을 막지 않는다.
engine start 뒤 성공 응답 전송 실패는 시작 실패로 오분류하거나 grant를 회수하지 않는다.
기존 engine cleanup, 선택적 route 재연결 또는 새 start await의 취소는 오류로 기록하거나
응답하지 않고 기존 grant를 회수한 뒤 같은 취소 신호를 재전파한다.

Discord `자율정지`는 grant를 먼저 회수하고, engine cleanup의 일반 예외만
`autonomy_stop_failed`와 exception type으로 같은 Discord owner counter에 기록한다.
관측 기록기 자체의 일반 예외는 고정 실패 응답을 막지 않는다. cleanup이 성공한 뒤의
성공 응답 전송 실패는 정지 실패로 기록하거나 두 번째 실패 응답을 보내지 않고 그대로
전파한다. cleanup 취소도 오류로 기록하거나 응답하지 않고 같은 취소 신호를 재전파한다.

자율 후속의 Discord 전송이 정상 반환한 뒤 history, active session, continuity,
memory 또는 self-state 후처리에서 발생한 일반 예외는
`autonomy_followup_finalize_failed`와 exception type만 같은 Discord owner counter에
기록한다. 관측기와 type-only logger의 일반 예외는 이미 전달된 action 결과나 plan
진행을 바꾸지 않는다. 취소와 memory deletion integrity 신호는 이 일반 오류 경계에서
삼키지 않는다.

Discord text의 기존 outer failure boundary에 도달한 일반 예외는 shared
`DiscordRuntimeStatus`에 고정 `discord_text_turn_failed`, exception type과
process-lifetime count 기록을 시도한다. 다음 status heartbeat에서 기존 Discord Runtime
Errors source로 보일 수 있으며, 예외 메시지·답변·사용자 입력은 기록하지 않는다.
type-only logger, status recorder와 turn-summary observer는 모두 best-effort다. 이
observer들의 일반 예외는 고정 `text_turn_failed` 응답·continuity를 막지 않으며,
진행 중 text turn의 취소 신호를 기록하거나 다른 예외로 바꾸지 않는다.

Fast Control continuity status는 주기 heartbeat가 아니라 restore·commit·오류 시 갱신되는
event snapshot이므로 최근 오류 창과 같은 1시간 freshness를 사용한다.

대상 HTTP owner:

- STT
- Vision
- Codex Gateway
- Mindcraft

Counter payload가 없어도 현재 장애로 합성하는 필수 health source:

- Control Page
- Bot API
- Main LLM
- Sub LLM
- Router LLM
- TTS
- STT

## Runtime Health

`collect_runtime_health()`는 다음 additive 필드를 제공한다.

```json
{
  "observability": {
    "exceptions": {
      "schema": "runtime_errors.summary.v1",
      "state": "clear|attention|error|unknown",
      "summary": {},
      "sources": {},
      "recentErrors": []
    }
  }
}
```

현재 상태 artifact에 owner 오류가 있으면 `error`, 최근 1시간에 오류가 있었으면
`attention`, 관측 가능한 오류가 없으면 `clear`다. 상태가 오래됐으면 현재 owner 오류로
판정하지 않는다. 위 필수 health source의 probe가 payload 없이
실패한 경우에도 현재 장애로 표시하지만, 프로세스가 기록한 예외가 아니므로 `errorCount`와
`totalCount`는 올리지 않는다. 선택 서비스의 payload 없는 probe 실패는 durable desired-state를
알 수 없어 합성하지 않는다. 이는 의도된 OFF 오탐을 피하지만 예상하지 못한 optional 장애도
Runtime Health에만 남을 수 있는 현재 한계다.

Control Page의 큰 합계는 `기록된 예외` 횟수이고 `currentErrorCount`는 owner 오류와 필수
서비스의 현재 probe 장애를 함께 센다.

## Control Page API

`GET /api/control-page/runtime-errors`

읽기 전용 API이며 apply, reset, delete endpoint는 제공하지 않는다.

## Privacy boundary

합성 결과와 Control Page에는 다음 정보를 노출하지 않는다.

- 예외 메시지
- stack trace
- 파일시스템 경로
- 요청 본문, transcript, raw audio

기존 서비스 heartbeat의 `lastError` 호환 필드는 유지하지만 새 합성기는 해당 값을
공개 응답으로 복사하지 않고 현재 오류 존재 여부 판정에만 사용한다.

### Launcher startup boundary

Windows launcher가 추가하는 시작 실패 요약은 사용자용
`EVL-START-NNNN` fixed code·설명·조치만 사용한다. 이는 HTTP/heartbeat의 소문자
runtime error code와 별도 namespace이며 프로세스 종료 코드도 대체하지 않는다.
최신 startup failure 파일에는 timestamp, fixed code, exception type과 fixed stage만
기록하고 예외 메시지, stack trace, token, 절대경로는 기록하지 않는다. 사용자 조치 표의 정본은
[`EVELYN_DOCKER_RUNTIME_QUICKSTART.md`](EVELYN_DOCKER_RUNTIME_QUICKSTART.md#시작-실패-오류코드)다.

Minecraft live status fallback은 upstream error를 fixed `minecraft_status_failed`, local
client·observer 예외를 `minecraft_status_failed:<exception-type>`으로 Main LLM context,
snapshot cache와 Control Page에 공개한다.

Mindcraft connection handler와 bot error listener는 disconnect/kick/error input을
분류에만 사용한다. console·MindServer `bot-output`과
handler/listener output에는 fixed classification message만 남기고 raw input을 복제하지 않는다.

Summary LLM primary·compact retry 실패 로그는 고정 prefix와 exception type만 남기며,
memory prompt·응답·예외 메시지·경로를 운영 로그에 복제하지 않는다.

Proactive open-question promotion과 background vault maintenance 실패 로그의 예외
detail도 `errorType=<exception-type>`만 남겨 예외 메시지·경로를 운영 로그에
복제하지 않는다.

Background cognitive refresh 실패 로그는 fixed prefix와 exception type만 남기며,
guild/session key·reason·예외 메시지·경로를 운영 로그에 복제하지 않는다.

Main LLM warmup non-200 response body는 읽지 않고 startup component detail과 외부
wrapper에 각각 fixed `llm_warmup_failed`, `LLM warmup failed`만 남긴다.

OmniVoice startup health·generate warmup의 non-200 response body도 읽지 않고
startup component detail에는 fixed `tts_warmup_failed`, 외부 wrapper에는 phase별
고정 문구만 남긴다.

Control Page server-start 실패도 startup component detail에는 fixed
`control_page_start_failed:<exception-type>`, operation log에는 fixed code/type만
남긴다. local-only outer wrapper `Control Page start failed`는 원인 traceback을 억제한다.

Control Page welcome LLM non-200 response body는 읽지 않는다. failure model-call turn
trace와 operation log에는 exception type만 남기며 fallback welcome은 유지한다.

Control Page tool-router failure operation log는 fixed prefix와 exception type만 남기며
원문 예외·경로를 console에 복제하지 않는다. 실패 결정의 기존 `None` fallback은 유지한다.

Speaker verification probe embedding 실패 detail은 fixed
`speaker_verification_failed:<exception-type>`만 사용하며 validation event와 turn
metrics에 원문 예외·모델 cache 경로를 복제하지 않는다. Enrollment skip·success 로그도
exception type과 sample count만 남기며 WAV·enrollment directory 경로를 기록하지 않는다.

Discord 음성 pipeline snapshot도 같은 경계를 따른다.

Discord-channel audio가 성공한 뒤의 text projection 또는 memory-exposure guard 예외는 shared
`DiscordRuntimeStatus`에 고정 `discord_voice_text_delivery_failed`, exception type과
process-lifetime count 기록을 시도한다. 같은 fixed code/type의 content-free
`voicePipeline.lastFailure`와 turn event도 별도로 기록을 시도한다. shared status는 다음
status projection/heartbeat에서 기존 Discord Runtime Errors source로 보일 수 있다.
어느 observer의 일반 예외도 완료된 turn을 바꾸거나 text send를 재시도하지 않으며,
취소 신호는 일반 오류로 기록하지 않고 그대로 전파한다.

Discord last voice-channel state 저장 실패 운영 로그도
`[VOICE STATE SAVE FAIL] errorType=<exception-type>`만 남기며 예외 메시지·경로를
복제하지 않는다.

- `lastFailure`는 `kind`, `errorType`, 숫자 `at`, `contentFree=true`만 공개한다.
- voice rejoin 오류는 `voice_rearm_failed`와 검증된 예외 클래스 이름만 공개한다.
- turn summary의 `error`는 고정 코드 또는 예외 클래스 이름이며 임의 문자열은
  `turn_failed`로 축약한다.
- STT timeout, TTS request/producer/playback, voice connection 부재, 빈 답변과
  delivery 실패는 고정 코드별 카운터를 유지한다.
- wake/STT/TTS/rejoin 로그와 validation observer로 전달되는 오류 필드에는 예외
  메시지 대신 고정 코드와 클래스 이름만 넣는다.
- Discord voice connect retry 실패 로그에는 기존 attempt/channel metadata와 exception
  type만 남긴다.
  retries를 소진한 final wrapper는 upstream 예외 메시지·경로를 caller나 운영 로그에
  전달하지 않는다.
- Voice validation observer 자체 실패와 그 오류 출력 실패는 원래 turn control flow 또는
  이어지는 public turn-trace projection으로 전파하지 않는다. 관측 가능한 경우
  `[VOICE VALIDATION OBSERVER ERROR] errorType=<exception-type>`만 남긴다.
- Turn-trace writer·file·console sink의 일반 예외(`Exception`) 실패도 원래 turn control
  flow로 전파하지 않는다.
  관측 가능한 fallback은 `[TURN TRACE FILE ERROR] errorType=<exception-type>`,
  `[TURN TRACE SINK ERROR] errorType=<exception-type>` 또는 JSON
  `"trace_error_type": "<exception-type>"`만 남기며 예외 메시지·경로를 복제하지 않는다.
  fallback 출력까지 실패하면 해당 관측 시도만 무음으로 끝낸다. Model-call의
  `BaseException`도 turn trace에 넣기 전에 exception type으로 축약한다. 취소와
  `SystemExit`·`KeyboardInterrupt` 같은 control signal 자체는 기존대로 전파한다.
- Opus load·STT warmup의 startup component detail에는 고정 code와 exception
  class만 넣는다. Control Page `bootProgress.steps[].detail`과 외부 wrapper traceback에
  upstream 예외 메시지·경로를 복제하지 않는다.
