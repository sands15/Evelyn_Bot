# Evelyn Active Risks

Document status: **Current**
Last reviewed: 2026-07-31 KST
Evaluation stance: 실패 가능성과 검증 공백을 우선 기록

## P0 — Minecraft functional readiness live E2E 대기

Mindcraft HTTP liveness와 실제 Minecraft 자율행동 readiness는 이제 별도
계약으로 판정한다. world lease, runner, fresh telemetry, Minecraft 연결,
gated goal manager의 명령 게이트·explicit postcondition task contract,
active autonomy 중 하나라도 없으면 `voyagerReady=false`다. 누락·모순된
Mindcraft 계약도 fail-closed하며 고정 blocker만 공개한다.

합성·이미지 내부 검증은 통과했지만 실제 Minecraft world에는 접속하지 않았다.
따라서 lease 승인부터 runner 연결, 실제 task effect, 연결 단절과 재시작까지
한 세션에서 readiness가 정확히 전이하는지는 아직 확인하지 못했다.

다음 조치: 사용자가 별도 Minecraft 검증 세션을 시작할 때
`blocked → starting → ready → blocked` 전이와 실제 world effect를
Control Page, Runtime Health, Mindcraft telemetry에서 함께 대조한다.

## P0 — 승인된 자율행동 live E2E 검증 대기

현재 프로세스에만 유효한 guild별 grant, 1시간 TTL, exact action scope,
restart 비복구, 변경성 Discord 명령 권한 검사와 미검증 결과의 plan 진행
차단은 구현되어 있다. action별 exact evidence allowlist, 실행 뒤 동일 grant
재검사, 실행 중 만료·교체의 cursor 차단, retry budget 비증거화, audit journal
flush/fsync와 기록 실패 시 전체 grant 폐기도 구현됐다. Minecraft 접속·종료·
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
proof, 만료·상태 불명 시 fail-closed 정지는 구현됐다. Bot API 단일 owner,
공유 claim을 통한 경쟁 owner 차단, Discord 인증 위임, split Fast Control의
승인 경로도 구현했다. Local I/O Bridge와 legacy auto-start 우회는 차단했다.

계획된 Bot API 교체의 claim handoff도 실제 컨테이너에서 검증했다. shutdown
취소를 포함한 모든 cleanup 경로가 `finally`에서 claim을 반납하고 30초 stop
grace를 사용한다. 실제 SIGTERM은 4.2초 안에 claim을 제거했으며 다음
`--force-recreate`는 첫 시도에 healthy가 됐다. 전원 차단·SIGKILL처럼 cleanup이
불가능한 종료는 의도적으로 15초 stale guard 뒤에만 새 owner가 인수한다.

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

완료 턴이 1초 periodic writer를 기다리던 crash-loss 창도 닫았다. Discord
text는 실제 전송 뒤 완료 상태와 checkpoint를 먼저 durable commit하고
선택적 TTS를 실행한다. Control Page 일반·검색, 검색 후속, 자율 후속,
Discord 명령, 음성 재생 완료 경로도 같은 즉시 commit 계약을 사용한다.
commit 실패는 이미 전달된 응답을 취소하거나 중복 전송하지 않고 고정 오류
코드만 남긴다.

`2272668`부터 각 surface는 commit callback의 단순 반환을 durable 성공으로
간주하지 않는다. exact status schema, current/verified checkpoint head,
rollback protection, 양수 generation·session count와 이번 commit 성공
metric을 모두 검증한 최소 receipt만 받는다. 부분·legacy·손상 status는
fail-closed하며, 자율 후속이 실제 owner 필드 대신 없는 `generation`을 읽어
항상 0으로 기록하던 결함도 닫혔다. 이 경계는 실제 전달을 되돌리거나 같은
답변을 재전송하지 않고 durability 실패만 정확히 드러낸다.

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
Fast Control은 owner `savedAt` 순서로 bounded recent context를 양방향
사용하며, 더 최신 empty/reset boundary보다 오래된 상대 문맥은 되살리지
않는다. 중앙 mutation owner로 이관할 때 생기는 multi-process overwrite
위험은 도입하지 않았다.

남은 공백은 실제 인증된 Discord↔Control Page handoff다. 교차 연결은
`CROSS_SURFACE_CONTINUITY_ENABLED`와 개인 guild/user ID를 명시해야 하며,
기본값은 의도적으로 fail-closed다. 이번 작업에서는 실행 중인 Bot API와
Control Page를 교체하지 않았고 Discord도 시작하지 않았으므로, 한 surface의
완료 턴이 실제 다른 surface의 다음 응답 의미에 반영되는 live 증거와
동시 write 중 반복 read의 Windows filesystem 지연 표본은 아직 없다.

코드는 이제 매 prompt 시도의 결과를
`cross_surface_continuity.merge.v1`으로 계측한다. Main 턴 metrics와 Fast
Control의 `lastMerge`에서 merged/reset/rejected 상태, generation/count,
ordering과 latency를 확인할 수 있어 live E2E가 단순 응답 의미 추정에만
의존하지 않는다. 증거는 원문·사용자/세션 ID·hash·경로를 포함하지 않고
저장하지 않는다. 현재 owner가 손상되면 정상인 상대 owner도 주입하지 않아
검증할 수 없는 reset 경계를 우회하지 않는다.

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
검증했다. 다만 checkpoint와 head를 함께 다시 쓸 수 있는 filesystem 관리자에
대한 keyed authenticity나 외부 불변 원장은 아직 제공하지 않는다.

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

남은 위험은 journal과 head, continuity checkpoint까지 함께 다시 쓰거나 함께
삭제할 수 있는 filesystem 관리자에 대한 외부 authenticity가 없다는 점과,
실제 Control Page에서 장시간 웹 조사 중 Bot API 컨테이너를 강제 종료하는 운영
E2E는 아직 수행하지 않았다는 점이다. live 검증에서는 시작 답변 뒤 강제 종료,
고정 중단 안내, 자동 재요청 0회와 `actions.recovery`의 content-free 상태를 함께
확인한다.

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

Falcon-OCR은 여전히 Hugging Face remote model code 실행을 요구한다.
`VISION_TRUST_REMOTE_CODE=false`는 SmolVLM 경로만 제한하며 Falcon-OCR을
sandbox한 것은 아니다.

다음 조치: Qwen-ASR의 Transformers 5 호환 릴리스와 CUDA 12.8 Torch 2.13
wheel을 재확인한다. 새 이미지 GPU 모델 로드 smoke 전에는 배포 완료로 판정하지
않는다.

## P1 — Node/Minecraft 의존성 취약점 14개

2026-07-31 Mindcraft runtime의 ESLint를 10.8.0으로 올리고 실제 runtime
config dependency를 명시적으로 고정한 새 이미지에서
`npm audit --omit=dev` 결과는 moderate 14개, high/critical 0개다.
기존 high 5개 ESLint/minimatch 체인은 제거됐다. 남은 대상은 Mineflayer
인증/프로토콜 및 플러그인 체인이다.

- 직접 의존성: `mineflayer`, `mineflayer-armor-manager`,
  `mineflayer-collectblock`, `mineflayer-pvp`
- 전이 의존성: `@azure/msal-node`, `minecraft-protocol`, `mineflayer-tool`,
  `mineflayer-utils`, `prismarine-auth`, `uuid`, `yggdrasil`

대부분 `fixAvailable=false`이며 제안된 일부 강제 수정은 주요 버전 역행을 포함한다.
다음 조치: 강제 audit fix는 금지하고, 별도 호환성 검증에서 Mineflayer 체인을 갱신한다.

## P1 — 실제 음성 하드웨어 E2E 미검증

Windows Host Supervisor는 이제 별도 `.venv-host`와 최소 lock으로 재현 가능하게
설치되며, launcher는 Host Supervisor와 Local I/O Bridge에서 서로 다른 두 번의
fresh heartbeat를 확인한 뒤에만 준비 완료를 보고한다. TTS 음성 프로필의 WAV,
JSON, `ref_text`도 Docker 시작 전에 검사한다.

그러나 CI의 실제 프로세스 smoke는 `main.py`가 기동 가능한지만 확인한다. 마이크
입력부터 STT, 대화, TTS, 로컬 재생까지 계획된 surface별 10턴과 무음 구간을
보장하지 않는다.

Control Page에는 이제 로컬 검증 전용 마이크 동의 임대가 배포됐다. preview
token은 120초·일회용이고, 동의는 세션 연결 전 최대 5분과 연결 뒤 최대 30분으로
제한된다. 마이크 ON과 `captureReady` ACK가 모두 확인되어야 활성화되며 검증
성공·실패·중단, 명시적 철회, 만료, Control Page 종료·재시작에서 OFF를
fail-closed 요청한다. 상태는 제어 메타데이터만 저장하고 음성·transcript를
저장하지 않는다. 현재 실제 마이크는 계속 비활성 상태다.

검증 FSM은 이제 현재 단계와 연결된 interrupt 단계 외 이벤트를 거부하고,
재생 완료 전 청취 확인과 지난 단계 재시도를 허용하지 않는다. STT 불일치,
중복 final/turn/playback/interrupt, 완료·취소 동시 관측, 무음 구간 활동도
즉시 실패다. 로컬 브리지는 재생 직전 일반 큐 발화를 TTS cleanup에서 잃지
않고, clone voice fallback도 단일 playback owner 안에서 수행한다. 전체 음성
414개와 관련 검증/runtime/UI 50개는 통과했지만 이는 합성 입력과 mock 장치를
사용한 계약 검증이다.

다음 조치: 사용자가 Control Page의 “검증 세션 동안 마이크 허용”을 직접 확인한
뒤 로컬/Discord 10턴, barge-in, 무음 구간을 실행하고 비식별 보고서를 기록한다.

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

새로 저장되는 raw 대화 row는 content-free stable evidence ID와 source turn
ID를 guild/room/person/session scope에 동일하게 보존한다. Prompt에 실제 선택된
row만 receipt와 turn summary에 기록하므로 이후 턴에서 어느 원문 turn이
제공됐는지 역추적할 수 있다. 기존 raw row는 내용을 이용해 ID를 소급 추론하지
않고 `unattributed`로 남긴다.

새로 생성되는 rolling summary는 본문 hash에 묶인 content-free sidecar에 자체
파생 evidence ID와 실제 Summary LLM 입력 evidence/turn ID를 기록한다. 새
facts/questions도 같은 입력 계보와 별도 파생 ID를 hot JSONL과 mirror에
보존한다. context-size compact 재시도에서는 첫 시도의 최근 row를 source로
남기지 않고 compact prompt에 실제 포함된 summary와 현재 턴만 연결한다. sidecar
본문 hash가 다르거나 row provenance가 손상되면 receipt는 이를 근거로 인정하지
않고 fail-closed한다.

배포 전의 rolling summary·facts/questions와 과거 raw row는 여전히 근거 없는
상태지만 사용 정책은 닫았다. 내용 유사도나 시간 인접성으로 소급 연결하지 않고,
해당 항목은 `memory.context-use.v1` 아래 `확인 전용`으로만 prompt에 남는다.
답변의 사실 근거로 쓰거나 단정할 수 없고 현재 사용자의 직접 확인 또는 짧은
확인 질문에만 사용할 수 있다. producer의 `groundingState`도 근거 ID/count로
재계산하며, 최종 1,680자 경계에서 문맥이 잘리면 개별 귀속을 버리고 하나의
opaque 확인 전용 component로 강등한다.

남은 coverage 위험은 실제 legacy 항목 중 어느 것이 사용자 확인을 거쳐 새 근거로
재작성되어야 하는지 운영 데이터에서 아직 측정·검토하지 않았다는 점이다. 확인
전용 문구는 모델 행동 계약이지 기억 내용의 진실성 보증이 아니므로 실제 대화에서
단정 억제와 확인 질문 품질도 평가해야 한다. 다음 조치는 content-free coverage
집계로 legacy 확인 전용 비율을 관측하고, 사용자가 확인한 항목만 새 evidence에
연결하는 preview/apply 흐름을 설계하는 것이다.

남은 위험은 coverage와 correction이 구조적 근거 연결만 다루며 기억 내용이나
사용자의 선택이 사실임을 보증하지 않는다는 점이다. hash chain과 head는
우발적·비협조적 파일 변조의 증거이지, journal과 head를 함께 다시 쓸 수 있는
filesystem 관리자에 대한 keyed authenticity나 외부 불변 원장은 아니다.
OS lock도 단일 host/shared filesystem의 writer 배제이며 분산 합의가 아니다.
실제 vault에는 현재 derived relationship이 0개라 운영 데이터에 대한 live
relink/unlink/undo는 비파괴 원칙상 실행하지 않았다. 또한 Sub-LLM이 꺼져
있거나 상위 source가 quarantine이면 multi-source note는 안전하게 격리된다.
pending 전용 retry는 기본 60초로 줄었지만 전체 vault 유지보수는 음성 hot
path와 GPU 경합을 피하려고 `realtime` 턴에서 실행하지 않는다. 따라서
음성만 계속되는 세션의 재시도는 다음 startup 또는 비실시간 기억 유지보수
기회까지 기다릴 수 있다.

다음 조치: 실제 derived 기억이 생기면 correction preview의 설명 가능성,
relink/unlink/undo 결과와 journal 복구를 운영 데이터 복제본에서 검증한다.
filesystem 관리자까지 위협 모델에 포함할 때는 keyed external anchor 또는
불변 audit sink를 추가하고, coverage bucket과 forward rejection 추세가 실제
품질 신호인지 함께 측정한다.

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

## P2 — `main.py` 선언형 wiring 밀도

`main.py`는 2,402줄로 목표 범위에 들어왔고 함수 정의와 `global`/`nonlocal`은 0개다. 남은 본문은 대부분 명시적 typed dependency wiring이며, 줄 수를 맞추기 위해 한 줄에 최대 두 인자를 배치해 이전보다 가로 밀도가 높다. 이는 현재 동작 위험보다는 리뷰 가독성의 잔여 비용이다.

다음 조치: 줄 수만을 위한 추가 이동이나 암시적 registry 도입은 하지 않는다. 새 동작은 owner 모듈에 추가하고, `main.py`에는 최대 158자·함수 정의 0개·`global`/`nonlocal` 0개 경계를 유지한다.

## P2 — 설정과 예외 처리의 잔여 분산

STT, Vision, Codex Gateway, Mindcraft는 공통 typed 설정 스키마로 이동했고
잘못된 값은 원문을 노출하지 않는 경고와 기본값으로 처리한다. Python/PowerShell
전체의 환경변수 조회, 특히 대형 호환 계층인 `config.py`와
`main_runtime_config.py`는 아직 분산돼 있다.

Host Supervisor, Local I/O Bridge, Discord, Conversation Continuity, STT,
Vision, Codex Gateway, Mindcraft의 오류 카운터를 Runtime Health와 Control
Page가 합성한다. 예외 메시지·스택·경로는 새 공개 응답에서 제외한다. 아직
owner 경계가 없는 보조 모듈의 광범위한 예외 처리는 남아 있다.

`0f0201f`는 Control Page의 legacy runtime service probe도 같은 공개 오류
경계로 옮겼다. Voyager, Bot API TCP/HTTP, Codex Gateway와 전체 refresh
예외는 이제 exact allowlist 코드만 `services`에 남기며 예외 원문, upstream
`error`/login 문자열, URL과 경로를 복사하지 않는다. 최종 payload builder도
알 수 없는 입력을 generic 고정 코드로 바꾸므로 다른 호출자가 원문을 다시
주입할 수 없다. Codex readiness는 HTTP service의 `ok`가 아니라 현재 계약의
`backendReady is true`를 요구해, gateway만 살아 있고 행동 backend가 없는
상태를 준비 완료로 오판하지 않는다.

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

다음 조치: 새 서비스 owner를 만들 때 typed schema와 오류 카운터를 필수 계약으로
적용하고, 기존 대형 설정 모듈은 기능 변경 시 점진적으로 이동한다.

## P2 — Codex 자격증명의 수명

사용자의 live `~/.codex` 직접 마운트는 제거했다. 전용 디렉터리에서
`auth.json`과 선택적 `config.toml`만 읽어 컨테이너 tmpfs에 복사하며 Gateway는
read-only root, capability drop, `no-new-privileges`로 실행한다.

전용 `auth.json` 사본은 여전히 장기 자격증명이다.

다음 조치: 사용자 대화형 세션과 독립적으로 폐기할 수 있는 목적 제한·짧은 수명
토큰이 제공되면 교체한다.

## P2 — 저장공간 보고는 Host Supervisor 가동에 의존

삭제 없는 주기 dry-run 보고와 Control Page 가시성은 추가됐다. 다만 Windows Host
Supervisor가 꺼져 있으면 보고서는 오래된 상태가 되며 실제 삭제는 의도적으로
자동화하지 않았다.

다음 조치: 후보가 반복적으로 누적될 때 보고서를 검토한 뒤 기존 retention CLI의
명시적 `--apply`를 별도 승인으로 실행한다. 브라우저 apply API나 무인 삭제는
도입하지 않는다.
