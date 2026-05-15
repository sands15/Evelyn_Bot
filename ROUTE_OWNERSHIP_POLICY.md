# Evelyn Route Ownership Policy

이 문서는 route 충돌과 skill 선택 혼선을 줄이기 위해 Evelyn의 route ownership 규칙을 고정한다.

## 기본 원칙
- router는 한 번 판단해서 **하나의 route**를 낸다.
- 같은 route를 여러 skill이 공유하는 구조는 기본적으로 지양한다.
- 우선순위(priority) 경쟁이나 병렬 router arbitration 대신, **route ownership**으로 충돌을 막는다.

## Route 종류
### 1. Core-owned routes
코어 시스템이 소유하는 route.
이 route들은 기본적으로 `main.py` 중심의 보호 경로와 강하게 연결된다.

현재 core-owned route:
- `main_direct`
- `policy_short_circuit`
- `search_executor`
- `delivery`

규칙:
- 외부 확장 skill이 같은 route를 공유하지 않는 것을 원칙으로 한다.
- 이 route들은 코어 동작 안정성을 우선한다.
- 필요하면 내부 구현은 skill 호출을 사용할 수 있어도, **소유권은 코어에 있다**.

### 2. Extension-owned routes
확장 기능이 소유하는 route.

예:
- `minecraft`
- 앞으로 추가될 특수 도메인 route

규칙:
- 외부/도메인 skill은 가능하면 자신만의 전용 route를 가진다.
- 기존 core-owned route를 재사용해 끼어드는 대신, 새 route를 명시적으로 만든다.

## 추천 구조
### 안전한 구조
- `main_direct -> conversation`
- `search_executor -> search`
- `delivery -> delivery`
- `minecraft -> minecraft`

즉 route 1개당 대표 skill 1개를 기본 구조로 둔다.

### 피해야 할 구조
- 같은 route에 여러 skill이 동시에 매달리는 구조
- route 충돌을 priority로 억지 해소하는 구조
- 여러 router/sub-router가 병렬로 경쟁해서 route를 정하는 구조

## 병렬 판단 정책
현재는 병렬 router arbitration을 도입하지 않는다.

이유:
- 디버깅 복잡도 증가
- 응답 지연 증가
- 같은 입력에서 선택 이유 설명이 어려워짐
- 코어 파이프라인 안정성 저하 가능성

현재 방향:
- router는 한 번 판단
- route는 하나 선택
- ownership으로 충돌 방지

## 확장 추가 시 규칙
새 확장 기능이 필요할 때:
1. 먼저 기존 core-owned route를 공유해야 하는지 검토한다.
2. 가능하면 공유하지 말고 새 extension-owned route를 만든다.
3. 해당 route를 담당하는 대표 skill을 1개 둔다.
4. route 충돌을 priority로 해결하지 말고 구조로 해결한다.

## 한 줄 원칙
**priority 경쟁보다 route ownership을 우선하고, core-owned route는 보호하며, 확장은 가능하면 전용 route를 가진다.**
