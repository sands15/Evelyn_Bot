# Evelyn Autonomy Authorization And Outcome Contract

Document status: **Current**
Last reviewed: 2026-07-30 KST

이 문서는 이블린이 “허락된 세계에서 스스로 행동한다”는 목표를 현재
런타임에서 어떻게 제한하고 검증하는지 정의한다. 기능 플래그, 저장된 상태,
서비스 재시작은 사용자 승인으로 간주하지 않는다.

## Assistant 자율 루프 불변식

1. assistant 자율 루프는 현재 프로세스에서 발급된 유효한 grant 없이는
   시작할 수 없다.
2. grant는 guild별 exact action scope이며, 빈 scope와 지원하지 않는 action은
   허용하지 않는다.
3. grant는 기본 1시간, 최대 4시간이고 프로세스 재시작 뒤 복구하지 않는다.
4. `AUTONOMY_ENABLED=true`는 기능 사용 가능 여부일 뿐 자동 실행 승인이 아니다.
5. 저장된 `enabled`와 `allowed_actions`는 재시작 뒤 실행 권한으로 복원하지 않는다.
6. action 실행 직전에 grant와 exact scope를 다시 검사한다.
7. 만료·철회·scope 불일치가 발견되면 루프를 중단하고
   `authorization_required`로 전환한다.
8. executor 결과가 성공 상태여도 `verified=true`와 예상된
   `evidence_code`가 모두 없으면 계획 cursor를 진행하지 않는다.
9. 구현되지 않은 callback은 `executor_callback_unavailable`로 차단하며
   성공 no-op으로 대체하지 않는다.

## 승인 진입점

Discord의 다음 변경성 명령은 허용된 Discord ID 또는 서버 관리자만 실행할 수
있다.

- `자율시작`, `자율정지`
- `마크접속`, `마크종료`, `마크목표`

`자율시작`은 해당 guild에 assistant action scope grant를 새로 발급한 뒤
engine을 시작한다. 시작 실패 시 grant는 즉시 폐기한다. `자율정지`는 engine
존재 여부와 관계없이 grant부터 철회한다.

현재 Control Page의 Minecraft 변경은 CSRF 보호를 통과한 명시적 사용자 요청
경계에서만 실행한다. Control Page에는 일반 assistant 자율 루프를 시작하는
별도 API가 없다.

## Grant 상태

owner는 `AutonomyAuthorizationManager`다.

- 공개 상태:
  `runtime_artifacts/autonomy_authorization/status.json`
- 로컬 감사 이벤트:
  `runtime_artifacts/autonomy_authorization/events/YYYYMMDD.jsonl`
- schema:
  `autonomy_authorization.status.v1`,
  `autonomy_authorization.event.v1`,
  `autonomy_authorization.decision.v1`

공개 상태에는 issuer identity를 넣지 않는다. 로컬 감사 journal에는 누가
승인을 발급했는지 확인할 수 있도록 정규화된 `issuerRef`를 남긴다. 어느
artifact에도 대화문, raw action arguments, 모델 응답 payload, 음성 데이터,
예외 stack 또는 임의 경로를 기록하지 않는다.

감사 이벤트는 action 이름, 고정 reason code, 결과 상태, 검증 여부와 정규화된
evidence code만 저장한다. 기본 retention은 30일 또는 20 MiB이고 최신 7개
파일을 보존한다.

## 결과 검증

계획 진행이 가능한 결과는 다음 조건을 모두 만족해야 한다.

- `status`가 `ok`, `done`, `completed` 중 하나다.
- `verified`가 정확히 `true`다.
- executor가 해당 action 계약에 맞는 `evidence_code`를 반환한다.

조건을 만족하지 않는 성공 응답은 `unverified/outcome_unverified`로
정규화한다. plan cursor는 유지하고 다음 cycle에서 재계획한다.

assistant 기본 executor의 대표 증거 코드는 다음과 같다.

- Discord 전송 완료: `discord_send_completed`
- 상태 snapshot 생성: `status_snapshot_built`
- 최근 문맥 요약 생성: `recent_context_payload_built`
- cognitive state 갱신: `cognitive_state_updated`
- 부작용 없는 idle: `no_side_effect_required`

## Minecraft 직접 제어 결과

Minecraft 접속은 위치 값이나 `active` 값만으로 성공하지 않는다.
`connected`, `minecraft_connected`, `voyager_connected` 중 실제 연결
증거가 확인되어야 `minecraft_connected` 결과를 발급한다.

종료는 running과 connection 상태가 모두 false인 것을 확인해야
`minecraft_stopped` 결과를 발급한다. 즉시 확인되지 않으면 제한된 시간 동안
상태를 polling하고, 끝까지 확인되지 않으면 `minecraft_stop_unverified`로
실패한다.

목표 변경은 서비스 응답의 `goal` 또는 `goal_override`가 요청 문자열과 정확히
일치할 때만 `minecraft_goal_confirmed`로 인정한다. 확인 전에 local override를
먼저 저장하지 않는다.

Control Page와 Discord formatter도 위 outcome marker를 다시 검사하므로, 낮은
계층이 불완전한 payload를 반환하더라도 성공 문구를 만들지 않는다.

현재 Voyager 직접 모드의 승인 경계는 owner/admin의 명시적 접속·종료·목표
명령이다. assistant engine grant의 TTL이 Voyager runner를 자동 중지시키는
watchdog으로 아직 연결되어 있지는 않다. 따라서 이 계약의 시간 제한 grant를
Minecraft의 지속 실행까지 확장했다고 해석하면 안 된다.

## 검증 범위와 남은 증거

단위·composition 테스트는 다음을 검증한다.

- restart 비복구, TTL 만료, exact scope, grant 교체와 철회
- 민감 payload가 없는 상태와 JSONL 감사 이벤트
- callback 부재와 evidence 누락의 fail-closed 처리
- 미검증 결과와 미검증 skip이 plan cursor를 진행하지 않음
- Minecraft 접속·종료·목표 변경의 긍정/부정 outcome
- 변경성 Discord 명령의 owner/admin 권한 검사
- 감사 journal retention 기본값

실제 Discord 메시지 전송, 장시간 grant 만료, Minecraft 연결·종료·목표 변경을
한 세션에서 수행하는 live E2E는 별도 운영 증거가 필요하다. 이 검증이 끝나기
전에는 자율행동 P0를 운영 완료로 판정하지 않는다. Voyager 지속 실행에도
별도 world-action lease와 restart 시 fail-closed 정지 owner가 필요하다.
