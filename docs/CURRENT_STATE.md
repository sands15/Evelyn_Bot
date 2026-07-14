# Evelyn Current State

Document status: **Current**
Last reviewed: 2026-07-15 KST
Source branch: `stabilization/evaluation-findings-2026-07-15`

이 문서는 현재 확인된 사실만 기록한다. 목표 구조와 과거 계획은 다른 설계/계획 문서를 사용한다.

## Source state

- 전체 프로젝트 감사의 즉시 항목을 별도 안정화 브랜치에서 처리 중이다.
- `main.py` 분해는 정훈 요청에 따라 이번 안정화 범위에서 제외했다.
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

- `pip check`: 통과
- `docker compose config --quiet`: 통과(검증용 Discord token 사용)
- UI/Compose/Codex Gateway 집중 테스트: 통과
- 전체 회귀선과 최종 Git bundle 검증 결과는 이 브랜치의 최종 체크포인트에서 다시 기록한다.

## Operational boundaries

- Bot API: `127.0.0.1:8798`
- Control-Page: `127.0.0.1:8799`
- Codex Gateway: `127.0.0.1:8787`
- Control-Page 변경성 요청은 CSRF 세션 계약을 사용한다.
- 런타임 repair는 preview와 apply를 분리하며, preview만으로 프로세스를 시작하지 않는다.

남은 문제는 [ACTIVE_RISKS.md](ACTIVE_RISKS.md)에만 유지한다.
