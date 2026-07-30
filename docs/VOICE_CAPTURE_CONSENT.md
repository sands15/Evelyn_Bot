# Voice validation capture consent

로컬 음성 검증 마법사는 시스템의 일반 마이크 설정과 별도로 시간 제한 동의를
요구한다. 이 계약은 Control Page에서 시작한 `voice-p0.v1` 로컬 검증에만
적용된다. 운영자용 `/mic on`·`/mic off` 명령의 계약은 변경하지 않는다.

## 안전 기본값

- Local I/O Bridge는 계속 `LOCAL_MIC_ENABLED=false`로 시작한다.
- 브라우저가 직접 Bot API의 마이크 제어 endpoint를 호출할 수 없다.
- Control Page의 모든 동의 변경 API는 기존 Origin/Host/CSRF 검사를 거친다.
- preview token은 메모리에만 존재하며 120초 뒤 만료되고 한 번만 사용할 수 있다.
- 동의는 검증 세션에 연결되기 전 최대 5분, 연결된 뒤 최대 30분 유효하다.
- 검증 성공·실패·중단, 명시적 철회, 임대 만료, Control Page 정상 종료 또는
  새 프로세스의 stale-owner 복구 시 마이크 OFF를 요청하고 Bridge ACK를 확인한다.
- 마이크 ON ACK뿐 아니라 `captureReady=true`가 확인되어야 동의가 활성화된다.
  확인되지 않으면 즉시 OFF를 요청한다.

## API

- `GET /api/control-page/voice-capture-consent`
- `POST /api/control-page/voice-capture-consent/preview`
- `POST /api/control-page/voice-capture-consent/apply`
- `POST /api/control-page/voice-capture-consent/revoke`

preview/apply의 허용 scope는 `voice_validation_local` 하나뿐이다. Control Page의
음성 capability는 활성 동의가 없을 때 `local_mic_consent_required` blocker와
확인형 동의 action을 제공한다. 이미 생성된 preflight 세션은 동의 적용 후 다른
blocker가 없으면 같은 세션으로 `running` 전환된다.

## 저장 계약

`runtime_artifacts/voice_capture_consent/state.json`에는 다음 제어 메타데이터만
저장한다.

- FSM state와 scope
- 무작위 lease/process owner 식별자
- validation session ID
- 요청·활성·만료·철회 시각
- 짧은 오류 코드와 철회 이유

원문 음성, 오디오 경로, 사용자 발화, prompt, STT transcript는 저장하지 않는다.
상태 파일은 누적 로그가 아니라 단일 최신 상태이며, ON 요청 전에 `enabling`,
OFF 요청 전에 `revoking`을 먼저 기록해 재시작 시 fail-closed 복구할 수 있게
한다.
