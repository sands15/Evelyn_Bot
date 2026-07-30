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
차단은 구현되어 있다. Minecraft 접속·종료·목표 변경도 명시적 outcome
marker와 실제 상태 증거가 없으면 성공 문구를 만들지 않는다.

그러나 현재 번들 Python에는 `aiohttp`와 `discord`가 없고 Docker Engine도
꺼져 있어, 실제 Discord 승인 명령부터 메시지 전송 및 Minecraft 상태 변화까지
한 세션에서 수행하는 live E2E는 아직 확인하지 못했다.

Minecraft world-action lease, process-rotated capability token, 5초 owner
heartbeat, 15초 service-side stale guard, restart 비복구, `/start`·`/goal`
proof, 만료·상태 불명 시 fail-closed 정지는 구현됐다. Bot API 단일 owner,
공유 claim을 통한 경쟁 owner 차단, Discord 인증 위임, split Fast Control의
승인 경로도 구현했다. Local I/O Bridge와 legacy auto-start 우회는 차단했다.

다음 조치: 공식 Discord/Bot API 이미지에서 owner/admin과 일반 사용자의 명령
경계를 각각 확인하고, grant 만료·프로세스 재시작·Minecraft 연결 실패를
포함한 합성 시나리오를 실행한다. 성공 action마다 audit journal의
`verified=true`와 예상 `evidenceCode`가 실제 효과와 일치하는지 대조한다.
split Docker에서는 Bot API 재시작 시 token 회전·lease 비복구·stale runner
정지, Discord 재시작 시 중앙 lease 유지, 동시 Control Page/Discord 요청의
owner mismatch를 실제 컨테이너와 Minecraft 세션에서 추가 검증한다.

## P1 — Conversation Continuity 전체 main crash/restart 검증 대기

완료된 대화 턴과 active follow-up을 15분 동안 제한적으로 복구하는 checkpoint,
guild 초기화 즉시 flush, 만료·손상·revocation fail-closed 계약은 구현됐고
집중 단위 테스트와 lifecycle smoke를 통과했다. checkpoint와 revocation
marker는 필요한 경로에서 flush·fsync 뒤 원자 교체된다.

periodic writer가 저장한 직후 첫 Python 프로세스를 `os._exit`로 강제 종료하고
두 번째 새 프로세스가 완료 턴, active follow-up, user ownership, 현재 system
prompt와 reply target을 복구하는 owner-level E2E도 통과했다. 부분 STT와 이전
system prompt는 복구되지 않았다.

real-main smoke가 설정한 임시 artifact root를 continuity, autonomy,
Minecraft lease도 따르도록 하드코딩 경로를 제거했다. 같은 임시 root에서 실제
`main.py`를 기동·강제 종료·재기동하고 두 번의 restore와 repository 기본
checkpoint 비변경을 확인하는 opt-in CI 시나리오도 추가했다.

현재 Docker Engine이 꺼져 있고 번들 Python에는 `discord`와 `torch`가 없어 새
real-main crash 시나리오는 로컬에서 실행하지 못했다. lock 의존성을 설치하는
Windows CI에서는 `EVELYN_RUN_REAL_MAIN_INTEGRATION=1`로 실행되지만, 아직 이
브랜치의 원격 CI 결과는 없다.

다음 조치: Windows CI에서 real-main crash/restart를 통과시킨 뒤, 공식 Discord
이미지에서 합성 Discord 세션과 guild reset 비복구를 확인한다.

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

CI의 실제 프로세스 smoke는 `main.py`가 기동 가능한지만 확인한다. 마이크 입력부터 STT, 대화, TTS, 로컬 재생까지 5회 연속 성공을 보장하지 않는다.

다음 조치: 릴리스 전 수동 하드웨어 검증을 별도 체크리스트로 실행하고 결과를 기록한다.

## P1 — 실제 Windows 화면 관찰 E2E 미검증

화면 관찰 요청, 실제 scene/OCR 근거, 실패·unreliable·unknown 상태를
`vision.evidence.v1`로 분리하고, 문자열 안내문만으로 vision 도구가 성공 처리되는
경로는 차단했다. scene만 성공한 경우 OCR 도구도 성공한 것으로 보던 경계 역시
분리했다.

그러나 현재 브랜치에서는 실제 Windows 캡처와 배포된 Vision/OCR 서비스를 함께
사용해 성공·검은 프레임·빈 분석 결과를 검증하지 않았다.

다음 조치: 실제 화면에서 명확한 UI 텍스트, 텍스트 없는 장면, 검은 프레임을 각각
실행하고, 답변의 화면 주장과 benchmark의 `vision_evidence_*` 필드가 일치하는지
확인한다.

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
