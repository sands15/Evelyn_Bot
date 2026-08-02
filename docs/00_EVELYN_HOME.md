---
aliases:
  - Evelyn Home
tags:
  - evelyn
  - project-hub
type: navigation
---

# Evelyn Project Home

이 문서는 이블린 개발 문서의 시작점이다. 현재 상태의 원본을 복제하지 않고,
필요한 근거 문서로 이동하는 탐색 허브로만 사용한다.

> [!important]
> 이 Vault는 **개발 프로젝트 지식용**이다. 이블린의 실제 장기 기억 저장소와
> 다르며, 개인 대화·음성·토큰·비밀번호·런타임 로그를 이곳에 기록하지 않는다.

## 지금 상태를 확인할 때

1. [[01_NOW|작업용 현재 문맥]]
2. [[CURRENT_STATE|현재 확인된 상태]]에서 관련 구간만 검색
3. [[ACTIVE_RISKS|남아 있는 위험과 검증 공백]]에서 관련 구간만 검색
4. [[DOCUMENTATION_INDEX|문서별 권위와 사용처]]
5. 관련 코드와 테스트

## 핵심 구조

- 전체 대화·LLM·음성 파이프라인: `../CURRENT_EVELYN_PIPELINE.md`
- 간단한 운영 구조: `../CURRENT_BOT_STRUCTURE.md`
- Minecraft/Voyager 현재 구조: `../CURRENT_EVELYN_ARCHITECTURE.md`
- Core/Extension 경계: `../CORE_ARCHITECTURE_BOUNDARY.md`
- Route 소유권: `../ROUTE_OWNERSHIP_POLICY.md`

## 주요 계약

- [[CONVERSATION_CONTINUITY_CONTRACT|대화 연속성]]
- [[CONVERSATION_INGRESS_RECOVERY_CONTRACT|입력 복구와 중복 방지]]
- [[MEMORY_PROVENANCE_DELETION_CONTRACT|기억 출처·수정·삭제]]
- [[LOCAL_VOICE_ADMISSION_CONTRACT|로컬 음성 입력 권한]]
- [[VOICE_CAPTURE_CONSENT|음성 캡처 동의]]
- [[AUTONOMY_AUTHORIZATION_CONTRACT|자율행동 승인]]
- [[MINECRAFT_WORLD_ACTION_LEASE|Minecraft 실행 권한]]
- [[MINECRAFT_AUTONOMY_READINESS_CONTRACT|Minecraft 준비 상태]]
- [[VISION_EVIDENCE_CONTRACT|화면 근거]]
- [[UI_ACTION_TARGET_CONTRACT|UI 동작 대상 계약]]

## 계획과 목표를 확인할 때

목표 문서는 현재 구현을 의미하지 않는다. 먼저 [[DOCUMENTATION_INDEX]]의
`Target Architecture / Design Direction`과 `Consolidated Plans` 구역을 읽고,
현재 코드·테스트·[[CURRENT_STATE]]와 대조한다.

## 아이디어 임시 보관

아직 검토되지 않은 아이디어와 질문은 [[99_PROJECT_INBOX]]에 적는다. 검토가
끝난 뒤에만 권위 문서나 작업 계획으로 이동한다.

## 작업 기억

- [[01_NOW]]: Codex가 작업 시작 시 읽는 작은 현재 문맥
- [[02_DECISIONS]]: 오래 유지할 결정과 근거
- `worklog/`: 날짜별 검증 결과와 체크포인트
- [[99_PROJECT_INBOX]]: 사용자가 자유롭게 적는 미검토 아이디어

Codex는 큰 문서를 통째로 읽지 않고 키워드로 관련 구간만 찾는다. 작업 중
확인된 결과는 체크포인트에 기록하고, 종료 시 현재 초점이 바뀐 경우에만
`01_NOW`를 갱신한다.

## Codex에게 요청하는 예시

```text
01_NOW를 먼저 읽고 필요한 문서만 검색해서,
현재 상태와 목표 상태를 구분해서 답해줘.
중요한 주장에는 근거 파일과 테스트를 표시해줘.
```

```text
99_PROJECT_INBOX의 항목을 검토해서
이미 구현됨 / 계획에 있음 / 새 제안 / 보류로 분류해줘.
실제 문서 수정 전에는 변경안을 먼저 보여줘.
```
