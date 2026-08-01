# Evelyn Autonomy Authorization And Outcome Contract

Document status: **Current**
Last reviewed: 2026-08-01 KST

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
7. executor가 성공 결과를 반환한 뒤에도 실행 전과 같은 grant ID가 여전히
   유효한지 재검사한다. 실행 중 만료·철회·교체되면 결과가 실제로 발생했더라도
   plan cursor를 진행하지 않는다.
8. 만료·철회·scope 불일치가 발견되면 루프를 중단하고
   `authorization_required`로 전환한다.
9. executor 결과가 성공 상태여도 `verified=true`와 그 action에 허용된 exact
   `evidence_code`가 모두 없으면 계획 cursor를 진행하지 않는다.
10. retry budget 소진은 효과 증거가 아니며 성공·skip으로 바꾸거나 plan cursor를
    진행하지 않는다.
11. 승인·결정·결과 journal을 durable 기록할 수 없으면 모든 grant를 폐기하고
    `authorization_audit_unavailable`로 fail-closed한다.
12. 구현되지 않은 callback은 `executor_callback_unavailable`로 차단하며
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
evidence code만 저장한다. 각 이벤트 행은 반환 전에 flush와 `fsync`를 완료한다.
기록 실패 시 새 grant나 실행 허용을 반환하지 않으며 기존 grant도 모두
폐기한다. 기본 retention은 30일 또는 20 MiB이고 최신 7개 파일을 보존한다.

status의 `auditReady`는 현재 journal 기록 가능 여부를 나타낸다. policy에는
`autonomy.outcome-evidence-policy.v1`, `strictActionEvidenceMatch=true`,
`retryExhaustionIsEvidence=false`가 노출된다. Discord `자율상태`는 issuer나
grant ID를 공개하지 않고 현재 guild의 승인 활성 여부, 남은 TTL, audit 상태와
strict evidence policy만 보여준다.

## 결과 검증

계획 진행이 가능한 결과는 다음 조건을 모두 만족해야 한다.

- `status`가 `ok`, `done`, `completed` 중 하나다.
- `verified`가 정확히 `true`다.
- executor가 `autonomy.outcome-evidence-policy.v1`에서 해당 action에
  허용한 exact `evidence_code`를 반환한다.
- 실행 뒤 같은 grant ID가 아직 유효하다.

조건을 만족하지 않는 성공 응답은 `unverified/outcome_unverified`로
정규화한다. plan cursor는 유지하고 다음 cycle에서 재계획한다.

assistant 기본 executor의 대표 증거 코드는 다음과 같다.

- Discord 전송 완료: `discord_send_completed`
- 상태 snapshot 생성: `status_snapshot_built`
- 최근 문맥 요약 생성: `recent_context_payload_built`
- 알림 요약 생성: `summary_payload_built`
- cognitive state 갱신: `cognitive_state_updated`
- proactive gate만 확인한 무동작: `proactive_gate_completed`
- 부작용 없는 idle: `no_side_effect_required`

`maybe_ping_user`는 실제 메시지를 보냈을 때 `discord_send_completed`, 보낼
필요가 없음을 gate에서 확인했을 때 `proactive_gate_completed`만 허용한다.
다른 action의 올바른 코드를 교차 제출해도 검증되지 않는다. Minecraft action은
각 action별 `minecraft_<action>_completed`를 사용하며, inventory/hazard/
hostile/target/food 부재로 명시적으로 생략하는 일부 단계만 각자의 고정
absence evidence code를 추가로 허용한다.

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

Minecraft world-action lease의 감사 내구성과 상태 publication readiness도
실행 권한의 일부다.
status/proof를 소비하는 모든 경계는 `auditReady`와 `statusReady`가 모두
정확한 boolean `true`일 때만 승인된 lease로 인정한다. 필드 누락, `false`,
문자열이나 숫자 같은 대체 값은 각각
`minecraft_world_lease_audit_unavailable` 또는
`minecraft_world_lease_status_write_failed`로 거부한다.

Bot API 단일 owner의 권한 근거는 stable `owner_claim.lock`에 process lifetime
동안 유지하는 exclusive OS lock 하나뿐이다. `owner_claim.json`의 heartbeat,
timestamp와 PID는 진단 정보이므로 오래됐다는 이유로 살아 있는 owner를 교체하지
않는다. claim의 process nonce는 status/proof epoch가 현재 owner publication과
일치하는지 fail-closed로 fencing하지만 새 owner를 선출하는 근거는 아니다. 정상
shutdown은 revoke와 shielded runtime cleanup 뒤 kernel lock을 반납하고,
crash·process exit에서는 OS가 lock을 해제한다. 새 owner는 lock 획득 뒤 새
process nonce와 capability token을 발급하고 이전 lease를 복구하지 않는다.
15초 heartbeat freshness는 Mindcraft/Voyager가 stale status를 거부하고 runner를
정지하는 service-side 경계이며 owner takeover 유예가 아니다.

별도의 stable `world_action.lock`은 proof admission과 실제 effect 사이를 owner
handoff와 직렬화한다. Mindcraft/Voyager `/start`·`/goal`은 검증 직전부터 effect
commit까지 이 lock을 유지하고, successor owner는 predecessor token 폐기와 새
claim/status/secret epoch 게시 동안 같은 lock을 유지한다. 따라서 검증 직후 이전
owner가 죽더라도 이미 검증된 proof가 새 epoch의 effect로 넘어갈 수 없다. lock
busy·unavailable은 임의 재시도나 timestamp fallback 없이 503으로 거부한다.

owner 초기화의 `process_started`, lease 발급, runner 시작 확인, goal 실행 전
시도와 실행 후 확인 event는 각 JSONL 행을 flush하고 `fsync`한 뒤에만 성공으로
인정한다. 필요한 event를 내구 기록할 수 없으면 다음 계약을 적용한다.

- 초기화, lease 발급, runner 시작과 goal 변경은 fail-closed한다.
- 활성 lease와 process capability를 제거하고 공유 private capability artifact를
  더 이상 권한 근거로 제공하지 않는다.
- 이미 runner start나 goal effect가 발생한 뒤 확인 event를 잃은 경우에도
  성공으로 반환하지 않고 runner 안전 정지를 시도한다.
- lease 철회, stop, watchdog cleanup과 shutdown은 감사 저장소가 죽어도 안전을
  위해 실행한다. 물리적 정지가 확인되어도 응답과 status에는 audit unavailable과
  `manual_intervention_required`를 남겨 감사된 성공으로 오인하지 않는다.
- public status artifact를 내구 교체하지 못하면 active lease와 delegation
  capability를 제거하고, 이미 실행 중일 가능성이 있는 runtime을 cancellation에도
  중단되지 않는 safety stop으로 정리한다. 결과는
  `minecraft_world_lease_status_write_failed`와
  `manual_intervention_required`이며 stale status 파일은 권한 근거가 아니다.

Bot API의 내부 mutation endpoint는 인증을 먼저 검사한다. 인증되지 않은 401은
고정 오류만 반환하고 `leaseStatus`나 lease metadata를 노출하지 않는다. 인증된
remote consumer는 authoritative status의 두 readiness boolean과 active/lease
형식을 다시 검증하며, status 누락·손상, 오류, transport 예외와 요청 취소 시
기존 active cache를 즉시 inactive error 상태로 지운다.

이 경계의 status와 journal에는 raw goal, transcript, Minecraft chat, action
arguments, token과 임의 예외 원문을 저장하지 않는다.

현재 Voyager 직접 모드의 승인 경계는 owner/admin의 명시적 접속·종료·목표
명령이다. assistant engine grant의 TTL이 Voyager runner를 자동 중지시키는
watchdog으로 아직 연결되어 있지는 않다. 따라서 이 계약의 시간 제한 grant를
Minecraft의 지속 실행까지 확장했다고 해석하면 안 된다.

## 검증 범위와 남은 증거

단위·composition 테스트는 다음을 검증한다.

- restart 비복구, TTL 만료, exact scope, grant 교체와 철회
- 실제 새 Python 프로세스의 crash/restart grant 비복구
- 민감 payload가 없는 상태와 JSONL 감사 이벤트
- audit journal의 flush/fsync와 write 실패 시 grant 전부 fail-closed
- callback 부재와 evidence 누락의 fail-closed 처리
- action별 evidence 교차 제출 거부와 전체 supported action policy coverage
- 실행 중 grant 교체·만료 시 cursor 유지 및 원래 grant ID 감사
- retry budget 소진의 미검증·cursor 유지
- 미검증 결과와 미검증 skip이 plan cursor를 진행하지 않음
- Minecraft 접속·종료·목표 변경의 긍정/부정 outcome
- lifetime owner lock의 live-owner 경쟁 거부, crash release, nonce/token 회전과
  refresh/status/release adversarial interleaving
- 변경성 Discord 명령의 owner/admin 권한 검사
- 감사 journal retention 기본값

실제 Discord 메시지 전송, 장시간 grant 만료, Minecraft 연결·종료·목표 변경을
한 세션에서 수행하는 live E2E는 별도 운영 증거가 필요하다. 이 검증이 끝나기
전에는 자율행동 P0를 운영 완료로 판정하지 않는다. 직전 world-action lease의
2026-08-01 durable-audit snapshot은 bundled Python에서 Minecraft 115개
(skip 7), runtime 513개(skip 4), 인접 Discord/Mindcraft/UI 39개 회귀를
통과했다. 다만 실제 Minecraft E2E 증거는 아니므로 운영 완료 근거로 사용하지
않는다. 현재 lifetime lock도 Docker Desktop bind mount를 공유하는 실제 두
컨테이너 사이의 exclusion과 SIGKILL crash release는 별도 live 증거가 필요하다.
현재 increment의 source 회귀는 Minecraft 156개(skip 8), runtime 518개(skip 4)를
통과했다. 후속 정리에서 stale opaque note ID 기대값, Windows SQLite 연결 수명,
Voyager 경량 import의 선택 `requests` 결합을 각각 수정했고, 전체 discover
2,482개도 실패 없이 통과했다(skip 18). 이는 source-level 증거이며 실제
Minecraft 연결이나 컨테이너 간 lifetime lock의 live 증거를 대신하지 않는다.
