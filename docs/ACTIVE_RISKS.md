# Evelyn Active Risks

Document status: **Current**
Last reviewed: 2026-08-12 KST
Evaluation stance: 실패 가능성과 검증 공백을 우선 기록

## P0 — Minecraft functional readiness live E2E 대기

Mindcraft HTTP liveness와 실제 Minecraft 자율행동 readiness는 이제 별도
계약으로 판정한다. world lease, runner, fresh telemetry, Minecraft 연결,
gated goal manager의 명령 게이트·explicit postcondition task contract,
active autonomy 중 하나라도 없으면 `voyagerReady=false`다. 누락·모순된
Mindcraft 계약도 fail-closed하며 고정 blocker만 공개한다. world lease
consumer는 `auditReady`와 `statusReady`가 모두 정확한 boolean `true`가
아니면 각각 `minecraft_world_lease_audit_unavailable` 또는
`minecraft_world_lease_status_write_failed`로 거부해야 하며, 감사 내구성이나
상태 publication 증거가 없는 lease를 functional readiness 근거로 사용할 수 없다.

합성·이미지 내부 검증은 통과했지만 실제 Minecraft world에는 접속하지 않았다.
따라서 lease 승인부터 runner 연결, 실제 task effect, 연결 단절과 재시작까지
한 세션에서 readiness가 정확히 전이하는지는 아직 확인하지 못했다.

기아 상태에서 밀만 있고 제작대가 없는 경우도 인벤토리 기반 선행조건 체인으로
보강했다. Goal Manager는 성공 문구가 아니라 실제 아이템 증가를 확인하면서
`통나무 1개 → 판자 4개 → 제작대 1개 → 빵` 순서로 진행하고, 중간 재시작 뒤에도
현재 단계를 복구한다. 격리 회귀와 이미지 검증은 통과했지만 실제 기아 월드에서
제작대 배치·회수와 빵 소비까지 이어지는지는 아직 live 증거가 없다.

다음 조치: 사용자가 별도 Minecraft 검증 세션을 시작할 때
`blocked → starting → ready → blocked` 전이와 실제 world effect를
Control Page, Runtime Health, Mindcraft telemetry에서 함께 대조하고, 밀 9개·
식량 0개·제작대 0개 fixture에서 식량 복구 체인을 수용 테스트한다.

## P0 — 승인된 자율행동 live E2E 검증 대기

2026-08-08 source 재감사에서 2026-08-01에 확인했던 production 연결 공백은
`8cf3581` 이후 닫힌 상태임을 확인했다. `main.py`는 guild별 typed Minecraft
executor builder를 production `RoutedAutonomyExecutor`에 전달한다. Discord
`마크접속`이 실제 연결을 확인한 뒤 route를 활성화하고, `자율시작`이 그 route를
다시 연결·검증한 경우에만 exact production allowlist인
`MINECRAFT_ROUTE_ACTIONS`를 grant에 추가한다. 현재 허용되는 Minecraft scope는
`minecraft:find_food_source` 하나이며, route 검증 실패 시에는 기존처럼
`assistant:*` scope만 발급한다.
`자율정지`는 executor를 끊되 process-local route intent를 유지하고, 명시적
`마크종료`만 route intent를 먼저 비활성화한다. 뒤의 cleanup이 실패해도 intent는
꺼진 채 유지된다. 다음 `자율시작`은 state label과 관계없이 engine stop을 먼저 끝낸다.
보존된 route intent가 있으면 route를 재연결·검증하며, 그 결과에 따라 assistant-only 또는
Minecraft allowlist를 포함한 grant를 발급한 뒤 start한다.
enable·disable과 lifecycle
connect·disconnect는 같은 router lock으로 직렬화돼 intent와 물리 연결이 어긋나지 않는다.
engine start·stop도 cleanup 완료까지 같은 engine lock을 유지한다. 따라서 권한 거부 뒤 살아 있는
disabled loop와 실패한 stop cleanup을 정리하지 않고 `running`으로 성공시키지 않는다.
start 호출자 취소는 재전파해 cleanup 뒤 새 loop를 만들지 않는다. typed Minecraft executor도
readiness 확인 뒤 disconnect currentness를 다시 검사해 stop 완료 뒤 action을 dispatch하지 않는다.

trusted planner도 현재 grant의 연속 prefix까지만 계획한다. 정상 세계의
`gather_logs`나 food 확보 뒤의 `consume_food`가 scope denial로 전체 loop를 끄던
경로는 executor 호출 없는 대기로 바뀌었다. stale·직접 주입 plan은 계속
`authorization_scope_denied`로 engine을 중단한다. 실제 world 검증은 남아 있다.

Mindcraft action gateway는 exact grant·lease·actionRun·goalRun·contract binding으로
content-free world-effect projector를 arm한다. projector의 durable
`effect_verified` event와 owner의 dispatch·completion, assistant의 exact outcome을
`autonomy-p0.v1` observer가 같은 shared artifact 경로에서 상관시킨다. 다만
`goal_verified`는 goal echo이고 readiness `ready`는 준비 상태일 뿐이므로 실제
effect를 대신하지 않는다. observer도 grant/connect/goal/stop 또는 service/queue
mutation을 실행하지 않으며, 필요한 live evidence가 없거나 stale이면 route와
postcondition blocker를 계속 유지한다. world-effect status의 numeric policy가
손상돼도 validation API를 중단하거나 false-ready로 올리지 않고 같은 fixed blocker로 닫는다.
남은 P0는 배선 구현이 아니라 사용자가
승인한 실제 Discord→Minecraft world-effect 세션의 성공·실패·복구 검증이다.

assistant 실행 전 승인·사후 재검사·outcome에는 실행별 `actionRunId`를 추가했고,
Minecraft stop audit에는 원래 lease ID를 보존한다. observer는 각각 같은 실행과
같은 lease의 증거만 결합하므로 교차 실행 또는 교차 lease 증거를 성공으로
오인하지 않는다. producer restart cleanup은 새 process epoch와 non-restoration,
inactive authority 및 verified global stop을 함께 요구한다.

현재 프로세스에만 유효한 guild별 grant, 1시간 TTL, exact action scope,
restart 비복구, 변경성 Discord 명령 권한 검사와 미검증 결과의 plan 진행
차단은 구현되어 있다. action별 exact evidence allowlist, 실행 뒤 동일 grant
재검사, 실행 중 만료·교체의 cursor 차단, retry budget 비증거화, audit journal
flush/fsync와 outcome 기록 실패는 현재 성공을 unverified로 바꾸고 전체 grant를
폐기한다. append 직전 grant가 바뀐 non-current outcome도 cursor 유지·engine 중단으로 닫는다. Minecraft 접속·종료·
목표 변경은 명시적 outcome marker와 실제 상태 증거가 없으면 성공 문구를
만들지 않는다.

executor 실패 문맥도 content-free 계약으로 닫혔다. 환경 관찰 실패,
action 실행 실패와 나머지 cycle 실패는 각각 exact code만 남기며, 실행
예외는 `failed/verified=false` action outcome이라 계획을 진행시키지 않는다.
이전 raw `last_error`와 `executor_errors`는 재시작 load, writer, Discord와
Control Page 최종 consumer에서 다시 정규화한다. 따라서 이 항목의 남은 P0는
오류 원문 처리나 정적 승인 계약이 아니라 실제 승인 세션의 live effect와
실패·복구 전이 검증이다.

현재 Docker local core와 Bot API는 실행 중이지만 Discord bot과
Minecraft/Voyager는 사용자 요청 없이 지연 시작 상태다. 번들 Python에는
`aiohttp`와 `discord`가 없으므로 실제 Discord 승인 명령부터 메시지 전송 및
Minecraft 상태 변화까지 한 세션에서 수행하는 live E2E는 아직 확인하지 못했다.

Minecraft world-action lease, process-rotated capability token, 5초 owner
heartbeat, 15초 service-side stale guard, restart 비복구, `/start`·`/goal`
proof, 만료·상태 불명 시 fail-closed 정지는 구현됐다. Bot API 단일 owner는
stable `owner_claim.lock`의 process-lifetime OS lock을 유일한 소유권 근거로
사용한다. `owner_claim.json` heartbeat와 그 timestamp는 진단 정보이며 살아 있는
owner를 교체하는 근거가 아니다. Discord 인증 위임과 split Fast Control 승인
경로도 구현했고 Local I/O Bridge와 legacy auto-start 우회는 차단했다.
`world_action.lock`은 Mindcraft/Voyager의 proof 검증부터 start/goal effect까지와
successor의 token 폐기·epoch publication을 직렬화해 검증-효과 TOCTOU를 막는다.
Mindcraft의 background reconcile도 이제 같은 lock을 먼저 획득한 뒤 guarded lease
snapshot을 읽고 runner stop 또는 ensure-start effect가 끝날 때까지 유지한다.
`/start`·`/goal`이 이미 획득한 exact-path lock capability는 같은 effect 경계에
재사용하고, busy·unavailable lock이나 획득되지 않은/다른 경로 capability는 고정
오류로 fail-closed한다. 따라서 shutdown·owner handoff와 경합한 이전 epoch의
자동 재시작 우회는 source 계약에서 차단됐다.

이미 child가 살아 있는 `/start`는 durable goal이나 immutable world-effect binding을
다시 쓰지 않는다. 이 idempotent 경계는 actual-class offline 회귀로 확인했지만, 실제
Minecraft에서 repeated start와 `/goal` 전환을 잇는 live E2E는 아직 수행하지 않았다.

2026-08-01 worktree의 추가 source 계약은 world lease event 행을 append 뒤
flush+`fsync`하며 POSIX의 새 daily file은 parent directory entry까지 sync한다.
모든 consumer가 exact `auditReady=true`와
`statusReady=true`를 요구한다. init, lease issue, runtime start와 goal
mutation은 audit loss에서 fail-closed하며 lease/process capability를 제거한다.
status artifact commit 실패도 같은 capability를 제거하고, 실행 중일 가능성이
있는 runtime을 force-stop한 뒤 `minecraft_world_lease_status_write_failed`와
`manual_intervention_required`를 보고한다. stop/revoke/watchdog/shutdown은
안전을 위해 계속 실행하지만 감사된 성공으로 바꾸지 않는다.

내부 delegation 401은 unauthenticated caller에게 `leaseStatus`를 반환하지
않는다. remote는 authoritative status 누락·손상, 오류, transport failure와
cancellation에서 stale active cache를 지운다. 이 경계는 raw goal,
transcript, Minecraft chat과 token을 저장하지 않는다.
Authenticated mutation도 `guildId`를 exact nonnegative JSON integer로 요구해
bool·float·문자열·누락 값의 cross-guild 축소 변환을 owner 호출 전에 막는다.

직전 durable-audit source snapshot은 bundled Python의 Minecraft 115개(skip 7), runtime
513개(skip 4), 인접 Discord/Mindcraft/UI 39개 회귀를 통과했다. 실제 Minecraft
connect/goal/stop live E2E는 계속 미검증이다.

현재 lifetime-lock increment는 같은 bundled Python에서 Minecraft 156개(skip 8)와
runtime 518개(skip 4)를 통과했다. 후속 source-verification 정리에서 stale opaque
note ID 기대값을 비식별화 계약에 맞췄고, Windows SQLite 연결 수명을 setup 실패와
cache-hit 조기 반환까지 닫았으며, Voyager의 경량 local-text-index import를 선택
runtime 의존성과 분리했다. 전체 discover 2,482개는 실패 없이 통과했고 skip은
18개였다. Python `compileall`, 모든 Control Page asset JavaScript의 `node --check`,
`git diff --check`도 통과했다. 혼합 bundled/.venv `pip check`의 기존 platform-tag
6건과 실제 main/Minecraft/Docker smoke는 이 증거에 포함하지 않는다.

artifact secret·claim을 모두 바꿀 수 없는 극단 실패에서는 shutdown이
`world_action.lock`과 lifetime owner lock을 31초 stale fence까지 유지한다. Bot API
Compose와 launcher의 종료 예산은 60초다. 종료 예산을 이보다 줄이거나 lock 파일을
수동 교체하면 오래된 proof가 stale되기 전에 kernel lock이 풀릴 수 있으므로 계약
테스트가 이를 고정한다.

이 lifetime-lock 변경 전 timestamp claim 구현에서는 계획된 Bot API 교체의
claim handoff도 실제 컨테이너에서 검증했다. shutdown 취소를 포함한 cleanup과
30초 stop grace 아래 실제 SIGTERM은 4.2초 안에 claim을 제거했고 다음
`--force-recreate`는 첫 시도에 healthy가 됐다. 이 관측은 역사적 배포 증거로
보존하지만 현재 owner 인수 규칙은 아니다. 현재 계약에서 정상 shutdown은 안전
정리 뒤 kernel lock을 반납하고, crash·SIGKILL은 OS가 lock을 해제한다. 15초는
Mindcraft/Voyager가 stale public status를 거부하고 runner를 정지하는 heartbeat
경계일 뿐 owner takeover 대기 시간이 아니다.

Minecraft가 지연 시작인 동안 owner claim과 공개 상태 heartbeat는 5초로
유지하되, 외부 서비스 `/status` 탐지는 30초로 분리했다. lease 만료와
Mindcraft 자체 authorization guard는 계속 5초 경계이며, 명시적 상태·변경
요청은 즉시 실행된다. 따라서 대기 서비스 timeout이 핵심 런타임 로그와
event loop를 계속 점유하지 않으면서도 unauthorized runner 방어는 유지된다.

공식 Discord 이미지에서 grant crash/restart 비복구, 실행 중 교체·만료,
audit write 실패, exact evidence, Discord status 노출, Minecraft lease와
실제 `main.py` crash/restart를 포함한 집중 테스트 96개를 통과했다. 전체 core
452개도 기능 assertion 실패 0개였고 이미지에 `git` 실행 파일이 없어 과거
main signature를 조회하는 테스트 2개만 환경 오류였다.

남은 공백은 실제 Discord 메시지 전송과 실제 Minecraft 연결·종료·목표 변경을
한 승인 세션에서 수행하는 live E2E다. split Docker의 Bot API 재시작 token
회전·lease 비복구·stale runner 정지, Discord 재시작 시 중앙 lease 유지,
동시 Control Page/Discord 요청의 owner mismatch도 실제 Minecraft 세션에서는
아직 확인하지 않았다.

다음 조치: 사용자가 별도 Discord/Minecraft 검증 세션을 시작할 때 owner/admin과
일반 사용자의 명령 경계, grant 만료·재시작·연결 실패, 각 성공 action의
`verified=true`와 exact `evidenceCode`, 실제 world effect를 한 흐름에서
대조한다.

## P1 — Minecraft lifetime lock의 Docker bind-mount live 검증 대기

소스 계약은 stable `owner_claim.lock`에 건 process-lifetime OS lock을 단일 owner의
유일한 authority로 사용한다. 살아 있는 owner가 lock을 가진 동안 timestamp가
오래돼도 replacement owner는 인수할 수 없고, 정상 shutdown이나 crash로 kernel
lock이 해제된 뒤에만 새 owner가 nonce와 token을 회전하며 lease를 복구하지 않고
시작한다. 이 경계는 이전 `read → unlink → create`와 늦은 refresh/status replace
사이의 TOCTOU를 제거한다.

남은 위험은 Windows 호스트의 Docker Desktop bind mount와 실제 split container
사이에서도 두 stable lock의 byte-range lock/POSIX `flock` exclusion과 crash
release가 소스 테스트와 동일하게 전달되는지 live로 확인하지 않았다는 점이다. 15초 status
heartbeat guard는 service-side 정지 경계이며 lock coherence의 대체 증거가 아니다.

다음 조치: 같은 bind mount를 공유하는 두 Bot API 프로세스·컨테이너를 의도적으로
겹쳐 실행해 live owner가 있는 동안 두 번째 기동이 즉시 fail-closed하는지,
SIGKILL 뒤 kernel lock이 자동 해제되어 새 owner가 lease 비복구·nonce/token 회전
상태로 인수하는지, 이전 proof가 거부되는지를 대조한다.

## P1 — Conversation Continuity live Discord·원격 CI 검증 대기

완료된 대화 턴과 active follow-up을 15분 동안 제한적으로 복구하는 checkpoint,
만료·손상·revocation fail-closed 계약은 구현됐다. guild 초기화는 이제
content-free write-ahead ledger를 먼저 durable 기록한 뒤 모든 sparse runtime
map을 독립적으로 지우고 checkpoint를 강제 교체한다. 교체가 끝난 뒤에만
marker를 없애므로 초기화 도중 프로세스가 죽어도 삭제된 guild가 되살아나지
않는다. checkpoint와 revocation marker는 필요한 경로에서 flush·fsync 뒤
원자 교체된다.

checkpoint v2는 generation, 이전 hash, canonical payload hash를 저장하고
별도의 content-free durable head가 최신 generation/hash를 고정한다. 따라서
valid JSON으로 내용을 바꾸고 self-hash를 다시 계산해도 head 불일치로
거부하며, 과거 generation rollback과 active head 뒤 checkpoint 삭제도
fail-closed한다. checkpoint commit 뒤 head commit 전에 죽은 경우에만 정확히
한 generation 앞선 chain을 복구한다. 기존 v1은 raw JSON hash로 generation
0 head에 먼저 고정한 뒤 다음 변경에서 v2로 연결하며, 빈 store는 empty head를
먼저 전진시킨 뒤 checkpoint를 제거한다.

선택적 외부 키 인증도 구현됐다. repository 밖의 최소 32바이트 key file을
`EVELYN_CONTINUITY_AUTH_KEY_FILE`로 주입하면 두 owner는 head v2에
HMAC-SHA256을 붙이고 writer, cross-surface reader, exact durable receipt가
같은 tag를 검증한다. checkpoint와 일반 hash head를 함께 임의 재작성해도 tag를
위조하지 못하면 거부되고, owner scope가 달라도 정상 tag를 재사용할 수 없다.
signed 상태의 key 누락·불일치와 검토되지 않은 v1
상태는 원본을 지우지 않고 fail-closed하며, v1 승격은 one-shot
`EVELYN_CONTINUITY_AUTH_BOOTSTRAP=true`가 있어야 한다. 기본 환경에는 운영 키를
포함하지 않으므로 배포에서 override를 실제 사용해야 이 보호가 활성화된다.
같은 키의 별도 HMAC domain이 guild revocation ledger 전체와 Fast Action
recovery head의 journal generation/hash도 인증한다. Action 인증 오류는 자동
중단 안내나 ack로 원본을 덮지 않고 `auth_error`에서 멈춘다.

외부 단조 앵커도 구현됐다. repository와 `runtime_artifacts` 밖의 미리 생성한
보호 디렉터리를 `EVELYN_CONTINUITY_AUTH_ANCHOR_DIR`로 주입하면 Main checkpoint,
Fast Control checkpoint, Main guild revocation ledger, Fast Action journal이
각각 독립적인 HMAC generation/hash 슬롯을 사용한다. runtime artifact만 이미
서명된 과거 세트로 되돌리거나 전체 삭제하면 현재 앵커와 불일치해 원본을 새
빈 상태로 덮지 않고 fail-closed한다. artifact commit 뒤 앵커 commit 전 crash는
인증된 chain이 정확히 한 generation 앞선 경우에만 owner가 복구한다. 기존
keyed 상태를 처음 앵커에 채택할 때도 one-shot bootstrap이 필요하고 read-only
cross-surface reader는 승격하지 않는다. guild revocation ledger는 이 모드에서
generation/previous hash/ledger hash를 포함한 v3를 사용한다.

periodic writer가 저장한 직후 첫 Python 프로세스를 `os._exit`로 강제 종료하고
두 번째 새 프로세스가 완료 턴, active follow-up, user ownership, 현재 system
prompt와 reply target을 복구하는 owner-level E2E도 통과했다. 부분 STT와 이전
system prompt는 복구되지 않았다. 별도 guild reset E2E는 durable marker 직후와
runtime clear 직후 두 crash 경계를 각각 강제 종료했고, 대상 guild는 비복구,
다른 guild는 정상 복구됨을 확인했다.

real-main smoke가 설정한 임시 artifact root를 continuity, autonomy,
Minecraft lease도 따르도록 하드코딩 경로를 제거했다. 같은 임시 root에서 실제
`main.py`를 기동·강제 종료·재기동하고 두 번의 restore와 repository 기본
checkpoint 비변경을 확인하는 opt-in CI 시나리오도 추가했다.

awaiting follow-up의 `active_until` 만료를 실행 중에도 강제한다. 만료된 질문
세션은 restart 여부와 무관하게 ambient text/voice admission을 열지 않으며,
명시적 호출·reply만 기존 정책으로 처리한다. 실제 Discord 검증은 아직 남아 있다.

완료 턴이 1초 periodic writer를 기다리던 crash-loss 창도 닫았다. Discord
text는 실제 전송 뒤 완료 상태와 checkpoint를 먼저 durable commit하고
선택적 TTS를 실행한다. Control Page 일반·검색, 검색 후속, 자율 후속,
Discord 명령, 음성 재생 완료 경로도 같은 즉시 commit 계약을 사용한다.
commit 실패는 이미 전달된 응답을 취소하거나 중복 전송하지 않고 고정 오류
코드만 남긴다.

자율 후속은 Discord send await의 정상 반환을 terminal delivery boundary로 삼아
900초 ping fence를 먼저 세우고, history·active session·continuity를 선택적 memory보다
앞서 시도한다. post-send 일반 예외는 `autonomy_followup_finalize_failed`와 type만 남기며
verified send와 plan cursor를 되돌리지 않는다. 따라서 같은 프로세스의 stale
search-pending/unresolved 관찰이 4초 poll마다 같은 답변을 다시 보내는 창은 source-level
회귀로 닫혔다. 실제 Discord timeout·취소의 원격 전달 모호성, process crash와 live
heartbeat 투영은 여전히 검증되지 않았으며 exactly-once나 durable outbox는 아니다.

대화형 자율 후속의 source-level recipient는 active canonical Discord text user session으로
제한된다. 명시적 관찰채널 또는 thread parent 경계 안에서 가장 최근 active target을 고르고,
observation의 session/message/channel/last-active snapshot이 바뀌거나 만료되면 전송 전에
blocked한다. 같은 reply slot은 send부터 exact recipient commit까지 유지하며 cognitive refresh와
prefixed command도 `reply -> session` 순서로 직렬화한다. normal reply가 먼저면 refresh가
nonwaiting으로 물러나고, refresh가 먼저면 exact session lock을 잡은 뒤 reply slot을 한 후속
text turn에 넘긴다. normal handler의 direct target writer는 durable claim과 turn begin 뒤에만
실행되므로 busy/ignored/redelivery ingress가 recipient generation을 바꾸지 않는다. 전달·기록에
성공한 plain-text prefixed command reply는 기존 command-continuity 경로로 target을 갱신할 수 있다.
다만 실제 multi-user/thread Discord에서의 permission,
채널 삭제·reconnect, latency, 동시에 여러 successor가 오는 경우와 process crash는 live
미검증이며, voice session은 이 text recipient 계약에 포함되지 않는다.

음성 재생 완료 경로는 exact assistant history, active follow-up과 process-local room owner
반영, completion continuity commit 시도를 같은 memory-exposure guard 안에서 먼저 처리한 뒤
benchmark, memory write, cognitive gating과 search follow-up을 실행한다. commit이 durable하면
후속 선택 작업 실패가 이미 들은 assistant pair와 active follow-up checkpoint를 되돌리지
않는다. commit 자체의 실패는 기존 고정 오류 경계를 따르며, room owner 자체의 restart 복구,
선택적 예외 전파와 실제 장치 검증은 이 변경의 보장이 아니다.

음성의 source-level 성공 판정은 playback pipeline이 명시한
`playback_completed=false`를 거부한다. Local speaker는 single의 실제 반환값과
streaming의 이 턴별 queued/played chunk 수를 투영하므로 부분 재생, stale validation,
다른 턴의 전역 재생 수 증가를 완료로 오인하지 않는다. 비어 있지 않은 답변의
무재생 반환은 Discord·local single/streaming 모두 기존 실패 finalizer로 합류해
답변 완료로 commit되지 않는다. 교체·취소된 `TurnScope`에 current task가
뒤늦게 attach되면 즉시 거부하고, 새 background task는 coroutine 본문 실행 전에
취소한다. voice worker의 catch-all 로그에는 exception type만 남긴다.

Voice search follow-up의 최초 전달과 재시작 복구도 별도 playback metrics를
전달한다. 명시적 무재생은 최초 continuity를 commit하지 않으며, 복구에서는
`delivery_uncertain`으로 남겨 자동 재전송하지 않는다. Router fallback metadata와
Local Bridge turn과 TTS warmup/control status·log도 upstream 예외 원문 대신 고정
코드와 exception type만 보존한다. Local mic capture 실패도 같은 projection으로
Control Page 상태와 Main dependency context를 닫는다. warmup의 non-200 HTTP body는 읽지 않는다.
Discord `vc.play`·`after` callback·stream source 실패도 turn trace에 fixed
`discord_playback_failed`와 exception type만 남긴다.
Voice validation observer 자체 실패 로그도 fixed
`[VOICE VALIDATION OBSERVER ERROR] errorType=<exception-type>`만 남기며, 예외
메시지·경로를 운영 로그에 복제하지 않는다.
Opus load·STT warmup 실패는 startup state와 Control Page boot progress에 fixed
code/type만 남기고, 외부 wrapper의 원인 traceback도 억제한다.
Main LLM warmup non-200 body는 읽지 않고 startup state와 외부 wrapper에 각각
fixed `llm_warmup_failed`, `LLM warmup failed`만 남긴다.
OmniVoice startup health·generate warmup non-200 body도 읽지 않고 startup state에는
fixed `tts_warmup_failed`, 외부 wrapper에는 phase별 고정 문구만 남긴다.
Control Page server-start 실패는 startup detail에
`control_page_start_failed:<exception-type>`만 남기고 operation log도 fixed code/type만
기록한다. local-only outer wrapper는 원인 traceback을 억제한 fixed
`Control Page start failed`다.
Control Page welcome LLM non-200 body도 읽지 않으며 failure model-call turn trace와
operation log에는 exception type만 남기고 fallback welcome을 유지한다.
Control Page tool-router failure operation log도 exception type만 남기고, 기존 `None`
반환 뒤 text/search fallback을 유지한다.
Speaker verification probe embedding 실패 detail도 fixed
`speaker_verification_failed:<type>`만 validation event·metrics에 전달하며, enrollment
skip·success 로그도 exception type과 sample count만 남긴다.
Main/Fast의 failed tool evidence와 Main vision runtime metrics도 fixed code만
직렬화해 다음 LLM prompt와 turn metrics에 예외 문장·경로를 복제하지 않는다.
Background Vision Watch도 non-200 body를 읽지 않고 분석·capture 실패를 fixed
code/type으로만 artifact, Control Page와 soft vision context에 전달한다.
Minecraft live status fallback도 upstream error는 fixed `minecraft_status_failed`, local
client·observer 예외는 `minecraft_status_failed:<type>`으로 Main LLM context, snapshot
cache와 Control Page에 전달한다.
Mindcraft disconnect/kick/error raw input은 classification에만 사용하며 console·MindServer
`bot-output`과 handler/listener output에는 fixed classified message만 공개한다.
Mindcraft `mcdata`의 전역 protocol `emit` override도 제거해 `PartialReadError`를 표준
listener dispatch로 전달한다. 이 경계는 source·effective overlay offline 증거이며 실제
Minecraft protocol parse failure나 Docker runtime에서는 검증하지 않았다.

`2272668`부터 각 surface는 commit callback의 단순 반환을 durable 성공으로
간주하지 않는다. exact status schema, current/verified checkpoint head,
rollback protection, 양수 generation·session count와 이번 commit 성공
metric을 모두 검증한 최소 receipt만 받는다. 부분·legacy·손상 status는
fail-closed하며, 자율 후속이 실제 owner 필드 대신 없는 `generation`을 읽어
항상 0으로 기록하던 결함도 닫혔다. 이 경계는 실제 전달을 되돌리거나 같은
답변을 재전송하지 않고 durability 실패만 정확히 드러낸다.

현재 branch는 receipt를 방금 완료한 대상에도 결박한다. 각 surface가 exact
session과 turn ID를 writer에 넘기고, writer는 `maxSessions` 상한
밖의 대상도 checkpoint에 우선 포함한 뒤 current head에서 다시 읽어 일치를
검증한다. 다른 session 하나가 저장됐다는 이유로 이번 턴을 durable로 오인하지
않으며, `lastTargetVerified=true`가 없는 status는 소비자가 거부한다. 공개
status에는 대상 session/turn 값이 아니라 검증 여부만 남는다. 기존 user turn이
없는 자율 후속과 Discord 명령은 실제 전달마다 전용 turn ID를 새로 발급한다.

guild-prefixed 명령도 일반 Discord text와 같은 exact channel/thread reply slot으로
직렬화한다. 일반 reply가 먼저면 정상 또는 고정 실패 reply의 전달·continuity와 선택적
voice 경계 뒤 명령을 실행해 새 command reply 다음에 오래된 normal reply가 붙는 역전을 막는다. 명령이 먼저면 뒤
normal turn은 기존 busy-drop 정책을 따른다. 진행 중 normal turn을 취소하는 urgent-command
preemption은 구현하지 않았으므로, 긴 선택적 TTS 동안 명령이 기다릴 수 있는 latency와
실제 Discord 동시 입력은 live 미검증으로 남는다.

`67a7adf`는 Discord reference 전송의 fallback도 같은 중복 방지 경계로
좁혔다. reference 생성이 네트워크 전에 로컬에서 실패했거나 Discord가 첫
요청을 비모호 4xx로 확실히 거부한 경우에만 일반 메시지를 한 번 보낸다.
timeout, 연결 오류, 상태 없는 예외, 5xx와 `408|409|425|429`는 첫 전송의
성공 여부가 모호하므로 자동 재전송하지 않는다. 서버에는 성공했지만 응답만
유실된 요청을 일반 메시지로 다시 보내는 중복 창을 닫은 것이다.

`f0543b7`은 실제 Control Page가 호출하는 standalone Bot API의
process-local `CHAT_MESSAGES` 손실을 닫았다. Fast Control 일반·stream·planner
실패·background follow-up은 이제 별도 v2 hash-chain owner에 즉시 commit되고
fresh process가 UI와 LLM recent context로 복구한다. 이 owner는 Discord/Main
owner와 같은 파일을 동시에 쓰지 않으므로 multi-process overwrite는 없다.

두 short-lived owner의 prompt-time cross-surface merge는 구현됐다. 각
checkpoint의 single writer는 유지하고 상대 process는 current hash/head,
TTL, privacy policy와 revocation ledger를 read-only로 검증한다. Main/Discord와
Fast Control은 선택 session의 durable 상대 활동시각으로 bounded recent
context를 양방향 사용한다. 무관한 session의 후속 commit은 대상 session을
재정렬·갱신하거나 철회/reset 전 문맥을 되살리지 못한다. 선택 대상의
누락·손상된 활동 metadata는 cross-surface와 owner restore에서 모두 거부한다.
owner restore도 checkpoint age와 row `lastActiveAgoSec`를 합친 effective age가
`maxAgeSec`를 넘는 session을 history/active state에 투영하지 않아 다른 session의
fresh flush가 만료 문맥을 되살리지 못한다. raw legacy artifact와 generation-0
anchor는 보존하지만 age를 증명할 수 없는 history는 자동 복구하지 않고 새 실제
turn만 다음 v2 generation으로 잇는다. target scope가 없는 checkpoint의 owner `savedAt`은 삭제
tombstone을 별도로 저장하지 않는 현재 schema에서 reset 부활을 막는
fail-closed 경계라, 무관한 commit과 실제 reset을 구분하지 못하면 상대 문맥을
보수적으로 생략할 수 있다. 중앙 mutation owner나 새 tombstone store는
도입하지 않았다.

남은 공백은 실제 인증된 Discord↔Control Page handoff다. 교차 연결은
`CROSS_SURFACE_CONTINUITY_ENABLED`와 개인 guild/user ID를 명시해야 하며,
기본값은 의도적으로 fail-closed다. 이번 작업에서는 실행 중인 Bot API와
Control Page를 교체하지 않았고 Discord도 시작하지 않았으므로, 한 surface의
완료 턴이 실제 다른 surface의 다음 응답 의미에 반영되는 live 증거와
동시 write 중 반복 read의 Windows filesystem 지연 표본은 아직 없다. source
회귀는 무관한 session ordering, restart 활동시각 복원, revocation/reset과 선택
대상 metadata 거부를 고정하지만 이 live 공백을 대신하지 않는다.

코드는 이제 매 prompt 시도의 결과를
`cross_surface_continuity.merge.v1`으로 계측한다. Main 턴 metrics와 Fast
Control의 `lastMerge`에서 merged/reset/rejected 상태, generation/count,
ordering과 latency를 확인할 수 있어 live E2E가 단순 응답 의미 추정에만
의존하지 않는다. 증거는 원문·사용자/세션 ID·hash·경로를 포함하지 않고
저장하지 않는다. 현재 owner가 손상되면 정상인 상대 owner도 주입하지 않아
검증할 수 없는 reset 경계를 우회하지 않는다.

Discord voice 재무장도 내부 reconnect wait가 client를 반환하지 못하거나 같은 채널
client가 disconnected면 stale client를 강제 정리하고 기존 connect 경로로 교체한다.
stale 정리부터 replacement 생성·재사용까지 guild lock 안에서 직렬화하고 disconnect
오류는 현재 stale 객체의 registry cleanup으로 수렴한다. 이미 연결된 same-channel
client와 disconnect 도중 설치된 replacement를 재확인·재사용해 동시 재연결이 첫 성공을
끊지 않는다. 실제
`is_connected()`가 true인 client만 listener callback, warmup과 last-channel success를 갱신한다.
채널 이동은 listener stop·generation 증가·tracked retry/task cancellation 요청과 bounded
queue 교체 뒤 기존 guild voice TurnScope와 active TTS를 정지하고, playback이 남으면
disconnect해 이동을 중단한다. 내부·외부 channel event는 같은 idempotent cleanup을
재적용하되 guild lifecycle lock 안에서 현재 exact channel만 정리·재청취한다. lock 대기나
cleanup 중 더 최신 이동이 생긴 stale event는 이전 channel로 client를 되돌리지 않는다.
Discord receive와 Discord-target local mic의 internal
client/generation/channel binding은 assembly·dequeue·STT 뒤·dispatch에서 재검증되며,
TurnScope 취소는 per-item child에만 적용되어 shared ingress worker를 종료하지 않는다.
connect·move 동안 pending ingress fence를 유지한다. 비치명 warmup은 lifecycle lock 밖에서
실행해 새 channel event의 turn/TTS cleanup을 지연하지 않는다. event는 관측한 exact
client/channel에만 cleanup을 적용한다. `move_to` 반환 뒤 실제 target channel ID가 아니면
disconnect와 fixed failure로 닫아 false-success·last-channel 저장을 막는다.
남은 검증 공백은 delayed retry·assembly·STT·reply/TTS 진행 중 실제 두 Discord 채널을
이동해 이전 channel utterance가 새 channel reply/playback으로 이어지지 않고 target
listener가 정상 재개되는지 관찰하는 live E2E다. source 경계만으로 완전한 cross-room live
차단을 주장하지 않는다.
last voice-channel state 저장 실패 운영 로그도
`[VOICE STATE SAVE FAIL] errorType=<exception-type>`만 남긴다.
Discord voice connect retry 실패 로그도 기존 attempt/channel metadata와 exception type만
남기며, retry를 모두 소진하면 fixed `voice_connect_failed` wrapper만 caller에 전달한다.

재시작 때 아직 재생을 시도하지 않은 voice search follow-up은 음성 연결이 없다는
이유만으로 `delivery_uncertain`에 고정하지 않는다. recovery claim만 해제하고
`delivery_ready`를 유지하며, 실제 connected client 재무장 직후 같은 recovery를 다시
실행한다. 재생 직전 `delivery_attempted` 전이는 그대로라 모호한 재생 실패는 자동
재시도하지 않는다.

다음 조치: 사용자가 별도 Discord 검증 세션을 시작할 때 개인 scope를 설정하고
Control Page→Discord, Discord text→Control Page, Discord voice→Control Page를
각각 실행한다. 다른 사용자와 다른 guild가 섞이지 않는지, reset 직후 이전
surface 문맥이 비복구인지, 각 턴의 merge 증거와 status가 원문 없이
state/count만 공개하는지도 함께 대조한다.

새 공식 Discord 이미지에서 guild reset/continuity/Discord command wiring과
opt-in real-main crash/restart 집중 테스트 68개, `compileall`, `pip check`를
통과했다. 전체 core 440개도 기능 assertion 실패는 0개였고, 이미지에 `git`
실행 파일이 없어 과거 main signature를 조회하는 테스트 2개만 환경 오류였다.

새 v2 이미지에서도 재계산 hash 변조, 과거 generation rollback, checkpoint
삭제, v1 migration, head commit crash 복구와 실제 `main.py` crash/restart를
검증했다. 외부 키가 설정된 경우 checkpoint/head 동시 임의 재작성도 새
authenticity 테스트가 거부한다. 새 외부 앵커 집중 테스트는 checkpoint,
revocation, Fast Action의 서명된 과거 세트 replay와 전체 artifact 삭제를
거부하고, 세 경로의 한 단계 anchor commit lag를 복구함을 확인한다.

`67a7adf` 공식 Discord 이미지에서는 새 전달 테스트 9개, 인접 경로 8개,
Discord I/O 전체 98개를 통과했다. core 468개도 기능 assertion 실패는
0개였고 이미지에 없는 `git` 때문에 난 기존 서명 검사 2개는 Windows에서
해당 모듈 13개를 재실행해 통과했다. 이미지 digest는
`sha256:66470617533a4d44eca6b53b0b91c2cf6e043a651675a63d74eeb083e2c22181`이며
`compileall`, `pip check`, 전체 profile Compose config도 통과했다. 이미지만
빌드했고 실제 Discord bot은 시작하지 않았다.

남은 검증 공백은 실제 인증된 Discord 세션에서 관리자 초기화 명령 직후
재시작까지 수행하는 live E2E와 이 브랜치의 원격 Windows CI 결과다. 실제
Discord bot은 사용자 요청 없이 시작하지 않았다.

즉시 `fsync`는 완료 턴마다 추가되는 디스크 비용이다. `5acdc83`부터
process-local 최근 성공 256개의 durable commit 지연을 content-free로
계측하고, 시도·성공·실패 횟수와 last/p50/p95/max만 status와 Runtime Errors,
Control Page에 공개한다. 20개 전에는 `warming`이며 이후 p95가 100ms를 넘으면
경고하되 대화 실패로 처리하지 않는다. stale 지표는 현재 경고로 승격하지 않는다.

합성 테스트와 격리된 실제 `main.py` crash/restart는 통과했지만, 이번 배포에서는
Discord/Main owner를 시작하지 않아 실제 대화 표본은 아직 0개다. 따라서 실제
Discord text/voice와 Control Page의 전달 후 commit p50/p95는 여전히
측정되지 않았다.

다음 조치: 사용자가 Discord 검증을 시작할 때 별도 테스트 guild에서 완료 턴과
active follow-up을 만든 뒤 관리자 초기화, 강제 재시작, 대상 guild 비복구와
다른 guild 보존을 확인한다. 원격 브랜치를 올릴 때 Windows CI의 opt-in
real-main 시나리오도 함께 통과시키고, 전달 후 commit 지연을 별도 지표로
측정한다. 실제 정상 완료 턴 20개가 쌓인 뒤 100ms 경고선이 Windows 저장장치
특성에 맞는지도 재평가한다.

Fast Control background 조사 작업의 별도 재시작 손실 창은 닫았다. 시작 전에
원문 없는 durable action 표식을 기록하고, 최종 답변 commit의 예상 continuity
generation과 current owner를 교차검증한다. 결과가 이미 durable하면 재시작
뒤 조용히 정리하고, 실행 중 또는 commit 전 crash면 고정 중단 안내를 한 번
commit하며 원래 작업은 자동 재시도하지 않는다. action commit 실패 뒤 다른
대화가 같은 generation을 사용해 완료로 오판되는 것도 `running` 복귀로 막는다.
실제 `os._exit` fresh-process 검증은 중단 안내가 한 번만 복구되는 것을
확인했다.

action journal도 v2 generation/hash chain과 별도 content-free durable head로
보강했다. 진행 표식이 생성된 chain의 단일 journal/head 삭제, self-hash 변조,
과거 journal rollback은 fail-closed하고, journal 교체 뒤 head 교체 전 crash의
정확한 한 generation만 복구한다. 기존 v1은 raw byte hash로 generation 0에
고정한 뒤 다음 mutation에서 v3로 연결한다.

v3는 각 action marker에 시작 당시 Fast continuity generation도 기록한다.
따라서 이전 action의 동일한 고정 안내가 마지막 문장인 상태에서 새 action이
시작 직후 죽어도 오래된 안내를 새 action 복구 증거로 재사용하지 않는다.
안내 commit 뒤 journal ack 전에 다시 죽은 경우에는 시작 generation보다 큰
현재 generation이 이번 안내의 durable 전달을 증명해 중복 안내를 막는다. 시작
generation이 없는 v1/v2 pending marker는 보수적으로 새 안내를 요구한다.

action journal/head와 guild revocation ledger도 외부 키를 켜면 임의 위조를
거부하고, 외부 앵커까지 켜면 runtime artifact replay와 전체 삭제를 거부한다.
남은 위험은 host의 보호된 앵커 디렉터리까지 과거 사본으로 되돌릴 수 있는
공격자다. 이 범위에는 TPM NV counter나 원격 append-only 원장이 필요하다. 실제
Control Page에서 장시간 웹 조사 중 Bot API 컨테이너를 강제 종료하는 운영 E2E도 아직
수행하지 않았다. live 검증에서는 시작 답변 뒤 강제 종료, 고정 중단 안내,
자동 재요청 0회와 `actions.recovery`의 content-free 상태를 함께 확인한다.

## P1 — Python 모델 런타임 의존성 잔여 취약점

루트/Windows lock의 Torch는 `2.13.0`으로 올라가
`PYSEC-2025-194` 감사 예외를 제거했다. 그러나 Qwen-ASR 0.0.6이
`transformers==4.57.6`을 정확히 요구하므로 Transformers finding 4개는 STT
호환 릴리스 전까지 남는다.

Transformers findings(2026-07-15 확인):

- `PYSEC-2025-217`
- `PYSEC-2026-2290`
- `PYSEC-2026-2288`
- `PYSEC-2026-2289`

CUDA 12.8 공식 인덱스는 현재 Torch/Torchaudio 2.11과 Torchvision 0.26까지만
제공한다. STT/Vision은 그 일치 조합으로 올렸지만 수정 버전 2.13은 사용할 수
없다. exact-latent/FlashAttention 결합인 VoxCPM은 모델 smoke 없이 2.8에서
올리지 않았다.

기본 TTS source는 `k2-fsa/OmniVoice`와 CUDA 12.8 Torch/Torchaudio 2.8로
전환했다. 서버 Python 소스는 exact 20-file SHA-256 allowlist로 닫았고 직접 runtime
의존성을 고정했다. 고정 revision의 필수 model snapshot 경로 13개도 시작 시 SHA-256으로
검증하지만 전이 wheel과 CUDA base image digest까지 고정한 완전 재현 build는 아니다.
offline cache는 revision
`c5fdb5ccb189668d56333f77ba2629f4cd7535f4`를 read-only로 제공해야 한다. 2026-08-08
고정 recipe image build, exact model load/health와 실제 clone sentence PCM 합성은 통과했다.
VoxCPM2의 host 8881 서비스는 opt-in 호환성·진단용이며
자동/runtime fallback도, 기존 client의 reroute도 제공하지 않는다.

Falcon-OCR은 여전히 Hugging Face remote model code 실행을 요구한다. 다만
full commit과 모든 snapshot 파일의 size/SHA-256을 고정하고, 실행 전 전체
검증, 기본 offline/local-only 로드, read-only model cache·root filesystem,
non-root·capability drop·no-new-privileges 경계를 적용했다. 모델 runtime은
외부 gateway가 없는 Compose `vision_isolated` internal network에만 연결한다.
기존 Docker/host 호출은 credential·model cache·bind mount가 전혀 없는 별도
UID 65534 ingress가 method/path/body/response allowlist를 적용해 고정
`vision_runtime:8891`로만 전달한다. 따라서 remote model code에는 인터넷이나
다른 Evelyn 서비스로 가는 network route가 없다.

이 경계는 kernel syscall sandbox가 아니며, 격리된 runtime에 할당된 CPU/GPU/
memory 고갈 시도까지 제거하지는 않는다. GPU 모델 로드 smoke도 별도 승인
전에는 미완료다.

다음 조치: Qwen-ASR의 Transformers 5 호환 릴리스와 CUDA 12.8 Torch 2.13
wheel을 재확인한다. 새 이미지 GPU 모델 로드 smoke 전에는 배포 완료로 판정하지
않는다.

## P1 — Node/Minecraft 의존성 취약점 12개

2026-08-01 Mindcraft runtime의 중복 PvP 구현을 정리했다. 기존
`mineflayer-pvp`가 제공하던 `attack`/`stop` 호출은 이미 설치된
`@nxg-org/mineflayer-custom-pvp`의 `bot.swordpvp`로 호환 연결하고, legacy
패키지와 그 전이 `mineflayer-utils` 가지를 lockfile에서 제거했다. 새 이미지의
`npm audit --omit=dev` 결과는 moderate 12개, high/critical 0개다. 기존 high
5개 ESLint/minimatch 체인도 제거된 상태를 유지한다.

- 직접 의존성: `mineflayer`, `mineflayer-armor-manager`,
  `mineflayer-collectblock`, `@nxg-org/mineflayer-custom-pvp`
- 전이 의존성: `@nxg-org/mineflayer-tracker`,
  `@nxg-org/mineflayer-trajectories`, `@azure/msal-node`,
  `minecraft-protocol`, `mineflayer-tool`, `prismarine-auth`, `uuid`,
  `yggdrasil`

대부분 `fixAvailable=false`다. npm이 fix 가능으로 표시한 custom-PvP와 armor
가지는 각각 현재 최신 `1.7.16`·`2.0.1`에서 구버전 `1.7.2`·`1.4.2`로
내리는 제안이라 적용하지 않았다. 다음 조치: 강제 audit fix는 금지하고,
Microsoft/Xbox 인증 체인과 Mineflayer 플러그인의 호환 릴리스를 추적한다.

## P1 — Runtime Health 공개 projection 잔여 실사용 검증

소스와 새 검증 이미지에서는 Runtime Health의 raw probe evidence를 서비스·
capability 판정에만 사용하고 `runtime_health.public.v1` 폐쇄형 projection을
거친 결과만 Control Page에 제공한다. artifact 경로, probe target/payload/error,
host 설정, PID, 출력 장치명과 임의 legacy/observability 확장 필드는 제거된다.

2026-08-08 정상 launcher가 stale Bot API·Control Page image를 current source로
자동 rebuild했다. 두 service의 image/expected revision과 Control Page의 Bot proxy
identity가 exact `/health`를 통과했고 공식 runtime checker도 core readiness를
통과했다. 실제 `/api/control-page/runtime-health`는
`runtime_health.public.v1`, `ok=true`, `coreState=up`이며 재귀 검사한 금지 key
`defaultHost/error/host/hostEnv/payload/portEnv/target`은 0개였다. privacy 표시는 raw
probe payload와 filesystem path를 모두 `false`로 보고했다.

2026-08-09 source는 각 probe를 manifest의 `timeout_ms`로 강제 제한한다. 멈춘
runner는 target·exception 원문 없이 `reason=timeout`으로 투영되고 required service는
ready가 되지 않는다. cooperative cancellation과 bounded 반환 회귀가 통과했다.

Discord와 Minecraft/Codex는 의도적으로 deferred라 public health 전체는 optional
degraded다. 다음 조치는 사용자가 각 surface를 승인해 기동할 때도 같은 공개 projection과
exact source identity가 유지되는지 확인하는 것이다. Discord normal text failure는 source에서
shared status에 fixed `discord_text_turn_failed`/type/count를 기록하지만, 실제 gateway 실패와
다음 heartbeat의 Runtime Errors 표시까지 잇는 live 검증은 아직 수행하지 않았다.
자율 후속 `send_discord_text` await 자체의 일반 예외도 fixed
`autonomy_followup_send_failed`/type/count로 같은 shared
status에 기록을 시도하지만 실제 Discord send 실패와 heartbeat 표시는 live 미검증이다.

## P0 source 완료 — Control Page capture owner 배포·live 경합 미검증

2026-08-02 source에서 이전의 손상 상태 fail-open은 닫혔다. consent load는
`verified | missing | untrusted`를 구분하고, 누락·손상·symlink·invalid UTF-8·
과도한 중첩·불변식 위반과 이전 owner의 active 계열 상태를 모두 `revoking`으로
복구한다. exact revision/action/Bridge/capture-stopped ACK 뒤에만 durable
`inactive`를 기록하며, OFF 실패·취소·상태 write 실패는 monitor가 다시 시도한다.

같은 변경에서 Bridge status reporter와 Control Page 내부 제어를 별도 process-scoped
bearer로 분리하고, exact `actionId`, 단조 `statusSeq`, Bridge instance 세대와
ON enable fence를 도입했다. OFF는 `disableGeneration`을 먼저 올려 취소·철회 뒤
늦은 ON을 거부한다. 부분·손상·중복·역전 heartbeat는 freshness를 갱신하지 않으며,
ambient 환경값이나 일반 `/mic on`은 더 이상 캡처 ON 권한이 아니다. apply와
validation confirm/retry/abort도 같은 app lock에서 terminal 전환과 exact OFF를
직렬화한다. 최신 preview만 정확한 validation 세대에서 apply할 수 있고, unbound
동의는 canonical idle만 허용하므로 idle ON 뒤 Discord-only 시작도 OFF로 돌아간다.
일반 `/mic on`은 이 권한 경계를 유지하면서 Control Page의 검증 시작·청취 동의
영역이 들어 있는 오른쪽 상태 drawer를 먼저 열고 버튼을 화면에 표시·포커스한다.
capture-ready와 authenticated watchdog, durable consent/
host-lease fence가 모두 current일 때만 이미 ON으로 인정한다. panel command에는
process generation을 붙여 Bot API 재시작 뒤 ID 재사용도 구분한다.
mutation I/O 예외는 고정 503과 즉시 recovery/OFF로 닫힌다. Supervisor의 개별
Docker 복구는 `--no-deps`이고, Bridge와 그 자식은 목적 밖 credential을 상속하지
않는다. 전체 재시작 credential 연속성은 Supervisor-owned exit-code handoff가 맡는다.

2026-08-02 current source는 hard-crash 경계를 추가로 닫았다. Control Page가 1초마다
목적 제한 HMAC으로 서명된 content-free owner/lease projection을 게시하고, Bridge는
각 0.25초 status tick과 ON 전·후에 4초 freshness와 원래 digest binding을 검사한다.
누락·손상·symlink·stale·expired·replacement에서는 입력을 폐기하고 exact capture
stop을 수행한다. stop 실패는 Bridge를 종료 코드 76으로 끝내 OS handle을 회수한다.
Supervisor는 현재 child PID/시작 시각, 서명된 전체 status, 고정 instance,
`statusSeq` high-water와 nested/top-level physical OFF가 모두 맞을 때만 stop evidence를
`verified`로 게시한다. 비신뢰 owner heartbeat는 4 KiB, Bridge status는 128 KiB로
제한된다. 실제 마이크는 켜지 않았다.

current source는 stable `voice_capture_consent/owner_claim.lock`에 Windows byte-range
lock 또는 POSIX `flock`을 process lifetime 동안 유지한다. 이 경계는 manager 생성과
상태 읽기보다 먼저 실행된다. busy/unavailable loser는 content-free 고정 오류로
startup을 중단하고 기존 owner의 state, heartbeat, revoke, mic ON/OFF를 수행하지
않는다. 정상 종료와 cleanup 취소에서도 monitor/heartbeat writer drain과 exact OFF
철회가 끝나기 전에는 lock을 풀지 않는다. hard-crash에서는 커널이 lock을 회수하고
successor가 기존 active lease를 복구하지 않은 채 startup recovery를 수행한다.

동의 generation write와 Bot API local-voice reserve/claim 사이에는 별도 stable
`claim_lease.lock`을 사용한다. 마지막 fence 검사부터 journal commit까지 같은 lock을
유지하고, token/reservation v2 proof를 발급 fence digest에 묶는다. OFF 계열 전이는
manager restart로 process-local token 목록이 비어 있어도 outstanding `reserved` row를
scope 단위로 durable purge한다. 따라서 소스 수준의 revoke-vs-acceptedText TOCTOU와
consent A→B에서 구 token이 되살아나는 경계는 닫혔다. Control Page writer의 대기는
2초로 제한되고 timeout·state write 실패는 memory-first `revoking/untrusted`와
physical OFF로 닫힌다. durable claim 뒤 관측 이벤트 오류도 token을 되살리거나 같은
LLM을 다시 실행하지 않는다.

실제 별도 Python 프로세스 경합·`os._exit(78)`·successor 인수와 aiohttp cleanup 취소
중 contender 배제, claim/revoke 선형화, manager restart와 A→B proof 불일치 회귀를
포함한 source 검증이 통과했다. 따라서 owner와 admission race의 source P0는 닫혔다.
voice 전체 회귀의 최신 개수는 worklog의 최종 검증 항목을 따른다. 실행 중 Control
Page 이미지는 교체하지 않았고, Docker bind mount의 container 간 경합이나 Windows
native와 Linux container를 섞은 두 lock의 coherence는 live 증거가 없다.

## P1 — 실제 음성 하드웨어 E2E 미검증

Windows Host Supervisor는 이제 별도 `.venv-host`와 최소 lock으로 재현 가능하게
설치되며, launcher는 Host Supervisor와 Local I/O Bridge에서 서로 다른 두 번의
fresh heartbeat를 확인한 뒤에만 준비 완료를 보고한다. TTS 음성 프로필의 WAV,
JSON, `ref_text`도 Docker 시작 전에 검사한다.

Control Page의 Discord OFF/ON은 별도 `discord_bot`만 fixed Host Supervisor action으로
전환하고 health heartbeat로 완료를 확인한다. OFF에는 Discord.py의 정상 close 경로를
사용하도록 Compose `SIGINT`와 30초 grace를 설정했지만, 실제 gateway·voice rejoin과
진행 중 text/voice turn drain은 아직 Docker/Discord live로 검증하지 않았다. 그 전에는
대화 사이에 전환하고, 실패 시 Discord worker의 error code/type 주변 로그로 재현한다.

현재 source의 TTS readiness는 HTTP 200만으로 통과하지 않고 실제 OmniVoice의
`status=healthy`, `ready=true`, `model_loaded=true`,
`model_id=k2-fsa/OmniVoice`,
`model_revision=c5fdb5ccb189668d56333f77ba2629f4cd7535f4`를 모두 요구한다. cache는 offline
read-only다. Compose의 `pull_policy: never`와 path-safe builder는 누락·fresh image를
로컬에서 만들고 Supervisor repair는 기존 image만 사용한다. 2026-08-08 실행 container를
고정 recipe로 교체했고 exact model identity/revision, `/v1/models`, Evelyn clone PCM과
read-only mount의 live 증거를 확인했다.

기본 local 합성은 sentence streaming을 사용한다. 실험적 blockwise generation은
client disconnect가 in-flight generation과 concurrency slot을 확실히 취소한다는 live
증거가 없으므로 비활성 상태다. 서버 source는 operational log의 text/path/session
identifier를 제거하고 profile API의 `ref_text`를 숨긴다. 새 image의 실제 profile 응답과
합성 후 로그 검사도 통과했다. client fallback/final failure도 upstream HTTP 오류 본문과
clone profile 대신 고정 `tts_request_failed`·`omnivoice_request_failed`만 남긴다.
Local Bridge의 direct HTTP PCM write는 cancellation-safe worker와 단일 playback
owner를 사용하고, 빈 clone stream만 재생 전 `auto`로 넘긴다. actual segmenter
회귀는 첫 threshold 후보 블록 뒤 owner release→flush와 fresh-wake 거부·normal
admission을 검증했다. 실제 장치의 TTS 종료 경계, echo false-accept, qualified
interrupt, successor playback 교체와 사용자 스피커 청취, 마이크 10-turn·무음,
Discord 채널 E2E는 아직 검증하지 않았다. source는 Main voice local-mic→Discord를 포함한 실제
channel playback 뒤 exact target에 visible text를 한 번 시도하지만, 실제 channel text
권한·표시·지연, move/reconnect, timeout delivery ambiguity와 shared Discord status heartbeat
투영, memory 삭제 경합은 live 미검증이다. source는 non-null memory exposure position으로
send용 deletion read lease를 다시 획득하지만 exactly-once나 restart replay를 보장하지 않는다.

2026-08-09 source는 validation GET이 terminal/expired session의 동의를 철회한 뒤
철회 전 capability를 반환하던 경합을 닫았다. session snapshot은 consent lock 안에서
한 번만 읽고, 성공·cleanup 실패 응답 모두 현재 consent projection을 그 공개 사본에
붙인다. 읽기 전용 live preflight는 active session 없음, mic physical OFF, output ready,
OmniVoice warmup 완료와 오류 0을 확인했지만 실제 재생·캡처는 수행하지 않았다.

같은 source에서 Discord playback의 prior-source 대기와 `after` callback 유실은 기존
`OMNIVOICE_TIMEOUT_SEC`(기본 180초)로 제한한다. 만료 시 같은 source가 current일 때만
정지하고 기존 failure finalizer가 room lock과 user-only continuity를 회수한다. Local
Bridge의 validation STT 요청도 content-free `validation_bound`만 전달해 STT 완료
로그의 transcript를 길이 marker로 바꾸며 session/attempt ID는 전송하지 않는다. 남은 위험은 source timeout이나
redaction 경계가 아니라 실제 장치·채널에서의 청취 품질과 장시간 동작 증거다.

2026-08-02 source 경계에서는 Local I/O Bridge가 캡처를 시작하기 전에
process-lifetime OS file lock을 fail-closed로 획득한다. Windows Supervisor는
`KILL_ON_JOB_CLOSE` Job Object에 생성한 exact Popen handle을 넣고, 동시에
content-free PID+OS birth identity를 durable write한다. Supervisor crash 뒤에는
같은 PID와 birth identity가 모두 일치하는 이전 자식만 종료하고 부재를 다시
확인한다. PID가 재사용됐으면 신호를 보내지 않으며, identity 손상·starting
ambiguity·잠금 충돌·Job assignment 실패는 새 브리지를 시작하지 않고
`manual_intervention_required`로 닫힌다. launcher도 Supervisor/Bridge PID 일치,
Job ownership과 birth identity 기록을 확인한다. 실제 Windows Job close로 할당된
테스트 자식이 종료되는 통합 테스트는 통과했지만, 이 source를 현재 실행 중인
Supervisor에 배포하거나 실제 마이크를 켜지는 않았다.

2026-08-02 ingress owner는 Fast Control과 Discord text의 LLM/tool/전달 전 claim,
부분 전달 ambiguity, 중복 억제와 restart reconcile을 닫았다. Local Voice도 token을
응답하기 전에 content-free durable reservation을 만들고, 재시작 뒤 Bridge가 제시한
exact token/bridge/turn/text/mode/validation binding으로 reservation을 claim한다.
claim receipt를 검증한 뒤에만 token/replay/follow-up/count를 확정하며 journal 오류나
receipt 불일치는 대화를 시작하지 않는다. recovered duplicate는 replay-only로
폐기되고 자동 대화 재실행은 0이다. 따라서 이전의 `issue response -> Bot restart ->
chat`과 `consume -> claim` crash-loss source 창은 닫혔다. Discord text의
`delivery_succeeded` reconcile은 journal `turnId`와 exact pair를 함께 요구한다.
exact current turn에 user-only tail만 durable한 crash 상태는 assistant/receipt/state를
commit 전에 한 번 완성하고, 다른 turn이나 과거의 동일 문답은 계속 fail-closed다.
복구는 기존 `active_until`을 보존해 expired admission을 다시 열지 않으며, history
비교도 journal과 같은 NFKC 정규화를 써 호환문자 중복·fence 우회를 막는다.

validation mutation과 local admission issue/consume은 attempt별 cross-process OS
lease를 공유하며, lease는 성공·409·503 JSON/stream의 실제 HTTP terminal까지
유지된다. validation LLM은 memory/history/tool 없이 격리되고 assistant 원문은
checkpoint/history/replay 대신 content-free terminal marker만 남긴다. 또한 Bot은
Host HMAC 키를 받지 않은 채 authenticated Bridge fence digest를 Host lease와 durable
consent state에 3자 대조한다. 발급 reservation 전·후와 claim 직전에 consent를 다시
검사하고, 철회 race에서는 exact reservation/capability를 revoke한다.
마지막 검사와 durable reserve/claim은 consent writer와 동일한 `claim_lease.lock`을
사용하고 proof 자체도 capture fence generation에 묶인다. manager restart 뒤 OFF는
process-local 목록에 없는 reserved row까지 scope purge한다.

남은 위험은 source crash-loss가 아니라 실제 운영 증거다. 실행 중 이미지 교체,
Discord reconnect/timeout/redelivery, 로컬 마이크·스피커의 10턴·2회 barge-in·15초
무음과 사용자 청취 확인을 아직 수행하지 않았다. 실제 Windows native와 Linux
container bind mount 사이의 lock coherence도 별도 live 증거가 필요하다.

그러나 CI의 실제 프로세스 smoke는 `main.py`가 기동 가능한지만 확인한다. 마이크
입력부터 STT, 대화, TTS, 로컬 재생까지 계획된 surface별 10턴과 무음 구간을
보장하지 않는다.

Control Page에는 이제 로컬 검증 전용 마이크 동의 임대가 배포됐다. preview
token은 120초·일회용이고, 동의는 세션 연결 전 최대 5분과 연결 뒤 최대 30분으로
제한된다. 마이크 ON과 `captureReady` ACK가 모두 확인되어야 활성화되며 검증
성공·실패·중단, 명시적 철회, 만료, Control Page 종료·재시작에서 OFF를
fail-closed 요청한다. 상태는 제어 메타데이터만 저장하고 음성·transcript를
저장하지 않는다. 현재 실제 마이크는 계속 비활성 상태다.

현재 소스에는 캡처 동의와 별도의 발화 admission 경계도 추가됐다. 정확한 선행
`이블린`, 성공 소비 뒤 45초 follow-up, shutdown/restart·mic·Minecraft 변경의
매회 fresh wake를 요구한다. 10초 일회성 capability는 bridge instance, turn,
canonical forward-text digest, mode와 exact validation attempt에 묶이고 durable
ingress claim과 exact receipt를 먼저 확보한 뒤 user row·planner·LLM·side effect
전에 소비된다. validation은 현재 step의 기대 transcript 판정을 통과한 exact
binding만 발급하며 소비 때 binding을 재검사한다.
validation-bound 소비는 일반 follow-up lease를 열거나 갱신하지 않는다.
공개 Control Page는 `local_bridge` source와 admission/bridge/validation 필드
spoof를 Bot API 프록시 전에 거부한다. 다만 이 소스는 실행 중 서비스에 배포하지
않았고 실제 방 안의 주변 발화·TV/TTS echo·동시 화자 corpus로 false accept와
false reject를 측정하지 않았다.

검증 FSM은 이제 현재 단계와 연결된 interrupt 단계 외 이벤트를 거부하고,
재생 완료 전 청취 확인과 지난 단계 재시도를 허용하지 않는다. STT 불일치,
중복 final/turn/playback/interrupt, 완료·취소 동시 관측, 무음 구간 활동도
즉시 실패다. 30분 TTL은 상태 조회뿐 아니라 preflight 재개, confirm, retry,
abort와 runtime event 기록에서도 먼저 적용하며, mutation 중 만료를 발견해도
로컬 마이크 동의 임대를 즉시 해제한다. 따라서 사전 GET이 없어도 만료된 세션은
새 증거나 동작을 받아들이지 않는다. 누락·비수치·비유한 만료값과 현재 세션/단계에
맞지 않는 명시적 runtime event ID도 fail-closed한다. 로컬 브리지는 첫 threshold
후보에서 playback generation을 고정한다. flush 전에 같은 generation이 해제되고
새 owner·active validation이 없으면 exact leading wake를 확인한 뒤 기존 admission으로
보내며, 다른 generation은 fail-closed한다. clone voice fallback도 단일 playback
owner 안에서 수행한다. Discord streaming뿐 아니라 canned wake와 명시적 기억
응답도 terminal turn summary를 남기며, transcript match, accepted-turn contract,
content-free reply-started/final과 playback failure·terminal outcome을 typed
field로 전달한다. 정상·interrupt 단계는 실제 final reply를 요구하고, 의도적인
barge-source 취소만 현재 세션·단계의 reply-started와 playback-started/cancelled
근거를 정확히 한 번 요구한다. 추가로 active playback manager가 source turn과
private metrics를 대조해 남긴 `qualified_tts_interrupt` typed 근거가 같은 accepted
turn ID와 일치해야 한다. 이때 완료되지 않은 답변을 final로 합성하지 않으며,
로컬·Discord 모두 답변이 실제로 먼저 완료된 뒤 취소됐다면 final 근거를 별도로
보존한다. 다른 취소, 오류·빈 답변·재생 실패·불완전 terminal은 positive
event보다 먼저 단일 실패로 닫으므로 barge-source가 늦은 오류보다 먼저
통과하거나 단계가 무기한 대기하지 않는다. 새 Discord 이미지의 전체 음성 회귀와
Bot API의 검증/API/runtime/UI 회귀, 호스트 focused 회귀는 통과했지만 이는 합성
입력과 mock 장치를 사용한 계약 검증이다.

무음 단계의 마지막 timer-only false positive도 닫았다. Local Bridge는 bridge,
mic, `captureReady`의 content-free heartbeat를 현재 silence attempt에 전달하고,
Discord는 선택된 guild/channel의 live gateway, connected, listening 상태를
전달한다. 15초 전체에서 첫/마지막 신선도와 최대 gap(Local 2초, Discord 3초)을
만족해야 하며 증거 누락, false readiness, 중간 단절, retry 이전 attempt와
out-of-order 샘플은 통과 근거가 아니다. 실제 방과 실제 Discord 채널에서 이
연속 heartbeat가 유지되는지는 아직 live 검증하지 않았다.

추가 source P0 감사에서는 세 가지 admission/readiness false-positive도 닫았다.
로컬 barge-in에 화자 검증이 요구되면 verifier가 정확히 `matched=true`를 반환한
경우만 중단을 허용하고, 미등록·too-short·unavailable·오류처럼 판정이 없는 입력은
`speaker_verification_unverified`로 거부한다. 로컬 출력은 `default` 문자열이나 TTS
bytes만으로 준비됐다고 보지 않고, 선택/default 장치 조회와 24 kHz mono `int16`
출력 설정의 비가청 검사를 모두 통과해 `outputReady=true`여야 한다. Discord
gateway도 cached `bot.user` 대신 현재 `bot.is_ready()`, not-closed와 live
`bot.ws.open`을 모두 만족한 연결만 ready heartbeat로 게시한다. 이 검사는 실제
스트림을 열거나 소리를 재생하지
않았으므로 장치 독점, 드라이버 단절, 실제 Discord 송수신 성공의 live 증거는
아니다.

추가 인과성 감사에서는 retry·abort 뒤 오래된 작업이 실제 출력으로 이어지는
경계도 닫았다. local/Discord ingress, STT 전후, interrupt debounce, 답변 dispatch와
실제 장치·Discord playback 직전에 같은 session/step/private attempt binding을
다시 확인한다. 검증 시작 전에 큐에 들어온 무표식 작업도 실행 시 해당 surface의
검증이 활성 상태면 폐기하므로 무음 단계가 관측 밖 발화와 동시에 통과하지 않는다.
paired interrupt 실패는 source 단계가 건너뛰지 못하며, 정상·interrupt 단계는
허용되지 않은 추가 interrupt evidence가 하나라도 있으면 통과하지 않는다.

재생 소유권은 generation으로 격리했다. 오래된 Discord teardown은 await 뒤 새
재생을 정지하거나 새 registry/metric을 지울 수 없고, source·voice client·worker
종료 중 하나라도 실패하면 성공 취소나 qualified interrupt로 기록하지 않는다.
로컬은 첫 장치 write 전에 attempt lease를 잡고, 부분 write 또는 stale 판정 뒤에는
전체 답변 fallback을 재생하지 않는다. 따라서 취소된 원본과 대체 응답의 동시 재생,
부분 재생 뒤 전체 답변 중복, 실제 재생 전 positive interrupt evidence를 막는다.
추가로 실제 첫 PCM write 성공 뒤 worker terminal 전에 exact binding에 stop이
원자적으로 접수돼 고유 token을 받은 경우만 인정한다. stop control·worker 종료는
단일 제한 시간 안에 끝나야 하며, 자연 종료 scheduling gap, timeout, 예외, 명시적
`False`, 중복 stop, stale generation/attempt는 context와 positive lease를 만들지
않는다. 로컬 playback 예외도 고정 실패 코드와 타입만 status/log에 남긴다.

검증 중 raw audio는 debug capture가 켜져 있어도 저장 큐에 들어가지 않는다.
STT·wake·reply 로그도 원문 대신 문자 수만 남기며 공개 bridge status에서 private
attempt token을 제거한다. 여기서 `transcriptStored=false`는 검증 event/report와
운영 로그의 비저장을 뜻한다. 사용자가 실제로 수행한 검증 대화가 일반 대화
history/continuity 정책을 따르는 것은 별도 계약이며, 보고서에는 원문이 복제되지
않는다.

Host Supervisor는 검증 실행 중 Local I/O Bridge가 비정상 종료되면 같은 attempt를
자동 재시작해 이어가지 않는다. 현재 단계를 고정 오류로 실패시키고
`manual_intervention_required`를 보고하므로, preflight 복구 뒤 새 attempt 또는 새
세션으로 시작해야 한다. 복구된 session은 canonical suite·surface·11-step 행렬과
attempt 구조를 다시 검증하고, session ID와 event/report 경로는 허용 문자와
artifact root containment를 모두 통과해야 한다. 청취 확인도 JSON boolean만
받으므로 문자열 `"false"` 같은 값은 성공 확인으로 변환되지 않는다. 공개 v1의
attempt 없는 confirm은 최초 attempt에서만 호환하며, retry 뒤에는 현재 attempt
revision을 명시하지 않거나 이전 revision을 보내면 상태를 바꾸지 않고 거부한다.

소스 계약에서는 reply gate를 통과한 사용자 발화 뒤 Discord 연결 부재, 빈 답변,
LLM/TTS 전달 실패와 Local Bridge의 failed·partial·cancelled playback ACK가 나도
사용자 row만 즉시 continuity checkpoint에 한 번
보존한다. 가짜 assistant row, memory write, search follow-up은 만들지 않고 공개
voice 상태와 turn summary에는 고정 오류 코드·예외 타입만 남긴다. Main/Fast의
다음 prompt는 restart restore와 cross-surface merge 뒤에도 history가 `user`로
끝나면 content-free `conversation.unanswered-user.v1` 규칙을 받아 미응답 문맥임을
명시하고, 추적에는 boolean만 남긴다. 다만 실제 Discord 연결 단절과 스피커/TTS
장애를 일으킨 뒤 재시작해 다음 응답이 이 미응답 발화를 이어가는 live
failure-injection은 아직 수행하지 않았다.

Local Bridge failure finalizer는 ingress의 exact `turnId`와 assistant hash를 먼저
검사하고 user-only checkpoint를 durable commit한 뒤 journal을 지운다. 중간 crash는
exact current turn과 마지막 user row가 함께 맞을 때만 journal을 정리하며 assistant
원문·receipt·action을 복원하거나 재실행하지 않는다.

다음 조치: 사용자가 Control Page의 “검증 세션 동안 마이크 허용”을 직접 확인한
뒤 먼저 dormant 상태의 주변 발화·중간/유사 호출어·고영향 follow-up이 실제
side effect 전에 거부되는지 확인한다. 이어 로컬/Discord 10턴, barge-in, 무음
구간과 연결/TTS failure-injection을 실행하고 비식별 보고서를 기록한다.

## P1 — 과거 기억의 누락된 파생 provenance와 재합성 지연

삭제 preview는 현재 `derivedFrom` graph의 영향 목록과 fingerprint를 제공한다.
유일한 근거를 잃는 파생 note는 content-free tombstone으로 연쇄 철회하고,
다른 살아 있는 근거가 있는 note와 그 하위 파생은 recall/FTS/vector/graph/
hot-context에서 fail-closed quarantine한다. 새 프로세스도 같은 상태를
재구성한다. Sub-LLM 재합성은 기존 파생 본문과 삭제 source를 입력하지 않고
남은 source note만 사용한다.

남은 위험은 이 판정이 note front matter의 `derived_from` 선언에 의존한다는
점이다. 과거 importer나 수동 note가 실제 근거 관계를 기록하지 않았다면 내용이
유사하더라도 자동 연쇄 철회 대상임을 증명할 수 없다. 또한 Sub-LLM이 꺼져 있거나
상위 source가 quarantine이면 multi-source note는 안전하게 격리된 채로 남아
자동 회상에 사용되지 않는다. 재합성 대기가 남으면 일반 900초 유지보수와
분리된 60초 retry gate를 기록해 다음 비실시간 유지보수 기회에 다시 시도한다.

2026-08-08 raw source ID와 ledger ID의 정렬 순서 차이로 의미가 같은 derivation
revocation state를 한 번 더 쓰던 비결정성은 닫혔다. reader의 역매핑 결과와 새
`directSourceIds`를 모두 raw ID 기준으로 중복 제거·정렬해 비교하고, 반복 sync의
파일 byte와 `updatedAt`이 그대로임을 고정했다. `contentFree` 없는 과거 raw-ID
artifact도 첫 read에서 기존 lock과 durable writer로 즉시 canonical content-free
형식으로 교체하며 실패는 integrity 오류로 닫힌다. 이 절에 남은 P1은 위의 과거
파생 provenance 누락과 재합성 지연이지 revocation 재쓰기 안정성이 아니다.

Control Page의 근거 감사는 legacy/과거 semantic note의 exact source ref와
evidence hash만 대조한다. 본문 유사도나 LLM 추측은 사용하지 않으며 교차 검증,
단일 신호, 모호한 후보를 분리한다. content-free 보고서에는 note ID와 판정만
저장하고, 사용자 수정으로 분리된 관계와 cycle 후보는 제외한다. 운영 상태에는
quarantine 대기 수, 재합성 가능 수와 가장 오래된 대기 시간을 표시한다.

`verified`/`review` 후보는 별도 2단계 확인으로만 연결한다. 일회용 token은 target,
모든 source hash와 전체 graph fingerprint에 묶이며 120초 뒤 만료된다. 적용 전
어느 node라도 바뀌면 아무것도 쓰지 않는다. `ambiguous`와 보호 대상은 적용할 수
없고, 새 consolidation/recomposition write는 `derived_from` 없이는 거부된다.

source type·note type·age별 provenance coverage와
`memory_derived_from_required` 거부 수는 content-free 지표로 관측한다.
명시적 신호가 없거나 현재 source와 맞지 않는 과거 note는 자동 추론하지 않고
사용자가 공개·비격리·접지된 source를 직접 선택한다. 이 경로도 120초 일회용
preview/apply이며 target/source/full graph가 바뀌면 아무것도 쓰지 않는다.

기존 관계도 이제 별도 2단계 preview/apply로 relink하거나 명시적 빈 source
배열로 unlink할 수 있다. 제거한 ID는 `origin_derived_from`에 남고, 가장 최근
relink/unlink만 현재 revision과 관계가 정확히 일치할 때 별도 append-only
변경으로 undo할 수 있다. token은 target/source hash, current/proposed
source·origin ID와 전체 graph fingerprint에 묶여 어느 node라도 바뀌면
fail-closed한다.

write-ahead correction journal은 note/source ID, revision, action과 시각만
저장한다. prepared를 `fsync`한 뒤 Markdown을 원자 교체하고 committed를
기록한다. 파일 교체 뒤 commit event 전에 죽으면 새 프로세스가 note의 change
ID/revision/source/origin과 정확히 일치할 때만 committed를 복구한다. UI와
API는 body, path와 content/source/evidence hash를 공개하지 않으며 모든
mutation은 CSRF와 별도 사용자 확인을 요구한다.

journal v2는 각 event의 sequence, 이전 event hash와 현재 event hash를 잇고,
별도 durable chain head로 꼬리 삭제도 감지한다. 기존 v1 prefix는 정확한 raw
line hash로 첫 v2 event 또는 sequence 0 head에 고정한다. Windows byte-range
lock/POSIX `flock`과 프로세스 내부 owner table이 correction 전체를 단일
writer로 만들며, content-free marker는 crash 뒤 stale owner 회수를 기록한다.
chain/head 손상이나 writer 경쟁은 note와 token을 건드리기 전에 fail-closed하고
API는 HTTP 503을 반환한다. journal append 뒤 head 교체 전에 중단된 경우에만
유효한 chain prefix를 같은 writer lease 아래 복구한다.

live prompt 경계도 이제 구조적 receipt를 남긴다. Vault recall과 pinned
hot-context에서 실제로 모델에 제공한 note ID, memory version, retrieval mode,
source-type 집계만 `memory.context-receipt.v1`에 기록하고 본문·제목·경로·
transcript는 넣지 않는다. stale memory version, 삭제/파생 상태 불일치,
note ID가 없는 과거 hot-context는 prompt에서 제외한다. Discord/Main turn
summary와 Fast Control 일반·stream 응답은 이 receipt를 노출하며 “제공됨”과
“모델이 실제 사용함”을 구분한다.

2026-08-08 감사에서 exact note ID만 맞으면 필수 provenance field가 손상돼도
recall receipt가 `attributed`가 되고, 정상 pinned hot-context의 ID가 손상 recall
본문까지 귀속시키는 경로를 확인해 닫았다. cache와 receipt가 같은 구조 validator를
사용하고 mixed context는 supplied ID를 비워 최종 prompt에서 전체 보류한다. 남은
이 절의 P1은 과거 기억의 근거 누락과 재합성·사용자 확인 품질이지 이 승격 경로가
아니다.

새로 저장되는 raw 대화 row는 content-free stable evidence ID와 source turn ID를
보존한다. person-bound turn은 guild raw JSONL 중복을 만들지 않고 room/person/사용자
결합 session에 기록한다. trusted person-bound 조회는 기존 guild raw와 guild
`vault_raw`를 prompt selection·count·receipt 전에 제외해 다른 channel의 principal로
퍼지는 guild fallback을 닫는다. 현재 ingress room 공유와 person/session continuity는
유지하고, 기존 row의 owner를 내용·guild/session/evidence로 소급 추론하지 않는다.
assistant raw는 vault note 삭제 현재성 receipt가 없으므로 계속 전역 보류한다.

이 경계는 current room permission/history authorization을 다시 검증하지 않는다.
같은 room의 공유 raw, `person_key`가 없는 local/legacy guild/room 및 요청된 session
fallback, 기존 guild raw와 일자별 mirror의 disk·legacy coverage 잔존은 호환성 위험으로
남는다. 기존 guild reset과 일자별 note 관리 동작은 유지하지만 해당 artifact를 자동
owner migration하지 않는다. 실제 다인 Discord channel과 local legacy 흐름은 live로
검증하지 않았다.

self-identity review queue도 principal scope가 없는 local artifact다. owner 없는 queue copy는
self-identity runtime-state hint로 읽지 않고 renderer는 reviewed profile만 사용하지만, 원문과
label은 기존 queue·사람용 export·decision metadata에 남을 수 있고 같은 턴의 별도
scope-authorized history/raw 의미도 유지된다. 실제 사람 검토·승격 workflow와 오래된 queue
정리는 live로 검증하지 않았고, 후보를 자동 owner 추정·migration하지 않는다.

새로 생성되는 rolling summary는 본문 hash에 묶인 content-free sidecar에 자체
파생 evidence ID와 실제 Summary LLM 입력 evidence/turn ID를 기록한다. 새
facts/questions도 같은 입력 계보와 별도 파생 ID를 hot JSONL과 mirror에
보존한다. context-size compact 재시도에서는 첫 시도의 최근 row를 source로
남기지 않고 compact prompt에 실제 포함된 summary와 현재 턴만 연결한다. sidecar
본문 hash가 다르거나 row provenance가 손상되면 receipt는 이를 근거로 인정하지
않고 fail-closed한다. 이 저장 계보만으로는 note 삭제 현재성을 증명할 수 없으므로
stored summary/facts/questions도 prompt consumer에서는 현재 전역 보류한다.

배포 전후의 stored derived layer와 assistant raw는 deletion-current lineage가 없어
사용 정책을 결정적으로 닫았다. 내용 유사도나 시간 인접성으로 소급 연결하지 않고
layer 수집 단계에서 제외한다. 다른 `partial|unattributed` 결합 문맥도 최종
Main/Fast 경계에서 전체 보류한다. prompt에는 구체 내용을 포함하지 않는
`MEMORY_WITHHELD_RULE`만 남아 필요하면 사용자에게 정보를 다시 말해 달라고
요청한다. producer의 `groundingState`도 근거 ID/count로 재계산하며, 최종
1,680자 경계에서 문맥이 잘리면 같은 본문 보류 정책을 적용한다. receipt와 turn
summary는 `state=withheld`, `promptMemoryWithheld`와 content-free 보류 count만
기록하고 supplied evidence는 0으로 비운다.

layer 원문을 복제한 legacy mirror, daily conversation note와 semantic derived note도
별도 vault recall 우회가 되지 않도록 live recall/hot context에서 제외한다. cache
schema v3와 hot-context policy marker가 이전 attributed cache를 무효화한다. 저장과
감사는 유지되지만 자동 파생 기억의 live recall은 deletion-current lineage가 생길
때까지 일시적으로 꺼져 있고, exact owner가 검증된 explicit user note와 system note
recall만 유지된다.

저장 legacy coverage는 이제 `memory.legacy-context-coverage.v1`로 측정한다.
summary/raw/fact/question을 prompt와 같은 evidence 규칙으로 재검사하고
kind/scope/storage별 전체·attributed·확인 전용 수만 감사 API·저장 보고서·UI에
노출한다. ID, scope key, 경로, 본문과 transcript는 내보내지 않으며 hot/일자별
mirror가 함께 셀 수 있어 고유 턴 수나 실제 prompt 선택 수는 아니라는 한계를
명시한다. 현재 프로젝트 `bot_memory`의 읽기 전용 측정은 scope 3개, 저장 legacy
항목 0개로 `empty`였다. 실제 사용자 기억은 수정하지 않았다.

현재 사용자 확인 발화를 새 근거로 만드는 경로는 Fast Control과 accepted
Discord text/voice에 연결됐다. 성공은 저장 후 card의 직접 사용자 source,
단일 turn ref, 본문 evidence hash, confirmed timestamp와 recall eligibility를
모두 재검사하며, 손상 provenance는 content-free 실패로 닫힌다. 격리된
저장→attributed prompt 회수→2단계 삭제→동일 query 비회상 lifecycle도
통과했지만 실제 사용자 기억에는 쓰기·삭제를 수행하지 않았다.

2026-08-12에는 attribution만 유효하면 A의 direct-confirm note가 다른 Discord
guild/user와 Fast local prompt에도 들어가던 principal 격리 누락을 temp vault에서
재현해 닫았다. 새 v2 note는 exact guild/person 또는 별도 Fast local owner를 opaque
scope로 저장하고 cache·FTS/vector/graph/read-only/render 전 경계에서 재검사한다.
scope token은 public card/receipt/graph에 나오지 않는다. owner 없는 v1·marker-only
legacy note는 관리·삭제 surface에 남지만 자동 prompt에는 들어가지 않으며 기존
session으로 owner를 자동 추정하지 않는다. 남은 위험은 실제 configured Discord↔Fast
handoff와 재확인 UX를 live 검증하지 않았고, legacy note가 다시 필요하면 현재
authenticated principal이 새 `/remember` 근거를 제공해야 한다는 가용성 비용이다.

저장 뒤 손상도 recall 시점에 다시 닫는다. 새 note marker와 기존 tag/path를
함께 사용해 user-confirmed 계열을 식별하고, 무결성이 깨지면 index·cache·hot
context에서 제외한다. Control Page는 본문을 숨기지 않고 사용자가 검토·편집·
삭제할 수 있게 두되 회상 불가와 고정 blocker를 표시한다. 편집은 새 user-edit
근거를 만들므로 손상된 원본 metadata를 그대로 신뢰해 복구하지 않는다.

Control Page의 일반 카드 확인도 exact note hash에 결박했다. 사용자가 읽은
`sourceHash` 없이 확인할 수 없고, lock 안의 재확인에서 revision이 다르면 아무
상태도 쓰지 않는다. sidecar hash가 현재 note와 다르거나 없는 과거 확인은
`stale`로 강등하며 숨겨진 legacy/internal note와 무결성이 손상된 explicit
confirmation note는 서버에서도 거부한다. 이 표시는 현재 내용을 사용자가
검토했다는 상태일 뿐 source/evidence를 만들지 않으므로 과거 ungrounded 기억을
attributed로 승격하지 않는다.

남은 위험은 이후 import/복원으로 확인 전용 legacy 항목이 생겼을 때 이를 사용자
확인만으로 과거 source에 소급 귀속할 수 없다는 점이다. 안전한 확인 흐름은 기존
row를 고치는 backfill이 아니라 현재 확인 발화를 새 turn evidence로 가진 새 기억을
만들고, 원래 항목은 계속 미확인으로 보존하거나 별도 철회하는 방식이어야 한다.
본문 보류로 근거 없는 legacy 내용을 모델이 단정하는 직접 경로는 닫혔다. 다만
실제 대화에서 사용자가 필요한 정보를 자연스럽게 다시 제공하도록 이끄는 확인
질문의 품질과, 이후 새 직접 사용자 근거로 저장되는 흐름은 아직 평가해야 한다.

남은 위험은 coverage와 correction이 구조적 근거 연결만 다루며 기억 내용이나
사용자의 선택이 사실임을 보증하지 않는다는 점이다. correction journal/head에는
관계 연속성 키와 분리된 기억 전용 HMAC과 외부 monotonic anchor를 추가했다.
명시적 one-shot bootstrap 뒤에는 HMAC 변조, signed past replay, journal/head
전체 삭제를 fail-closed하고 journal→head 및 head→anchor의 한 단계 commit lag만
writer lease 아래 복구한다. 다만 이 보장은 memory 파일과 key/anchor 경로의
권한이 분리된다는 trust boundary에 의존한다. filesystem 관리자가 key를 읽거나
외부 anchor도 함께 되돌릴 수 있으면 로컬 파일만으로는 감지할 수 없고 OS lock도
단일 host/shared filesystem의 writer 배제일 뿐 분산 합의가 아니다.
실제 vault에는 현재 derived relationship이 0개라 운영 데이터에 대한 live
relink/unlink/undo는 비파괴 원칙상 실행하지 않았다. 또한 Sub-LLM이 꺼져
있거나 상위 source가 quarantine이면 multi-source note는 안전하게 격리된다.
pending 전용 retry는 기본 60초로 줄었지만 전체 vault 유지보수는 음성 hot
path와 GPU 경합을 피하려고 `realtime` 턴에서 실행하지 않는다. 따라서
음성만 계속되는 세션의 재시도는 다음 startup 또는 비실시간 기억 유지보수
기회까지 기다릴 수 있다.

다음 조치: 실제 derived 기억이 생기면 correction preview의 설명 가능성,
relink/unlink/undo 결과와 journal 복구를 운영 데이터 복제본에서 검증한다.
key/anchor 경로까지 장악하는 관리자를 위협 모델에 포함할 때는 TPM 또는 원격
append-only audit sink를 추가하고, coverage bucket과 forward rejection 추세가
실제 품질 신호인지 함께 측정한다.

## P1 — 삭제 journal 외부 rollback 보호와 read-lease 가용성

삭제 journal의 malformed/partial row 무시 문제는 닫혔다. strict v1/v2 parser,
legacy raw-prefix pin, sequence/hash chain, durable head, OS single-writer lease와
정확한 1-event crash recovery가 적용됐다. Windows write-through replace와 POSIX
parent-directory fsync 뒤에만 durable commit으로 인정하고, source Markdown은
tombstone commit 뒤 content-free stub으로 먼저 durable redaction한 다음 unlink한다.
direct·cascade redaction이 실패하면 source를 unlink하지 않고, 최종 재조정 뒤에도
cleanup이 남으면 기존 content-free `memory_delete_cleanup_required`로 반환한다.
recall/context/Control Page/provenance/Sub-LLM 경계도 삭제와 선형화됐다. 전체
legacy+vault context에서 캡처한 root-bound position은 Main non-stream, Voice
stream/legacy response와 Fast Control의 실제 HTTP sink에서 request 시작 전에
재검증하며 응답 소비까지 lease를 유지한다. 따라서 build 뒤 삭제가 먼저
commit되면 HTTP POST 자체를 시작하지 않는다.

남은 첫 번째 위험은 외부 anchor가 기본 필수가 아니라는 점이다. key와 외부
anchor가 설정되면 signed head와 `memory-deletions.json`이 journal+head의 과거
쌍 replay를 탐지하지만, 외부 anchor까지 검증되지 않으면—기본 미설정 상태와
key-only 상태를 포함해—local chain은 손상·단독 truncation만 탐지한다. journal과
head를 함께 같은 과거 상태로 되돌리는 관리자 공격은 로컬 두 파일만으로 판정할
수 없다. Control Page snapshot과 삭제 preview는
`memory.deletion.integrity.v1.rollbackProtected=false`와 경고를 표시하므로 이를
보호된 영구 삭제로 오인하지 않는다. 실제 사용자 memory root에는 비파괴 원칙상
key/anchor bootstrap이나 삭제를 실행하지 않았다.

disposable-replica verifier는 실제 memory root를 받지 않고, 별도 빈 scratch/anchor에서
unsigned 이력의 strict 거부→one-shot bootstrap→strict fresh process→signed 과거 pair
replay 거부→정상 pair 복원을 검증한다. 다만 loader와 이 검증기는 경로 분리까지만
검사한다. POSIX mode/uid, Windows DACL/owner와 Docker secret·bind mount의 실제 권한은
아직 증거가 없으므로 replica 결과도 `operationallyVerified=false`다.

빈 deletion ledger는 재시작이나 read만으로 signed head/anchor를 생성하지 않는다.
첫 승인 삭제가 signed `memory-deletions.initialized.json` witness를 anchor보다 먼저
내구 기록하고 ledger를 초기화한다. 기존 unsigned 이력 채택만 one-shot bootstrap을
명시적으로 조율해야 한다. witness가 남아 있는데 journal/head/anchor가 사라지면
완전 미초기화로 오인하지 않고 integrity failure다.

장기 exposure·HTTP response·deletion-only outbound guard는 이제 같은 lock file의
shared reader lease를 사용한다. Windows는 `LockFileEx` shared byte-range, POSIX는
`LOCK_SH`, process 안에서는 thread+async-task별 reader owner 집합으로 겹친 조회를
분리한다. reader끼리는 공존하고 삭제 writer는 마지막 reader가 끝날 때까지
fail-fast한다. 정상 lock 경합은 무결성 손상과 분리된 고정
`memory_deletion_journal_busy` 503으로 8798·8799와 Control Page에 전달한다.

index sync, retrieval-cache migration/write, cognitive·memory artifact commit처럼
실제 쓰기가 섞인 경로는 계속 exclusive lease를 사용한다. fresh recall은 정상일 때
그 writer 경로를 유지하되, 진입 전 exact busy만 no-repair shared reader로 전환한다.
fallback은 immutable read-only SQLite와 현재 Markdown을 다시 대조하고 cache,
FTS/vector/graph/hot/legacy/disk cognitive를 사용하지 않는다. 따라서 이미
materialize된 response뿐 아니라 fresh Main/Fast recall도 reader와 공존하지만,
그 turn은 `indexFresh=false`이며 아직 index되지 않은 외부 note 또는 note ID 정렬 기준
500개 read-only 후보 밖의 indexed note를 잠시 놓칠 수 있다.
DB가 없거나 손상·구버전이고 journal repair가 필요하면 복구 쓰기를 하지 않고
content-free unavailable로 닫는다.

semantic consolidation과 derivation recomposition의 Sub-LLM 구간은 shared reader로
일반 조회와 공존한다. 다만 삭제·편집 writer는 호출당 최대 45초 동안 retryable
busy가 될 수 있고 recomposition은 기본 최대 4회 반복한다. Control Page mutation은
pre-entry writer busy만 최대 2초 비동기 대기하며 operation·guard-exit·result-shaped
busy와 confirm token 작업은 재실행하지 않는다. 2초를 넘는 경합은 기존 exact 503과
수동 재시도로 남는다. 이미 commit된 edit·provenance mutation의 cleanup 503은 UI가
고정 public code로 구분해 4개 snapshot을 무효화하고 강제 재조회한 뒤 적용 사실과
자동 재시도 금지를 표시한다. 남은 공백은 이 busy 전이의 live 확인과 64 MiB journal
상한의 검증 가능한 rotation이다.

삭제도 direct·cascade source의 durable redaction 성공 뒤에만 unlink한다. 전체
tombstone source의 최종 재조정에도 cleanup이 남으면 cleanup-required로 닫고,
source redaction/unlink가 끝난 뒤 index/hot-context cleanup이
integrity-class 오류를 내면 ledger 최종 재검증이 성공한 경우에만
`memory_delete_cleanup_required`, `tombstoned=true`로 보고한다. 실제 ledger 손상은
최종 guard에서 다시 raise되어 sibling field 없는 exact integrity 503으로 유지된다.

generic JSON LLM helper는 경계 없는 non-memory 호출과 required memory 호출을
구분한다. 현재 cognitive-state, route planning, memory writeback은 builder에서
typed deletion position을 캡처해 primary/compact retry와 실제 HTTP sink까지
명시적으로 전달하고, 파생 상태 write 전에도 같은 position을 재검증한다.
search/tool 전용 sink는 vault memory context를 입력으로 받지 않는다. Core Python의
recognized transport syntax는 보호된 memory-capable call site, 두 Sub-LLM shared
guard caller, raw `urlopen` transport와 welcome/warmup isolated sink를 exact inventory로
고정한다. 이 검사는 aiohttp가 없어도 실행되는 drift alarm이며, 실제 stale boundary가
request factory를 0회로 막는 증거는 Main·공용 exposure/JSON·Sub-LLM 동적 회귀가 맡는다.

이 inventory는 범용 HTTP/SDK dataflow 분석이 아니다. alias·새 wrapper·비정형 transport,
간접 JSON caller와 isolated allowlist의 payload provenance를 자동 증명하지 않는다. 새
Core sink는 canonical wrapper를 사용하고 inventory review를 거쳐야 한다.

2026-08-09 기본 Mindcraft history는 bounded process-local turn만 사용한다.
`load_memory=false`이고 Compose가 `bot_memory/mindcraft`를 mount하지 않으므로 재시작
복구나 cross-process persistence를 제공하지 않는다. 기존 legacy memory/archive 파일은
읽거나 현재 generation으로 rebase하거나 삭제하지 않는다. planner recovery도 디스크에
저장하지 않는다. Google translation 경로는 local identity 변환으로 바뀌었고 Node child의
stdout/stderr는 `DEVNULL`이지만, 이미 존재하는 legacy data와 log는 그대로 남아 있다.
Player chat/whisper ingress는 빈 `only_chat_with`를 deny-all로 해석하고 exact configured
name만 허용한다. self-prompt/system autonomous path는 이 ingress gate를 거치지 않는다.
이는 source-level 경계이며 실제 Minecraft chat/whisper 세션에서는 검증하지 않았다.

process-local generation exposure는 turn 첫 await 전부터 LLM 요청·결과와 실제 final
route/action sink까지 clear를 busy로 막는다. inter-agent ingress/outbound pause·classifier와 예약 timer queue도
같은 경계를 사용하며 성공한 clear는 queue/monitor를 폐기한다. `!clearChat`은 turn,
summary placeholder, recovery/action-mode, goal-manager의 raw execution/gate 상태와
self-prompt·`last_sender`를 비운다. goal/status artifact에는 raw command·result·reason이나
observation argument 대신 enum code와 count/boolean만 기록한다. Node writer와 Python
status owner는 legacy/free-text 상태를 재사용하지 않고 runtime error도 fixed code로 투영한다.
Node의 direct local/router/Codex model endpoint는 기본 경로에서 제거했다. planner는 전용
token-file authenticated Bot API broker만 호출하고, broker가 fixed upstream을 선택해 공용
conversation filter와 `memory_exposure_request`를 통과시킨다. 현재 core memory 입력이 없는
broker request projection row는 strict `not_used` receipt이며 반환된 다른 state는 거부한다.
lease는 Node의 frame parse/validation 뒤 `delivered|discarded` ACK까지 유지된다. 이 ACK는
history append·chat route·world action 성공 증거가 아니며, process-local generation exposure만
실제 final route/action sink까지 별도로 이어진다.

이는 durable rollback-safe clear, bound-receipt restart history나 legacy cleanup을 뜻하지
않는다. 기존 memory/archive와 과거 log의 삭제·이관은 사용자 승인 migration으로만
수행한다. Docker image build, 실제 Minecraft 연결·행동과 live clear도 검증하지 않았다.

recovery outcome은 exact history snapshot을 key로 한 process-local one-shot issuance로만
소비한다. 다른 target, 수동 명령, concurrent turn과 재사용 receipt는 plan을 진행하지 못하고
token·raw command는 durable projection에 남지 않는다. 이는 restart restore 계약은 아니다.
`!clearChat`은 자율 목표 자체를 영구 중지하지 않으며 그 의도에는 `!endGoal`을 사용한다.

세 번째였던 일반 대화 receipt 미전파 위험은 이 branch에서 닫혔다.
compact `bound|not_used|unattributed` receipt를 process-local history에만 두지
않고 durable checkpoint, restart restore, session·cross-surface merge까지 전파한다.
Full receipt의 `not_used`도 exact no-memory schema와 빈 ID/count에서만 만들며,
metrics 누락·null·non-mapping·모순 필드와 손상 compact state는 예외 없이
`unattributed`로 강등해 기억 의존 assistant row를 보존하지 않는다.
Discord 명령의 전달 완료 답변도 `not_used`로 기록해 필터가 assistant 행을
제거하고 직전 명령을 미응답으로 되살리던 경로를 닫았다.
저장 기억을 요청하지 않은 Fast Control prompt도 빈 context 조정 뒤
`state=not_requested`, `groundingState=not_requested`를 함께 유지해 compact
`not_used`가 된다. 정상 응답을 `unattributed`로 잘못 강등해 음성만 재생하고 공개
텍스트 행을 숨기던 경로를 닫되, 손상 receipt의 fail-closed 제거는 유지한다.
Main/Fast/Voice/Search/tool과 production autonomy가 history를 재사용하기 전에 누락·손상·
`unattributed`·stale version·tombstoned-note assistant row를 fail-closed로
제거하고, persona/cognitive/router의 history-derived 상태도 strict receipt가
없으면 재사용하지 않는다. autonomy는 observation부터 plan·execute·persist까지
typed exposure를 유지하고, 늦게 읽은 callback도 자체 guard 안에서 side effect를
완료한다. durable/returned state에는 raw history·summary·text와 private
assistant-history 의사결정 신호를 남기지 않으며 후속 대화에는 compact receipt를
전파한다. 재시작 때 receipt 없는 autonomy cache는 모두 버린다. Minecraft 활성
환경의 plan/cursor는 대화 history가 아니라 typed world observation으로만 결정되므로
삭제 guard를 유지한 채 보존한다.
일반 autonomy loop나 cognitive refresh가 살아 있으면 guild reset은 continuity와
파일 삭제 전에 고정 코드로 거부한다. legacy runtime cache는 load 즉시 content-free
상태로 다시 저장하며 note 삭제도 receipt 없는 autonomy cache를 정리한다.
proactive open-question queue는 deletion-current receipt가 없어 선택·mark를 전역
fail-closed했고 새 raw/ask text 복제도 저장하지 않는다. 과거 queue/pending 원문은
index sync에서 제거한다. stored summary/fact/question과 assistant raw도 같은
lineage 공백 때문에 prompt에서 보류하지만, person-bound 요청의 room/person/session
또는 person key 없는 호환 경로의 guild/room과 요청된 session layer에서 evidence 검사를
통과한 exact user raw와 모델이 현재 답변에 직접 넣은 명시적 질문은 유지된다. 기능 재활성화는
exact receipt와 tombstone 검증이 생긴 뒤에만 가능하다.
Main/Fast Control의 actual HTTP write와 Discord/TTS playback handoff가 같은 deletion
position을 재검사한다. TTS-bound Local Bridge reply는 HTTP EOF를 성공으로 쓰지 않고
software-playback ACK 뒤 server-side currentness를 재검증해 commit한다. receipt와 boundary 메타데이터에는 대화
원문·transcript·raw audio를 저장하지 않는다.

공개 8799도 8798 응답을 완전히 읽은 뒤 content-free handoff를 strict parse하고
browser `prepare` 직전에 exposure를 다시 검증한다. state 재직렬화와 실제
`write_eof`까지 새 lease를 유지하며 내부 handoff header/note ID는 공개하지
않는다. Control Page text/search는 voice와 같은 공용 validator로 receipt와
exposure의 state/version/note ID 불일치를 assistant persistence·continuity·TTS
전에 거부한다.

따라서 이 항목에 남은 삭제 경계 위험은 receipt propagation, materialize된 response의
exclusive lease나 fresh recall busy가 아니라, fallback turn의 일시적인
`indexFresh=false`, external anchor host 권한 미검증, 2초 admission을 넘는 Sub-LLM
shared phase의 삭제·편집 writer 경합과 64 MiB journal rotation이다. 실제
마이크·스피커·Discord 10턴 재생은 이 정적 계약의 완료 증거가 아니며, 별도의 실제
음성 하드웨어 E2E 위험으로 계속 남는다.

다음 조치: 별도 service identity의 host ACL/owner와 Docker key read-only·anchor
read-write mount를 실제 복제 배포에서 확인해 `operationallyVerified` 공백을 닫는다.
Sub-LLM shared phase의 2초 admission과 timeout 503을 실제 UI 전이로 검증하고,
chain과 외부 anchor를 잃지 않는 checkpointed rotation도 함께 설계한다. fallback
빈도와 `indexFresh=false`가 실제 사용자 recall 품질에 미치는 영향은 live telemetry
없이 추정하지 않는다.

## P1 — UI 접근성 corpus·live 행동 검증 미완성

Windows Host Vision Bridge에 읽기 전용 Windows UI Automation Control View를
연결했다. 허용 control type과 최대 120개/5초 freshness를 고정했고, foreground
title/class가 별도 관측과 일치할 때만 사용한다. Edit·Document·Value/Invoke
pattern·PID·경로·명령행은 읽지 않으며 runtime ID는 외부로 내보내지 않고
단방향 element ID로 바꾼다. 버튼·메뉴·탭 요청은 해당 이름 있는 control type이
실제로 있을 때만 exact-text evidence가 된다.

Per-turn 합성 근거도 `vision.evidence.v2`로 올려 screenshot capture 뒤 15초
안에서만 live로 인정한다. v1 legacy나 timestamp 누락·역전·미래·만료는
fail-closed하고 stale 원문은 Host Bridge·client·LLM context에서 제거한다.
foreground와 UIA가 충돌하면 두 structured source를 버리고 screenshot/native
OCR만 low-confidence·non-actionable fallback으로 남긴다.

2026-07-30 실제 Control Page E2E에서 SDL 전경 제목
`테라리아: 모래는 OP다`를 문자 그대로 반환했고 Host Vision은
`live_accessibility_observation`, actionable=true를 기록했다. 같은 앱은 Window
루트 외 Button을 노출하지 않았으므로 버튼 이름 요청은 Main LLM 전에 고정
no-evidence 응답으로 닫혔다. screenshot과 모든 큐 파일은 즉시 삭제됐다.

별도 UI Action Target 계약은 구현됐다. 현재 전경의 이름 있는 enabled
`Button`과 `invoke`만 허용하고, 30초·일회성·재시작 비복구 token을 exact
window/element fingerprint와 postcondition에 묶는다. apply는 token을 먼저
소모한 뒤 다시 관찰한 foreground와 target이 완전히 같을 때만 fixed
PowerShell `InvokePattern`을 1회 호출한다. 결과는 `target_absent`,
`target_disabled`, `window_changed` 중 승인된 조건을 재관찰해야 성공한다.
실행됐지만 결과가 확인되지 않으면 `outcome_unverified` 실패로 보존하고 자동
재시도하지 않는다. 임의 command/argv/path/좌표/키보드와 background window는
받지 않으며 target/window text는 status와 감사 journal에 저장하지 않는다.

이 경계는 로컬 Bot API, Control Page, Windows Local I/O Bridge에 배포됐다.
실제 공개 API에서 CSRF 누락, 임의 command 필드, 존재하지 않는 well-formed
element ID, 명시 확인 없는 apply를 각각 403/400/409/400으로 거부했다.
존재하지 않는 target 요청은 host queue와 전경 재관찰까지 통과했지만 executor를
호출하지 않았고, 이후 queue 3개는 비었으며 execution/verified count는 0이다.
브라우저 panel은 `running`을 표시했고 warning/error console log는 없었다.

불투명 element ID를 수동 복사하던 UX는 read-only Button discovery로
대체됐다. 별도 5초 전경 전환 뒤 이름 있고 enabled인 Button을 최대 24개
transient selector에 표시하며, source tree가 잘렸다면 truncated 상태를
보존한다. 발견은 token을 만들지 않고 preview와 명시 확인을 계속 요구한다.
queue는 v2 exact-schema이며 disabled/duplicate/malformed/over-limit target을
Docker client에서 fail-closed한다. target/window text는 응답과 메모리 DOM
밖에 저장하지 않는다.

남은 위험은 실제 행동을 한 번도 수행하지 않았다는 점이다. UIA를 잘 노출하는
Win32/Chromium/WinUI 앱의 양성·음성 corpus가 없고, SDL·게임·일부 GPU 앱처럼
root-only인 화면은 계속 non-actionable이다. 현재 범위에는 Button 외 control,
window activation, keyboard/text 입력, 일반 rollback이 없다. Control Page에는
명시적으로 무장하고 취소할 수 있는 discovery/preview/apply별 5초 전경 전환이
추가됐다.
브라우저 타이머가 2초보다 늦게 깨어나면 요청하지 않고, apply는 별도 확인 후에만
무장한다. `target_disabled`를 되돌릴 수 있는 단일 Button fixture도 준비됐지만
유효한 live discovery, 실제 양성 실행과 전경 불일치 corpus는 아직 사용자 승인
세션에서 검증하지 않았다.

다음 조치: 격리된 테스트 앱과 사용자 동의 세션에서 파일 탐색기, 브라우저,
설정, WinUI의 stable Button corpus를 먼저 측정한다. no-op 또는 쉽게 되돌릴 수
있는 동작부터 target identity와 세 postcondition을 확인하고, 실제 focus
handoff와 복구 절차가 검증되기 전에는 범위를 넓히지 않는다.

## P2 — Host capture stop evidence 소비자 검증 부재

Supervisor는 exact physical OFF를 확인한 content-free stop evidence에도 별도 HMAC
scope를 적용하지만 현재 downstream consumer는 서명을 다시 검증하지 않는다. 또한
startup ambiguity를 판단하는 freshness probe는 서명 검증 전 단계라 공유 artifact
writer가 fresh-looking status를 써서 Bridge 시작을 거부시키는 availability 공격은
가능하다. 두 경우 모두 Local Bridge의 캡처 lease 검증이나 물리 stop을 우회하거나
`verified` 증거를 위조할 수는 없다.

다음 조치: stop evidence를 권위 판단에 사용하는 consumer가 생길 때 같은 scope의
exact verifier와 replay binding을 함께 추가한다. startup probe는 안전한 시작 거부를
유지하되 서명 검증을 재사용해 불필요한 수동 개입만 줄인다.

## P2 — `main.py` 선언형 wiring 밀도

`main.py`는 2,500줄로 현재 상한을 지키고 함수 정의와 `global`/`nonlocal`은 0개다. 남은 본문은 대부분 명시적 typed dependency wiring이며, 줄 수를 맞추기 위해 한 줄에 최대 두 인자를 배치해 이전보다 가로 밀도가 높다. 이는 현재 동작 위험보다는 리뷰 가독성의 잔여 비용이다.

다음 조치: 줄 수만을 위한 추가 이동이나 암시적 registry 도입은 하지 않는다. 새 동작은 owner 모듈에 추가하고, `main.py`에는 최대 158자·함수 정의 0개·`global`/`nonlocal` 0개 경계를 유지한다.

## P2 — 설정과 예외 처리의 잔여 분산

STT, Vision, Codex Gateway, Mindcraft는 공통 typed 설정 스키마로 이동했고
잘못된 값은 원문을 노출하지 않는 경고와 기본값으로 처리한다. Python/PowerShell
전체의 환경변수 조회, 특히 대형 호환 계층인 `config.py`와
`main_runtime_config.py`는 아직 분산돼 있다.

Host Supervisor, Local I/O Bridge, Discord, Conversation Continuity, Fast Control Continuity, STT,
Vision, Codex Gateway, Mindcraft의 오류 카운터를 Runtime Health와 Control
Page가 합성한다. 예외 메시지·스택·경로는 새 공개 응답에서 제외한다. 아직
owner 경계가 없는 보조 모듈의 광범위한 예외 처리는 남아 있다.

Control Page·Bot API·세 LLM·TTS·STT가 counter payload를 내기 전에 probe 실패한 경우도
현재 장애로 표시한다. 선택 서비스는 실행 의도와 장애를 구분할 durable desired-state가
없어 payload 없는 실패를 합성하지 않는다. 의도된 OFF 오탐을 피하는 대신 예상하지 못한
optional 장애도 Runtime Health에만 남을 수 있다. 오류 카운터는 여전히 process lifetime이며
재시작 전 이력 보존은 별도다.

`0f0201f`는 Control Page의 legacy runtime service probe도 같은 공개 오류
경계로 옮겼다. Voyager, Bot API TCP/HTTP, Codex Gateway와 전체 refresh
예외는 이제 exact allowlist 코드만 `services`에 남기며 예외 원문, upstream
`error`/login 문자열, URL과 경로를 복사하지 않는다. 최종 payload builder도
알 수 없는 입력을 generic 고정 코드로 바꾸므로 다른 호출자가 원문을 다시
주입할 수 없다. Codex readiness는 HTTP service의 `ok`가 아니라
`backendReady`, `isolatedRuntime`, `toolAccessVerified`가 모두 exact `true`인지
요구해, gateway만 살아 있고 행동 backend가 없는 상태를 준비 완료로 오판하지 않는다.

`436fb59`는 Main/Voice LLM에 주입되는 legacy runtime status context도
content-free로 바꿨다. Codex `error`/`stderr_tail`/`message`, Voyager
`last_error`/`last_critique`와 error log 마지막 줄을 prompt에 복사하지 않는다.
구조화 phase/completion reason과 오류 파일의 비어 있지 않음만
`runtime.recent-error.v1`의 exact owner/code/age bucket으로 바꾸며, 최종
context builder가 schema·owner·code·bucket allowlist를 다시 검사한다.
성공한 Voyager critique는 최근 오류로 취급하지 않는다.

`26c97e8`은 자율 engine 내부 판단·영속 상태·공개 status의 executor 예외
원문도 `autonomy.failure.v1`으로 교체했다. 관찰·실행·cycle의 세 exact code
외 입력은 generic cycle code로 바뀌고, 실패 payload의 domain/action은 지원
목록만 허용한다. 실행 실패는 content-free audit 결과로 기록되지만 성공
evidence가 아니며, legacy 상태와 최종 consumer도 독립적으로 재검사한다.

2026-08-09 memory write-behind의 step/event-log 실패는 payload·30일 보존 JSONL·
운영 로그에 예외 원문을 복사하지 않고 고정 code와 exception type만 남긴다.
self-identity review queue write 실패의 turn-trace decision과 daily vault mirror 실패
로그도 각각 fixed code/type, fixed prefix/type만 남긴다. Summary LLM primary·compact
retry 실패 로그도 fixed prefix와 exception type만 남긴다. proactive open-question
promotion과 background vault maintenance 실패 로그의 예외 detail도
`errorType=<exception-type>`만 남긴다. background cognitive refresh 실패 로그도
session key·reason·예외 메시지 없이 fixed prefix와 exception type만 남긴다.

다음 조치: 새 서비스 owner를 만들 때 typed schema와 오류 카운터를 필수 계약으로
적용하고, 기존 대형 설정 모듈은 기능 변경 시 점진적으로 이동한다.

## P1 — Codex tool-free 실행 경계와 자격증명 격리 미검증

기본 Minecraft/Mindcraft는 local Qwen으로 동작하며 Codex token read, HTTP와
subprocess를 실행하지 않는다. 선택적 Gateway도 `toolAccessVerified=false`인 동안
health not-ready, action 503, spawn 0으로 닫힌다. host-native/custom shell 경로와
`config.toml` staging은 제거했고 전용 `auth.json`만 Docker profile에 mount한다.

그러나 read-only sandbox와 mount는 같은 process principal의 파일 읽기를 막지 않는다.
현재 source option과 mock subprocess test는 pinned Codex image가 shell·file·network·MCP
tool을 실제로 0개 제공한다는 증거가 아니다. 전용 `auth.json`도 장기 자격증명이다.

다음 조치: 격리된 image에서 실제 tool registry와 secret canary 비노출을 검증하거나,
filesystem tool이 없는 broker와 목적 제한·짧은 수명 token을 사용한 뒤에만
`toolAccessVerified`와 Codex action route를 활성화한다. 그 전에는 기본 local 경로를
유지한다.

## P2 — 저장공간 보고는 Host Supervisor 가동에 의존

삭제 없는 주기 dry-run 보고와 Control Page 가시성은 추가됐다. 다만 Windows Host
Supervisor가 꺼져 있으면 보고서는 오래된 상태가 되며 실제 삭제는 의도적으로
자동화하지 않았다.

다음 조치: 후보가 반복적으로 누적될 때 보고서를 검토한 뒤 기존 retention CLI의
명시적 `--apply`를 별도 승인으로 실행한다. 브라우저 apply API나 무인 삭제는
도입하지 않는다.
