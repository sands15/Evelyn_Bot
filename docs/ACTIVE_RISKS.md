# Evelyn Active Risks

Document status: **Current**
Last reviewed: 2026-07-30 KST
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

새 공식 Discord 이미지에서 guild reset/continuity/Discord command wiring과
opt-in real-main crash/restart 집중 테스트 68개, `compileall`, `pip check`를
통과했다. 전체 core 440개도 기능 assertion 실패는 0개였고, 이미지에 `git`
실행 파일이 없어 과거 main signature를 조회하는 테스트 2개만 환경 오류였다.

남은 검증 공백은 실제 인증된 Discord 세션에서 관리자 초기화 명령 직후
재시작까지 수행하는 live E2E와 이 브랜치의 원격 Windows CI 결과다. 실제
Discord bot은 사용자 요청 없이 시작하지 않았다.

다음 조치: 사용자가 Discord 검증을 시작할 때 별도 테스트 guild에서 완료 턴과
active follow-up을 만든 뒤 관리자 초기화, 강제 재시작, 대상 guild 비복구와
다른 guild 보존을 확인한다. 원격 브랜치를 올릴 때 Windows CI의 opt-in
real-main 시나리오도 함께 통과시킨다.

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

남은 위험은 coverage와 correction이 구조적 근거 연결만 다루며 기억 내용이나
사용자의 선택이 사실임을 보증하지 않는다는 점이다. journal은 단일 Bot API
writer 안에서 직렬화되지만 event chain의 암호학적 tamper evidence나 여러
writer의 합의는 제공하지 않는다. 실제 vault에는 현재 derived relationship이
0개라 운영 데이터에 대한 live relink/unlink/undo는 비파괴 원칙상 실행하지
않았다. 또한 Sub-LLM이 꺼져 있거나 상위 source가 quarantine이면 multi-source
note는 안전하게 격리되지만 즉시 재합성되지 않는다.

다음 조치: 실제 derived 기억이 생기면 사용자 청취·화면 확인과 별개로 correction
preview의 설명 가능성, relink/unlink/undo 결과와 journal 복구를 운영 데이터
복제본에서 검증한다. 이후 journal hash chaining과 단일-writer ownership
marker가 필요한지 위협 모델로 판단하고, coverage bucket과 forward rejection
추세가 실제 품질 신호인지 함께 측정한다.

## P1 — UI 접근성 corpus·동작 대상 계약 미완성

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

남은 위험은 두 가지다. UIA를 잘 노출하는 Win32/Chromium/WinUI 앱의 버튼·메뉴
양성 표본을 아직 실제 corpus로 대조하지 않았고, SDL·게임·일부 GPU 앱처럼
root-only인 화면은 정확한 하위 UI 의미를 제공하지 않는다. 또한 element ID는
관측 상관관계용일 뿐 클릭 권한이 아니며, 현재 구현은 UI focus·invoke·입력
mutation을 전혀 수행하지 않는다.

다음 조치: 파일 탐색기, 브라우저, 설정, WinUI 앱의 title/button/menu/tab
양성·음성 corpus를 반복 측정한다. 이후에도 클릭은 사용자 승인, 재관측,
foreground 일치, element identity, 결과 검증과 rollback을 포함하는 별도
행동 계약을 설계·검증한 뒤에만 허용한다.

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
