# Evelyn Docker Compose Migration Blueprint

작성일: 2026-06-12  
목표: 장기 운영 안정성 확보 + Fast Boot 우선 전략을 해치지 않는 단계별 전환

## 결론

장기 운영/재현성은 **Docker/Compose**가 Windows 가상환경만 사용할 때보다 유리하다.
그렇지만 현재 Evelyn의 체감 부팅 개선은 우선순위 1로 **Fast Boot**가 먼저다.  
즉, Compose는 다음 단계의 신뢰성 강화 수단으로 둔다.

## 현 구조 요약

- **Windows PowerShell 런처**
  - `evelyn_core\runtime\launchers\start_*.ps1` 중심으로 서비스 기동.
- **WSL LLM / Vision**
- `main.py` 내에서 모델 포트(9820/9821/9822), Vision, TTS 관련 보조 경로를 사용.
- **Python Bot API + Control-Page**
  - Bot API(8798)와 Control-Page(8799)가 런타임 상태의 핵심 진입점.
- **service_manifest 기반 health/repair**
  - 서비스 목록과 상태 정의로 구조화된 상태 조회/수복을 진행하려는 방향으로 진행 중.

## Docker/Compose가 주는 이점

- 격리와 재현성: 의존 패키지/런타임 충돌을 구조적으로 줄임.
- healthcheck/restart 정책: 서비스 다운 시 자동 재시도/재시작 경로를 선언적 정의 가능.
- 로그 통합: 운영 로그 경로를 단일 정책으로 수집.
- 의존성 충돌 방지: Python/Node/시스템 라이브러리 버전 충돌을 컨테이너 경계로 차단.
- repair 단순화: manifest 기반 상태 조회와 Compose 상태를 정합해 “무엇이 down인지” 즉시 판단.

## Docker 한계(실무에서 큰 비중)

- GPU 모델 cold start: GPU 컨테이너 초기화 비용이 크고, 모델 언로드/리로드 시간은 체감 부팅을 악화시킬 수 있음.
- VRAM 적재 경합: LLM 여러 컨테이너 동시 기동 시 VRAM 경합/OOM 리스크.
- Windows/WSL/Docker GPU pass-through: 런타임 환경이 제각각이어서 경계 설정이 까다로움.
- 로컬 디바이스 접근:
  - 마이크, 오디오 장치, Discord, 일부 장치/IPC 경로는 컨테이너 바운더리 처리 비용이 큼.
- 런타임 민감도: startup order가 미세하게 달라져 기존 fast boot 개선이 반대로 늦어질 수 있음.

## 권장 전략: 하이브리드 점진 전환

1. **Fast Boot를 먼저 완료**해 사용자 체감 부팅을 개선(문서 1).
2. 같은 manifest로 **Compose healthcheck**와 런타임 상태를 정합.
3. 컨테이너화는 low-risk 서비스부터 단계별 적용:
   - 먼저 제어/API 경계(service boundary)가 명확한 것부터.
4. GPU 모델/오디오/장치 의존이 큰 부분은 마지막에 실증 전환.

## 단계별 로드맵

### 단계 0: 기준선 고정
- 현재 Fast Boot 문서/상태 모델 반영 후 측정값 고정(동일 환경 비교 기준).
- 서비스 ID 정합: `service_manifest.json` 기준으로 service name 고정.

### 단계 1: Compose 오케스트레이션 초안
- `docker-compose.fast-control.yml` 같은 파일로 제어 계층만 정의.
- 핵심:
  - `control_page`, `bot_api`만 포함.
  - health endpoint(`/api/health`, `/api/control-page/state`)를 기반으로 `depends_on` + restart 정책 구성.
- 목표: 운영에서 실행/중단이 쉬운지, 로그/상태 확인이 안정적인지 검증.

### 단계 2: Control-Page / Bot API 완전 전환
- 컨테이너 환경 변수, 포트 바인딩, 볼륨(로그/캐시) 정리.
- Runtime health/repair를 compose 상태와 일치:
  - manifest의 `required`, `port`, `check` 항목과 compose의 `healthcheck`를 one-to-one로 연결.
- 이 단계는 **사용자 제어면(8798/8799) 안정화**가 목적.

### 단계 3: TTS / Vision 분리
- TTS/vision은 비교적 덜 상태 민감하거나 독립성이 높은 편이면 컨테이너로 분리.
- 오디오/카메라 접근 경로 점검(필요 시 볼륨/mount, device policy).
- warmup 실패 시 Fast Boot가 제시한 degraded response가 여전히 정상 동작하는지 확인.

### 단계 4: Main/Router/Sub LLM 마지막 검증 전환
- GPU 모델 컨테이너화는 마지막 단계로 둔다.
- 이유: cold start, VRAM 충돌, WSL2/GPU pass-through 리스크가 가장 큼.
- 전환 전 필수 체크:
  - 컨테이너별 모델 캐시 경로 고정
  - GPU 디바이스 노출 정책(3090/4060 선택)
  - 시작 시간/동시 요청 처리/회복 시간 비교

## 서비스별 컨테이너화 적합도

| 서비스 | 권장 우선순위 | 컨테이너 적합도 | 핵심 리스크 | 전환 기준 |
|---|---|---|---|---|
| `control_page` | 1 | 높음 | 정적 설정 관리 | Fast Boot 상태 API와 호환되면 바로 가능 |
| `bot_api` | 1 | 높음 | 외부 라우팅 의존(Discord/REST) | /api/control-page/state가 완전 정상일 때 |
| `tts` | 2 | 보통~높음 | 오디오 디바이스/샘플레이트 차이 | 로컬 마이크/스피커 경로가 아닌 오디오 API 기반일 때 |
| `vision` | 2 | 보통 | 카메라/윈도우 API 접근 | 장치 passthrough 정책 검증 후 |
| `main_llm` | 4 | 낮음 | GPU memory/부팅 지연 | 단독 캐시/측정 가능한 warmup 성능 확보 후 |
| `router_llm` | 4 | 낮음 | 네트워크 지연/초기화 의존성 | main_llm와 동일 채널 검증 |
| `sub_llm` | 4 | 낮음 | 병렬 모델 요청 처리량 | 실서비스에서 메모리 여유 확보 후 |
| `voyager` | 3 | 중간 | 게임/로컬 인터페이스 의존 | 실제 운영 경로에서 브리지 동작 검증 |
| `codex_gateway` | 3 | 중간 | 세션/권한 경로 의존 | 내부 테스트에서 restart 정책 확인 후 |

## Docker Compose healthcheck와 service_manifest 정합

- 권장 정합 규칙:
  - `service_manifest.json`의 `id` = compose service name.
  - manifest `checks` 항목 = compose `healthcheck.test`로 변환.
  - `required` = compose restart/depends 정책의 필수성 표시로 반영.
  - manifest repair 허용 정책과 compose restart 정책을 분리:
    - manifest: 운영자가 허용한 수복 제안.
    - compose: 런타임 자동 회복.
- 정합 체크 예시:
  - manifest: `8798` required + `/api/control-page/state` HTTP 200.
  - compose: `bot_api` healthcheck를 동일 URL로 주기 검사.

## GPU 관련 운영 주의

- NVIDIA Container Toolkit 설치/버전 및 Docker GPU runtime 동작 여부 사전 확인.
- WSL2에서 Docker GPU가 모델 디바이스에 실제로 노출되는지 테스트.
- 모델 저장소는 컨테이너 간 공유 가능한 볼륨 경로로 고정:
  - 모델 캐시 경로 충돌/재다운로드 방지.
- **GPU 이름 확인 필수**:
  - WSL/CUDA 환경에서 `CUDA_VISIBLE_DEVICES`와 실제 장치명이 다를 수 있음.
  - 특히 `RTX 3090` vs `RTX 4060 Laptop GPU`의 노출 순서/명칭을 런타임 로그에 남겨 혼선 차단.

## 검증 명령(문서 기준)

```powershell
# Compose YAML 문법/구성 검증
docker compose -f docker-compose.fast-control.yml config

# 컨테이너 상태 및 헬스
docker compose -f docker-compose.fast-control.yml ps
docker compose -f docker-compose.fast-control.yml ps --services --filter "status=running"

# 서비스별 헬스 직접 확인
docker compose -f docker-compose.fast-control.yml exec -T bot_api python - <<'PY'
import requests
print(requests.get('http://127.0.0.1:8799/api/control-page/state', timeout=5).status_code)
PY

# GPU 확인(런타임 경로 분리 확인)
nvidia-smi
docker compose -f docker-compose.gpu.yml exec main_llm python - <<'PY'
import torch
print(torch.cuda.get_device_name(0))
PY
```

## 최종 방향

1. **Fast Boot**로 체감 부팅(조작 가능 상태 진입)을 먼저 확보한다.  
2. **Compose**로 상태 오케스트레이션을 정교하게 붙인다.  
3. **GPU LLM은 마지막에 검증 전환**한다.

최종적으로는 `controlReady → botApiReady → chat/voice 점진` 구조를 유지한 채,  
Compose 기반 가시성과 복구성으로 운영 난이도를 낮추는 방식이 Evelyn에 맞다.
