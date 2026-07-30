# Evelyn Current State

Document status: **Current**
Last reviewed: 2026-07-30 KST
Source branch: `codex/dependency-config-hardening` at `cdc2177`

이 문서는 현재 확인된 사실만 기록한다. 목표 구조와 과거 계획은 다른 설계/계획 문서를 사용한다.

## Source state

- 전체 프로젝트 감사의 즉시 항목을 별도 안정화 브랜치에서 처리 중이다.
- `main.py` 분해는 목표 범위에 도달했다.
  - 현재 2,402줄이며 원래 목표 범위인 1,500~2,500줄 안에 들어왔다.
  - top-level/nested 함수 정의, `global`/`nonlocal`, dependency-builder 함수 정의는 모두 0개다.
  - 기능 구현, 판정, 상태 mutation은 owner runtime/composition 모듈에 있고 `main.py`는 설정 import,
    객체 생성, 명시적 typed dependency wiring, Discord 등록, runtime 진입을 담당한다.
  - dependency wiring은 암시적 registry나 `globals()` 우회 없이 한 줄 최대 두 인자, 158자 이하로 유지한다.
- 핵심 준비 상태와 선택 기능 준비 상태를 분리했다.
  - `ok`: 필수 핵심 서비스 준비 여부
  - `fullyHealthy`: 선택 기능을 포함한 전체 건강 여부
  - Voyager HTTP 응답과 실제 runner/bridge/Minecraft 준비 여부도 분리했다.
- 루트 Python 의존성은 `requirements.lock`으로 고정했다.
- GitHub Actions는 Windows/Python 3.11/Node 24에서 전체 회귀 테스트와 실제 `main.py` 프로세스 smoke를 실행한다.
- Codex Gateway의 `/codex/action`은 bearer token을 요구한다. `/health`는 읽기 전용으로 유지한다.
- 사용되지 않던 `docs/assets/evelyn-page.js`는 삭제했고, UI 테스트는 실제 `docs/index.html` 인라인 컨트롤러를 검사한다.
- Docker Compose의 사용자별 `C:/Users/Admin/...` 경로는 환경변수와 `USERPROFILE` 기반으로 바꿨다.
- Windows Host Vision Bridge가 Docker Bot API의 화면 요청을 실제 호스트
  캡처에 연결한다.
  - exact schema, TTL, 크기 제한을 사용하며 임의 명령·argv·경로를 받지 않는다.
  - foreground window title/class, SmolVLM scene, Windows Runtime OCR을 서로
    다른 신뢰도로 합성한다.
  - 정확한 UI 텍스트는 OCR evidence가 actionable할 때만 사용하며, 아니면
    Main LLM 호출 전에 결정론적으로 추측을 거부한다.
  - screenshot, OCR tile, 요청/응답은 요청 뒤 삭제되고 status에는 근거
    metadata와 지연만 남는다.
- 한글 프로젝트 경로의 Docker Buildx 문제를 피하기 위해 빌드 동안만 사용하지
  않는 드라이브 문자를 매핑한다. allowlist 이미지 세 개만 빌드하고 자신이 만든
  매핑만 검증 후 해제한다.

## Deployment state

- `cdc2177` 소스로 `bot_api`, `control_page`, `vision` 이미지를 실제 재빌드하고
  공식 `start_local_background.ps1` 경로로 교체했다.
- 세 컨테이너 모두 `healthy`, restart count 0이다. Main/Router/Sub LLM,
  TTS, STT도 계속 healthy다.
- Windows Host Supervisor와 Local I/O Bridge heartbeat는 fresh이고 bridge는
  `ready=true`, TTS warmup 완료, Host Vision `running`이다.
- 개인정보 보호 기본값에 따라 로컬 마이크는 비활성 상태다.
- Minecraft/Voyager와 Codex Gateway는 기본 local core에서 지연 시작되며 현재
  실행하지 않는다. Discord bot도 사용자 요청 없이 시작하지 않았다.

## Last runtime evidence

2026-07-30 실제 local-only runtime checker 결과:

- Control Page, Bot API, Main/Router/Sub LLM, TTS, STT, Vision HTTP health 통과
- `controlReady`, `botReady`, `mainReady`, `routerReady`, `subReady`,
  `ttsReady`, `sttReady`, `chatReady`, `voiceReady`, `visionReady` 모두 `true`
- Windows Local I/O Bridge attached/ready
- 지연 시작되는 `voyagerReady`, `codexReady`는 경고이며 core 실패로 계산하지 않음
- 공식 checker 최종 결과: `Docker runtime check passed.`

배포된 Control Page에서 실제 화면 질문 두 종류를 재검증했다.

- 일반 앱 질문은 현재 화면 근거를 사용해 `파일 탐색기야.`라고 응답했다.
- 정확한 제목·버튼 질문은
  `화면 캡처는 됐지만 이번에는 글자를 읽을 수 있는 근거를 얻지 못했어.
  제목이나 버튼 이름은 추측하지 않을게.`라고 응답했다.
- 마지막 Host Vision 응답은 `screenshotDeleted=true`였고 requests,
  processing, responses, screenshots 디렉터리는 모두 0개였다.
- `status.json`만 남았으며 화면·OCR·사용자 문장 원문은 포함하지 않는다.

## Verification state

검증한 코드 기준점: `cdc2177`

- 새 Bot API Python 3.11 이미지에서 전체 `unittest discover` 1,585개를
  실행했다. 기능 assertion 실패는 0개였다.
- 이미지별 의존성 또는 OS 차이로 발생한 10개 import/platform 오류는 Windows,
  Discord, Codex Gateway/Voyager 소유 환경에서 각각 재실행해 모두 통과했다.
- 관련 Fast Control, capability, repair, launcher, local mic 테스트 168개 통과
- Vision 전체 중 Host bridge를 제외한 58개와 Host bridge 4개 통과
- 실제 `main.py` Control Page 기동 및 강제 종료 뒤 대화 연속성 복구 smoke
  2개 통과
- Python `compileall`, 모든 `docs/assets/*.js`의 `node --check`, 변경
  PowerShell parser, `git diff --check` 통과
- `docker compose config --quiet` 통과
- 새 Bot API와 Vision 이미지 `pip check` 통과
- 한글 경로 allowlist 빌드, Bot API owner-claim 정상 해제, 이미지 교체,
  전체 launcher readiness E2E 통과
- 배포 후 공식 `check_docker_runtime.ps1 -IncludeLocalBridge` 통과

## Operational boundaries

- Bot API: `127.0.0.1:8798`
- Control-Page: `127.0.0.1:8799`
- Codex Gateway: `127.0.0.1:8787`
- Control-Page 변경성 요청은 CSRF 세션 계약을 사용한다.
- 런타임 repair는 preview와 apply를 분리하며, preview만으로 프로세스를 시작하지 않는다.
- Host Vision 요청은 `runtime_artifacts/host_vision/`의 exact-schema queue만
  사용하고, Host Supervisor가 소유한 Local I/O Bridge만 화면을 캡처한다.

남은 문제는 [ACTIVE_RISKS.md](ACTIVE_RISKS.md)에만 유지한다.
