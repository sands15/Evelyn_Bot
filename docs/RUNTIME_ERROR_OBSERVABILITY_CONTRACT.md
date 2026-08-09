# Runtime Error Observability Contract

Document status: **Current**
Last reviewed: 2026-07-31 KST

## Purpose

핵심 음성 런타임의 광범위한 예외 경계가 실패를 삼킨 뒤 운영자에게 보이지 않는
문제를 줄인다. Host Supervisor, Local I/O Bridge, Discord, Conversation
Continuity는 heartbeat에, STT, Vision, Codex Gateway, Mindcraft는 HTTP
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

대상 HTTP owner:

- STT
- Vision
- Codex Gateway
- Mindcraft

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

현재 heartbeat에 `lastError`와 `lastErrorCode`가 함께 있으면 `error`, 최근 1시간에
오류가 있었으면 `attention`, 관측 가능한 오류가 없으면 `clear`다. heartbeat가
오래됐으면 현재 오류로 판정하지 않는다.

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

Speaker verification probe embedding 실패 detail은 fixed
`speaker_verification_failed:<exception-type>`만 사용하며 validation event와 turn
metrics에 원문 예외·모델 cache 경로를 복제하지 않는다. Enrollment skip·success 로그도
exception type과 sample count만 남기며 WAV·enrollment directory 경로를 기록하지 않는다.

Discord 음성 pipeline snapshot도 같은 경계를 따른다.

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
- Voice validation observer 자체 실패 로그는
  `[VOICE VALIDATION OBSERVER ERROR] errorType=<exception-type>`만 남기며, 이후 public
  turn-trace projection은 계속한다.
- Opus load·STT warmup의 startup component detail에는 고정 code와 exception
  class만 넣는다. Control Page `bootProgress.steps[].detail`과 외부 wrapper traceback에
  upstream 예외 메시지·경로를 복제하지 않는다.
