# Evelyn Active Risks

Document status: **Current**
Last reviewed: 2026-07-15 KST
Evaluation stance: 실패 가능성과 검증 공백을 우선 기록

## P0 — 배포되지 않은 보안 변경

Codex Gateway action 인증은 소스와 테스트에는 들어갔지만 실행 중인 컨테이너에는 아직 반영되지 않았다. 재시작 승인을 받기 전까지 현재 프로세스의 `/codex/action` 보호 여부를 새 코드 기준으로 주장하면 안 된다.

다음 조치: 승인 후 관련 컨테이너를 재빌드/재시작하고, 무토큰·오토큰 `401`, 정상 토큰 `200`을 실제 포트에서 다시 확인한다.

## P0 — Voyager는 HTTP health와 기능 준비가 다르다

마지막 확인에서 Voyager HTTP는 응답했지만 runner, bridge, Minecraft 경계는 준비되지 않았다. `healthy` 컨테이너만 보고 Minecraft 자동화가 가능하다고 판단하면 오판이다.

다음 조치: 실제 Minecraft 세션을 사용할 때 runner/bridge/TCP/task contract를 순서대로 검증한다.

## P1 — Python 의존성 취약점 4개

2026-07-15 `pip-audit` 결과, lock의 111개 패키지 중 `transformers==4.57.6` 하나에서 알려진 취약점 4개가 보고됐다.

- `PYSEC-2025-217`
- `PYSEC-2026-2290`
- `PYSEC-2026-2288`
- `PYSEC-2026-2289`

표시된 수정 버전은 `5.0.0`과 `5.3.0`이지만, 이블린의 STT/Vision 모델 호환성을 검증하지 않은 채 major upgrade를 적용하면 런타임을 깨뜨릴 가능성이 크다.

다음 조치: 별도 호환성 브랜치에서 모델 로드, STT, Vision smoke를 먼저 통과시킨 뒤 upgrade 여부를 결정한다. 재검토일: 2026-07-22.

## P1 — Node/Minecraft 의존성 취약점 8개

2026-07-15 `npm audit` 결과는 moderate 8개다. 대상은 Mineflayer 인증/프로토콜 체인이며 `fixAvailable=false`다.

- 직접 의존성: `mineflayer`, `mineflayer-collectblock`, `mineflayer-tool`
- 전이 의존성: `@azure/msal-node`, `minecraft-protocol`, `prismarine-auth`, `uuid`, `yggdrasil`

다음 조치: 강제 audit fix는 금지하고, 별도 호환성 검증에서 Mineflayer 체인을 갱신한다. 재검토일: 2026-07-22.

## P1 — 실제 음성 하드웨어 E2E 미검증

CI의 실제 프로세스 smoke는 `main.py`가 기동 가능한지만 확인한다. 마이크 입력부터 STT, 대화, TTS, 로컬 재생까지 5회 연속 성공을 보장하지 않는다.

다음 조치: 릴리스 전 수동 하드웨어 검증을 별도 체크리스트로 실행하고 결과를 기록한다.

## P1 — `main.py` 구조 부채

`main.py` 분해를 시작해 음성 ingress/audio filtering, wake probe/환경음 조기 차단, TTS interrupt/input suppression gate, partial/full STT 실행, transcript 확정/barge-in merge, short transcript/final wake session gate는 런타임 모듈로 이동했다. 그러나 `_process_member_audio_impl`은 여전히 231줄이며 reply context와 대규모 dependency 조립을 직접 수행한다. 파일이 줄었다고 구조 위험이 해결된 상태는 아니다.

다음 조치: reply context/dependency 조립 경계를 분리하고 전체 회귀를 유지한다.

## P2 — 설정과 예외 처리의 잔여 분산

Compose의 사용자별 경로 일부는 제거했지만 Python/PowerShell 환경변수 조회와 광범위한 `except Exception`은 여전히 많다. 현재 변경은 전체 설정 통합이나 예외 관측성 문제를 해결하지 않았다.

다음 조치: 설정 스키마 통합과 예외 카운터/최근 오류 시각 노출을 독립 작업으로 진행한다.

## P2 — 자격증명 마운트

Codex 자격증명은 컨테이너에 읽기 전용으로 마운트된다. 읽기 전용은 변조만 줄이고 탈취 범위는 제거하지 않는다. Gateway action 인증 추가만으로 컨테이너 침해 위험이 사라지는 것은 아니다.

다음 조치: 전용 최소 권한 자격증명 또는 짧은 수명 토큰 도입 가능성을 검토한다.

## P2 — 저장공간 정리는 아직 수동

retention 도구는 있지만 정기 dry-run 보고와 승인 기반 정리가 자동 운영되지 않는다. 큰 로그나 Chrome/CDP 프로필이 다시 누적될 수 있다.

다음 조치: 삭제 없는 주기 보고부터 추가하고, 실제 삭제는 별도 승인 경계로 유지한다.
