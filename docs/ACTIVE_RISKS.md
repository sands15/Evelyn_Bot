# Evelyn Active Risks

Document status: **Current**
Last reviewed: 2026-07-19 KST
Evaluation stance: 실패 가능성과 검증 공백을 우선 기록

## P0 — Voyager는 HTTP health와 기능 준비가 다르다

마지막 확인에서 Voyager HTTP는 응답했지만 runner, bridge, Minecraft 경계는 준비되지 않았다. `healthy` 컨테이너만 보고 Minecraft 자동화가 가능하다고 판단하면 오판이다.

다음 조치: 실제 Minecraft 세션을 사용할 때 runner/bridge/TCP/task contract를 순서대로 검증한다.

## P1 — Python 의존성 취약점 5개

lock의 111개 패키지 중 `transformers==4.57.6`에서 알려진 취약점 4개, `torch==2.12.1`에서 1개가 보고됐다.

Transformers findings(2026-07-15 확인):

- `PYSEC-2025-217`
- `PYSEC-2026-2290`
- `PYSEC-2026-2288`
- `PYSEC-2026-2289`

Torch finding(2026-07-18 CI 확인):

- `PYSEC-2025-194` / `CVE-2025-3000`: `torch.jit.script` 메모리 손상, GitHub Reviewed Low 1.9, 로컬 공격·낮은 권한 필요, 수정 버전 `2.13.0`

이블린 소스에는 `torch.jit.script`, `torch.jit`, 직접 `torch.load` 호출이 없다. 다만 전이 라이브러리 내부 호출 가능성까지 없다고 단정할 수는 없다. 표시된 수정 버전은 Transformers `5.0.0`/`5.3.0`, Torch `2.13.0`이지만, STT/Vision 모델과 `torchaudio==2.11.0`, CUDA 호환성을 검증하지 않은 채 업그레이드하면 런타임을 깨뜨릴 가능성이 크다.

다음 조치: 별도 호환성 브랜치에서 Torch/Torchaudio 버전 정합성, 모델 로드, STT, Vision smoke를 먼저 통과시킨 뒤 upgrade 여부를 결정한다. CI는 이 문서에 기록된 5개 ID만 한시적으로 예외 처리하며 다른 신규 finding은 계속 실패시킨다. 재검토일: 2026-07-22.

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

## P2 — `main.py` 선언형 wiring 밀도

`main.py`는 2,402줄로 목표 범위에 들어왔고 함수 정의와 `global`/`nonlocal`은 0개다. 남은 본문은 대부분 명시적 typed dependency wiring이며, 줄 수를 맞추기 위해 한 줄에 최대 두 인자를 배치해 이전보다 가로 밀도가 높다. 이는 현재 동작 위험보다는 리뷰 가독성의 잔여 비용이다.

다음 조치: 줄 수만을 위한 추가 이동이나 암시적 registry 도입은 하지 않는다. 새 동작은 owner 모듈에 추가하고, `main.py`에는 최대 158자·함수 정의 0개·`global`/`nonlocal` 0개 경계를 유지한다.

## P2 — 설정과 예외 처리의 잔여 분산

Compose의 사용자별 경로 일부는 제거했지만 Python/PowerShell 환경변수 조회와 광범위한 `except Exception`은 여전히 많다. 현재 변경은 전체 설정 통합이나 예외 관측성 문제를 해결하지 않았다.

다음 조치: 설정 스키마 통합과 예외 카운터/최근 오류 시각 노출을 독립 작업으로 진행한다.

## P2 — 자격증명 마운트

Codex 자격증명은 컨테이너에 읽기 전용으로 마운트된다. 읽기 전용은 변조만 줄이고 탈취 범위는 제거하지 않는다. Gateway action 인증 추가만으로 컨테이너 침해 위험이 사라지는 것은 아니다.

다음 조치: 전용 최소 권한 자격증명 또는 짧은 수명 토큰 도입 가능성을 검토한다.

## P2 — 저장공간 정리는 아직 수동

retention 도구는 있지만 정기 dry-run 보고와 승인 기반 정리가 자동 운영되지 않는다. 큰 로그나 Chrome/CDP 프로필이 다시 누적될 수 있다.

다음 조치: 삭제 없는 주기 보고부터 추가하고, 실제 삭제는 별도 승인 경계로 유지한다.
