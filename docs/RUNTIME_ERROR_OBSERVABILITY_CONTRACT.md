# Runtime Error Observability Contract

Document status: **Current**
Last reviewed: 2026-07-30 KST

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
