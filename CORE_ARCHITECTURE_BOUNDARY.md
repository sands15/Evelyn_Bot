# Evelyn Core Architecture Boundary

이 문서는 Evelyn 구조를 정리할 때 흔들리지 않아야 하는 **코어/확장 경계**를 고정하기 위한 기준이다.

## 절대 코어로 유지할 것 (`main.py` 중심)
아래는 Evelyn의 중심 생명선이다. 기본적으로 `main.py` 및 그 직접 코어 흐름에 남겨둔다.

- STT
- router subLLM
- main LLM
- TTS
- 이 네 단계를 잇는 핵심 orchestration

즉, 다음 기본 축은 코어로 유지한다.

```text
STT -> router subLLM -> main LLM -> TTS
```

## 왜 코어로 남겨야 하나
이 경로는:
- 항상 살아 있어야 하고
- 고장나면 전체 시스템이 멈추고
- 디버깅 시 한 흐름에서 추적 가능해야 하고
- 지연/품질/안정성이 가장 민감한 구간이기 때문이다.

따라서 이 축은 확장성보다 **안정성, 추적성, 복구 용이성**을 우선한다.

## 스킬로 빼도 되는 것
스킬은 코어를 대체하는 층이 아니라, 코어 바깥의 확장 층이다.

대표 예시:
- Minecraft
- 특수 도메인 기능
- 외부 executor 연동
- 보조 search workflow
- 특수 follow-up / automation
- 재사용 가능한 부가 기능

## 스킬 시스템의 역할
스킬 시스템은 다음 용도로 사용한다.

- 코어 경로 밖의 확장 기능 추가
- 도메인별 기능 분리
- 외부 executor 연결
- 실험적/선택적 기능 추가
- 사용자별/환경별 확장 포인트 제공

## 코어와 확장의 분리 원칙
### 코어에 남기는 것
- STT 자체
- router subLLM 자체
- main LLM 자체
- TTS 자체
- 이들을 직렬로 묶는 핵심 실시간 파이프라인
- 핵심 실시간 파이프라인의 안정성 중심 orchestration

### 확장으로 분리할 수 있는 것
- Minecraft 같은 특수 도메인 skill
- 보조 search workflow
- 특수 follow-up / automation
- 외부 executor 연결
- 실험적 기능
- 사용자/환경별 커스텀 route

## 현재 구조 방향
- `main.py`
  - 코어 음성 파이프라인 유지
  - 핵심 라우팅/응답 생성/출력 유지
  - skill dispatch의 진입점 역할은 하되, 코어 생명선은 넘기지 않음
- `evelyn_core/runtime/evelyn_core/skills/...`
  - 확장 기능 수용
  - executor 연동
  - 특수 도메인 처리
  - 실험적 기능 분리

## Route ownership 방향
- core-owned route는 보호한다.
- extension 기능은 가능하면 자신의 전용 route를 가진다.
- priority 경쟁이나 병렬 router arbitration보다, route ownership으로 충돌을 방지한다.
- 자세한 기준은 `C:\Evelyn\ROUTE_OWNERSHIP_POLICY.md` 참고.

## 한 줄 원칙
**코어 음성 파이프라인(STT -> router subLLM -> main LLM -> TTS)은 `main.py`에 남기고, `skills`는 그 바깥 확장 레이어로만 사용한다.**
