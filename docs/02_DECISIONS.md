---
tags:
  - evelyn
  - decisions
type: decision-log
---

# Evelyn Decision Log

오래 유지할 제품·아키텍처·운영 결정을 근거와 함께 기록한다. 이미 존재하는
권위 계약의 세부 결정을 복제하지 말고 해당 문서에 링크한다.

## 기록 형식

```md
## YYYY-MM-DD — 결정 제목

- 상태: 제안 | 승인 | 대체됨
- 결정:
- 이유:
- 근거: [[문서]] 또는 `코드/테스트 경로`
- 영향:
- 대체한 결정:
```

## 2026-08-02 — Obsidian을 개발 작업 기억으로 사용

- 상태: 승인
- 결정: `docs/`를 개발자용 Obsidian Vault로 사용하고 Codex가 Markdown을 직접
  검색·갱신한다.
- 이유: 작업 문맥과 결정 근거를 세션 밖에서도 사람이 검토 가능한 형태로 유지한다.
- 근거: [[00_EVELYN_HOME]], [[01_NOW]], 루트 `AGENTS.md`
- 영향: 큰 문서는 선택적으로 검색하고, 체크포인트와 현재 문맥만 짧게 기록한다.

## 2026-08-02 — 공식 프로젝트 문서 저장소 단일화

- 상태: 승인
- 결정: 공식 프로젝트 문서와 개발자용 Obsidian Vault는
  `C:\Users\Admin\Documents\이블린 - Evelyn\docs` 하나만 사용한다.
  `C:\Evelyn\docs`에는 앞으로 이중 작성하지 않는다.
- 이유: 현재 Git 작업공간, 코드·테스트 변경 이력, 루트 `AGENTS.md`의 문서 계약을
  같은 저장소 안에서 함께 검토하고 체크포인트하기 위해서다.
- 근거: 사용자 지정, 루트 `AGENTS.md`, 현재 Git 작업공간
- 영향: 과거 `C:\Evelyn\docs`에만 있는 유효 문서는 필요할 때 한 번 비교·이관하고,
  이후 모든 문서 갱신은 이 저장소의 `docs/`에서 관리한다.

## 2026-08-02 — 코딩 작업에 Ponytail full 적용

- 상태: 승인 — 정량 종료 보고 조항은 아래 결정으로 대체
- 결정: 모든 코딩 작업은 기존 구현 재사용, 표준 기능, 최소 코드 순서의
  `ponytail full`을 기본으로 한다. 작업 종료 보고에는 시작 전에 기록한 기준안과
  최종 생산 코드 변경량을 비교한 감소율, 변경 파일 수와 새 의존성 수를 포함한다.
- 이유: 제품 보장을 유지하면서 중복 추상화·미래용 설계·불필요한 의존성을 줄인다.
- 근거: 사용자 지정, `ponytail` skill, [[01_NOW]]
- 영향: 보안, 데이터 보존, 명시된 계약, 신뢰 경계 검증과 이를 고정하는 테스트는
  감소 대상으로 보지 않는다. 기준안이 없는 기존 작업은 수치를 꾸며내지 않고
  `산정 불가`로 표시한다.

## 2026-08-02 — Ponytail 정량 종료 보고 생략

- 상태: 승인
- 결정: 코딩 작업의 `ponytail full` 적용은 유지하되, 작업 종료 때 감소율·변경량·
  절감 수치를 별도 항목으로 보고하지 않는다.
- 이유: 최소 구현 원칙은 작업 방식으로 유지하되 결과 보고는 구현·검증·남은 위험에
  집중한다.
- 근거: 사용자 지정, [[01_NOW]]
- 영향: 보안, 데이터 보존, 명시 계약과 검증을 줄이지 않는 기존 예외는 그대로다.
  필요한 경우 코드 diff 자체는 검토하되 Ponytail 성과 지표로 포장하지 않는다.

## 2026-08-02 — Host capture 증거는 세대·목적 제한 HMAC으로 인증

- 상태: 승인
- 결정: 공유 artifact를 지나는 capture owner lease, Bridge status와 Supervisor stop
  evidence는 서로 다른 HMAC domain을 사용한다. 키는 공식 launcher 세대마다 새로
  만들고 Control Page, Host Supervisor, Local Bridge에만 전달한다.
- 이유: 공유 폴더의 read/write 권한만으로 캡처 권한이나 physical OFF 증거를 위조할
  수 없어야 하며, raw owner/lease 값이나 음성 데이터를 저장할 필요도 없어야 한다.
- 근거: [[VOICE_CAPTURE_CONSENT]],
  `evelyn_core/runtime/evelyn_core/voice_capture_consent.py`,
  `tests/runtime/test_host_supervisor.py`
- 영향: artifact는 content-free digest와 인증 tag만 보존한다. 키 누락·오류,
  cross-scope replay, stale·replacement와 status rollback은 fail-closed하며 일반
  자식 프로세스에는 키를 상속하지 않는다.
