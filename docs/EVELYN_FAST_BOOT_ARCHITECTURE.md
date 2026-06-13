# Evelyn Fast Boot Architecture

생성일: 2026-06-12  
대상: 운영 판단 및 실행 순서 정리(코드 수정 전 설계 문서)

## 목적

부팅 완료를 기다리게 만드는 방식에서 벗어나, **전체 ready가 아니라 조작 가능한 상태를 먼저 확보**한다.  
사용자에게는 빠른 제어권과 상태 투명성을 제공하고, 무거운 모델 초기화를 뒤로 밀어 실제 작업 가능한 범위를 선형적으로 확장한다.

## 현재 병목(Observed)

- `C:\Evelyn\evelyn_core\runtime\launchers\start_bot.ps1`
  - Bot API 실행 전에 `Main LLM(9820)`, `Router LLM(9822)`, `Sub LLM(9821)`, `TTS(8880)` 포트 대기를 순차 수행.
  - 모든 엔드포인트가 살아날 때까지 launch가 멈춰 있어 초기 노출이 지연됨.
- `C:\Evelyn\main.py`
  - startup에서 Opus → STT warmup → Main LLM warmup → TTS warmup → local mic, vision watch, background tasks를 초기에 수행.
  - 이 때문에 process가 “실행 중”이더라도 외부에서 `작동 준비`로 보일 때까지 대기해야 함.
- `Control-Page(8799)`는 빠르게 뜰 수 있으나 `Bot API(8798)`가 늦으면 사용자 입장에서 정지로 오인됨.
- 결과: “실제 동작 가능성”과 “완전 준비 상태”를 동일한 기준으로 보던 운영 판단이 붕괴됨.

## 핵심 원칙

1. **Control-Page 즉시 노출**: 8799 UI/API(상태 조회)는 최대한 빨리 공개한다.  
2. **Bot API 선기동**: 제어 채널이 먼저 살아야 `/api/control-page/state` 같은 운영 신호가 의미를 가짐.  
3. **모델/STT/TTS lazy warmup**: `chat`, `voice`, `vision` 요청 시 필요 시점에서 초기화하거나 백그라운드로 비차단 실행.  
4. **health/repair가 전체 상태를 안내**: “완전 준비됨” 대신 “현재 조작 가능 범위”를 정확히 노출한다.

## 상태 모델(운영 기준)

- `controlReady`
  - `8799` 기본 응답 가능, 상태 페이지 접근 성공.
  - 목적: UI 진입 및 최소 관리.
- `botApiReady`
  - `8798` 정상 응답 + `/api/control-page/state`를 통해 현재 상태 조회 가능.
  - 목적: 제어/diagnostic API 사용 가능.
- `chatReady`
  - 기본 채팅/대화 경로가 동작, STT 또는 LLM이 warm된 상태.
  - 예: `send_chat` 라우트가 실제 응답 생성 가능.
- `voiceReady`
  - TTS 음성 합성 파이프라인이 준비되어 음성 출력 가능.
- `fullReady`
  - 필수/선택 서비스와 모델 전부 warmup 완료.
  - 운영 모드 상 “모든 기능 사용 가능” 상태로 전환.

`controlReady`/`botApiReady`는 **운영 가능한 최소 바운더리**이고,  
`chatReady`/`voiceReady`는 **서비스 가용성**, `fullReady`는 **완전 가용성**으로 분리한다.

## 구현 단계 (Code 변경 없이 바로 반영 가능한 운영 범위 포함)

### 1) 플래그 정합

- 환경변수: `EVELYN_FAST_BOOT`
  - 기본값: `1`(활성)
  - Fast boot 비활성 시 기존 동작(완전 대기)으로 fallback.
- 목표 상태:
  - `EVELYN_FAST_BOOT=1`: 시작 지연 최소화, readiness 단계화.
  - `EVELYN_FAST_BOOT=0`: 기존 방식 유지(디버그/비교).

### 2) `start_bot.ps1` 대기 optional화

- 기존: Main/Router/Sub/TTS 포트 순차 대기 후 main 실행.
- 제안:
  - `EVELYN_FAST_BOOT=1`이면 `8799` 준비 확인만 먼저 진행하고 `Bot API(8798)` 시작으로 즉시 진입.
  - 모델 포트/헬스는 제한 시간(timeout) 내에서 **백그라운드 폴링**:
    - `EVELYN_FAST_BOOT_MODEL_READY_TIMEOUT`으로 보조 제한.
    - timeout 초과 시 fail-fast가 아니라 상태만 기록.
- 추가: 기존 동작 유지 토글로 A/B 비교 가능한 운영 기준을 남김.

### 3) `main.py` warmup background task화

- 기존 startup 초기화 코드를 4개 단계로 분리:
  1. control/page API 및 상태 저장소 초기화 (동기)
  2. `create_readiness_tracker` 기초 상태 등록 (`controlReady`, `botApiReady`)
  3. Opus/STT/Main LLM/TTS warmup을 `asyncio.create_task`로 분리.
  4. 각 warmup 종료 시 readiness를 갱신.
- 핵심은 `chat`/`voice` 요청이 들어왔을 때 warmup을 트리거하고, 동시 요청 시 중복 실행을 막는 lock/token 방식 적용.

### 4) 기능별 degraded response 정합

- 요청/라우트별로 “필요 조건”을 명시:
  - STT 필요 라우트: `chatReady` 미달성 시 안내 + 상태 코드(권장: 503) + 재시도 힌트 제공.
  - TTS 필요 라우트: `voiceReady` 미달성 시 텍스트 응답 모드(또는 큐잉)로 fallback.
  - vision/agent 연동: 모델 미상태면 상태 API는 성공해도 기능은 거절.
- 즉, 준비되지 않은 기능은 숨기지 않고, **정확한 사유로 제한**한다.

### 5) UI boot progress 분리

- Control-Page에서 `bootPhase` 노출:
  - `controlReady`, `botApiReady`, `chatReady`, `voiceReady`, `fullReady` 각 타임스탬프.
  - “대기 중/완료/불가(의존성 누락)”를 구분해 사용자 오해 감소.
- `/api/control-page/state` 응답에 현재 모드(`EVELYN_FAST_BOOT`)와 마지막 갱신 시간을 포함.

## 실패 모드 / 리스크

- **ready 과장 리스크**: `controlReady`를 `fullReady`로 오인하여 UX 오해 유발.
  - 대응: 상태 키마다 `ready_level` 명시, 메시지 템플릿 통일.
- **요청 시 의존성 없는 기능 처리 오류**: chat 요청은 chatReady만 필요, TTS는 voiceReady가 필요 등.
  - 대응: 라우트별 dependency matrix를 문서화/검증.
- **STT/LLM lazy warmup 레이스**: 동시 호출 시 초기화가 여러 번 실행되거나 리소스 경합.
  - 대응: single-flight lock + 큐 + 타임아웃 기반 상태 전이.
- **부팅 중 요청 폭주**: startup 바로 직후 다량 요청 시 내부 큐 메모리/타임아웃 악화.
  - 대응: readiness 기반 리젝/대기 알림 + rate limit.
- **모니터링 과부하**: 포트 폴링 주기를 너무 짧게 하면 자기 부하 유발.
  - 대응: jitter와 지수 백오프 적용.

## 테스트/검증

### 단위/통합 테스트

- `tests/runtime/test_fast_boot_readiness.py`
  - 상태 전이(control → bot → chat → voice → full) 단위 검증.
- `tests/runtime/test_boot_warmup_race.py`
 - STT/LLM lazy warmup 동시 호출 시 single-flight가 작동하는지 점검.
- `tests/ui/test_control_page_boot_state.py`
  - `/api/control-page/state`의 ready level/문구 일관성 확인.

### 실행 검증(운영)

```powershell
# 1) 기동 직후 control-page만 살아있는지 확인
Get-NetTCPConnection -LocalPort 8799 -State Listen

# 2) bot API 포트 확인(짧은 타임내 8799는 살아도 8798은 지연될 수 있음)
Test-NetConnection -ComputerName 127.0.0.1 -Port 8798

# 3) runtime_health 상태 조회
Invoke-RestMethod -Uri http://127.0.0.1:8799/api/control-page/state | ConvertTo-Json -Depth 8

# 4) 모델 미다운 상태에서 control state가 여전히 반환되는지 확인
Invoke-RestMethod -Uri http://127.0.0.1:8799/api/control-page/state | ConvertFrom-Json | Select-Object -ExpandProperty services
```

### 수치 기준(권장)

- `controlReady` 도달 시간: 기존 대비 눈에 띄는 단축(타겟: 최대한 빠르게, 수 초 이내).
- `botApiReady` 가시성: 사용자 제어는 즉시 가능.
- `chatReady` 전환: 사용자 요청으로 warmup이 시작되고 10~30초 내 전이될 것(환경 따라 조정).
- `voiceReady` 전환: TTS 의존 기능 테스트.

## 운영 판단 요약

현재 병목은 “실제 동작 준비”의 총합이 아니라 “기동 순서 설계”에서 발생한다.  
따라서 **부팅 속도 1순위는 Docker/Compose 전환이 아니라 Fast Boot 구조**로 가져가는 것이 맞다.  
Docker는 2차 레이어에서 신뢰성/복제성을 붙이는 방향으로 가야 한다.
