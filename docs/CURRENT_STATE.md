# Evelyn Current State

Document status: **Current**
Last reviewed: 2026-07-18 KST
Source branch: `refactor/main-py-decomposition-2026-07-15`

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

## Deployment state

- 이번 변경 뒤 Docker 컨테이너와 Windows 로컬 런타임은 재시작하거나 재빌드하지 않았다.
- 따라서 현재 실행 중인 프로세스에는 이 브랜치의 Codex Gateway 인증과 readiness 변경이 아직 적용되지 않았다.
- 실제 반영은 정훈의 재시작 승인 뒤에만 수행한다.

## Last runtime evidence

소스의 현재 health builder를 기존 로컬 서비스 상태에 직접 적용한 결과:

- `ok=true`
- `fullyHealthy=false`
- `coreState=up`
- `overallState=degraded`
- `voyagerHttpReady=true`
- `voyagerRuntimeReady=false`

즉, 핵심 대화/제어 서비스 준비와 Voyager 실제 자동화 준비는 같은 상태가 아니다.

## Verification state

최근 `main.py` 검증 시각: 2026-07-18 19:03 KST
검증한 코드 기준점: `refactor/main-py-decomposition-2026-07-15`의 `main.py` 2,500줄 목표 작업 트리

- 전체 unittest: `EVELYN_RUN_REAL_MAIN_INTEGRATION=1`, `PYTHONWARNINGS=error::ResourceWarning`에서 1,289개 통과
- 실제 `main.py` Control Page 프로세스 smoke 별도 재실행 통과
- `main.py` AST/compile 감사: 함수 정의 0개, `global`/`nonlocal` 0개, 최대 줄 길이 158자, 손상 문자 0개

다음 항목은 2026-07-15 전체 프로젝트 감사에서 통과했으며 이번 줄 수 작업에서는 재실행하지 않았다.

- `pip check`
- `docker compose config --quiet`(검증용 Discord token 사용)
- Python `compileall`
- 활성 Live2D/boot JavaScript `node --check`
- Codex Gateway 테스트 서버: 무토큰/오토큰 `401`, 정상 bearer token `200`

## Operational boundaries

- Bot API: `127.0.0.1:8798`
- Control-Page: `127.0.0.1:8799`
- Codex Gateway: `127.0.0.1:8787`
- Control-Page 변경성 요청은 CSRF 세션 계약을 사용한다.
- 런타임 repair는 preview와 apply를 분리하며, preview만으로 프로세스를 시작하지 않는다.

남은 문제는 [ACTIVE_RISKS.md](ACTIVE_RISKS.md)에만 유지한다.
