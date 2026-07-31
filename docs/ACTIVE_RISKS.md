# Evelyn Active Risks

Document status: **Current**
Last reviewed: 2026-07-31 KST
Evaluation stance: 실패 가능성과 검증 공백을 우선 기록

## P0 — Voyager는 HTTP health와 기능 준비가 다르다

마지막 확인에서 Voyager HTTP는 응답했지만 runner, bridge, Minecraft 경계는 준비되지 않았다. `healthy` 컨테이너만 보고 Minecraft 자동화가 가능하다고 판단하면 오판이다.

다음 조치: 실제 Minecraft 세션을 사용할 때 runner/bridge/TCP/task contract를 순서대로 검증한다.

## P0 — 승인된 자율행동 live E2E 검증 대기

현재 프로세스에만 유효한 guild별 grant, 1시간 TTL, exact action scope,
restart 비복구, 변경성 Discord 명령 권한 검사와 미검증 결과의 plan 진행
차단은 구현되어 있다. action별 exact evidence allowlist, 실행 뒤 동일 grant
재검사, 실행 중 만료·교체의 cursor 차단, retry budget 비증거화, audit journal
flush/fsync와 기록 실패 시 전체 grant 폐기도 구현됐다. Minecraft 접속·종료·
목표 변경은 명시적 outcome marker와 실제 상태 증거가 없으면 성공 문구를
만들지 않는다.

현재 Docker local core와 Bot API는 실행 중이지만 Discord bot과
Minecraft/Voyager는 사용자 요청 없이 지연 시작 상태다. 번들 Python에는
`aiohttp`와 `discord`가 없으므로 실제 Discord 승인 명령부터 메시지 전송 및
Minecraft 상태 변화까지 한 세션에서 수행하는 live E2E는 아직 확인하지 못했다.

Minecraft world-action lease, process-rotated capability token, 5초 owner
heartbeat, 15초 service-side stale guard, restart 비복구, `/start`·`/goal`
proof, 만료·상태 불명 시 fail-closed 정지는 구현됐다. Bot API 단일 owner,
공유 claim을 통한 경쟁 owner 차단, Discord 인증 위임, split Fast Control의
승인 경로도 구현했다. Local I/O Bridge와 legacy auto-start 우회는 차단했다.

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

새 공식 Discord 이미지에서 guild reset/continuity/Discord command wiring과
opt-in real-main crash/restart 집중 테스트 68개, `compileall`, `pip check`를
통과했다. 전체 core 440개도 기능 assertion 실패는 0개였고, 이미지에 `git`
실행 파일이 없어 과거 main signature를 조회하는 테스트 2개만 환경 오류였다.

새 v2 이미지에서도 재계산 hash 변조, 과거 generation rollback, checkpoint
삭제, v1 migration, head commit crash 복구와 실제 `main.py` crash/restart를
검증했다. 다만 checkpoint와 head를 함께 다시 쓸 수 있는 filesystem 관리자에
대한 keyed authenticity나 외부 불변 원장은 아직 제공하지 않는다.

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

## P1 — Node/Minecraft 의존성 취약점 11개

2026-07-23 스테이징 이미지 `npm audit --omit=dev` 결과는 moderate 11개,
high/critical 0개다. 대상은 Mineflayer 인증/프로토콜 및 플러그인 체인이다.

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
자동 회상에 사용되지 않지만 즉시 재합성되지는 않는다.

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

남은 위험은 coverage와 correction이 구조적 근거 연결만 다루며 기억 내용이나
사용자의 선택이 사실임을 보증하지 않는다는 점이다. hash chain과 head는
우발적·비협조적 파일 변조의 증거이지, journal과 head를 함께 다시 쓸 수 있는
filesystem 관리자에 대한 keyed authenticity나 외부 불변 원장은 아니다.
OS lock도 단일 host/shared filesystem의 writer 배제이며 분산 합의가 아니다.
실제 vault에는 현재 derived relationship이 0개라 운영 데이터에 대한 live
relink/unlink/undo는 비파괴 원칙상 실행하지 않았다. 또한 Sub-LLM이 꺼져
있거나 상위 source가 quarantine이면 multi-source note는 안전하게 격리되지만
즉시 재합성되지 않는다.

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
