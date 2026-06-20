# Evelyn 운영 상태 진단 가이드 (운영용)

Last reviewed: 2026-06-16

## 1) 상태코드 사전 (실운영 우선 사용)

- `CP_UP_BOT_DOWN`: Control-Page는 켜졌지만 Bot API가 내려간 상태.
- `CP_BOT_PROXY_ERROR`: Bot API 계약 조회 실패(네트워크/타임아웃/서버 에러).
- `CP_BOT_PROXY_TIMEOUT`: Bot API 계약 조회 타임아웃.
- `CP_BOT_HTTP_401`: Bot API 인증 실패(401).
- `CP_BOT_HTTP_403`: Bot API 권한/접근 제어 실패(403).
- `CP_BOT_HTTP_404`: Bot API 경로 오류(404).
- `CP_BOT_HTTP_500`: Bot API 내부 오류(500).
- `CP_BOT_STATE_NOT_READY`: Bot API는 응답했지만 `ready` 판정 못함(8798 계약 준비 안 됨).
- `CP_BOT_STATE_NOT_DICT`: Bot API 응답 형식이 계약 규격이 아님.
- `BOT_API_DOWN_WITH_CONTROL_PAGE_UP`: Control-Page는 동작하지만 Bot API가 응답 없음.
- `BOT_API_PARTIAL`: Bot API 일부 요청만 동작.
- `CP_BOT_STATE_*` + `CP_CONTROL_RUNTIME_*`: Runtime 캐시/상태 동기화 계열 문제.
- `CP_MAIN_LLM_DOWN`, `CP_ROUTER_LLM_DOWN`, `CP_SUB_LLM_DOWN`: 해당 LLM 중 단일 모듈 비정상.
- `CP_TTS_DOWN`, `TTS_DOWN`: TTS 비정상.
- `VOYAGER_DOWN`: Voyager 비정상.
- `CP_CODEX_GATEWAY_DOWN`, `CODEX_GATEWAY_DOWN`, `CODEX_GATEWAY_ACTION_FAILED`: 코드 실행/게이트웨이 문제.

## 2) 재시작 기준 (권장)

- `Control-Page` 또는 `main.py`에서 `botReady`가 false면 `/voice continuity` 상태만 점검하고, 바로 시스템 재시작은 제한한다.
- `CP_UP_BOT_DOWN`, `CP_BOT_HTTP_*`, `CP_BOT_PROXY_TIMEOUT`, `CP_BOT_STATE_NOT_READY`, `BOT_API_PARTIAL`가 3회 이상 반복되면:
  - `/voice continuity reset` 금지(확인 필요 없음).
  - `/restart` 후 20초~60초 내 재확인.
- `CP_MAIN_LLM_DOWN`, `CP_ROUTER_LLM_DOWN`, `CP_SUB_LLM_DOWN`은 `runtime.status`에서 LLM 상태 복구 후 자동 알림 후 재시도.
- `CP_TTS_DOWN`은 음성 출력 경로가 불가하므로 TTS 재설정을 우선하고, 곧바로 완전 재시작은 보류.
- `CP_RUNTIME_REFRESH_ERROR`는 Control-Page 캐시 문제 우선. 화면 새로고침 + 1회 재조회 후 재발 시 재시작 후보.

## 3) 예상 조치 메시지(짧은 사용자 안내)

- `Bot API is down`:
  "이제 Bot API를 재시작하고 1분 뒤 상태를 다시 확인해줘."
- `contract failed`:
  "Bot API 계약 점검에서 실패했어. 재시작 후에도 계속되면 8798 경로(포트/엔드포인트/권한)부터 확인할게."
- `LLM not responding`:
  "LLM 서비스가 느리거나 멈춘 상태야. 10~20초 뒤 다시 확인해줘."
- `TTS down`:
  "음성 출력이 멈춘 상태야. 현재는 텍스트 응답만 먼저 보여줄게."
- `Control-Page down`:
  "Control-Page가 일시 정지돼 있어. 런타임 재시작이 필요할 수 있어."

## 4) 8798 계약 가드 (고정 문구)

- 계약 성공이 아니면 `botReady`는 true가 아님.
- 판단 순서: `8798 포트 열림` → `Bot API 8798 응답/HTTP` → `state=ready`.
- 기본 표기:
  - 포트 미오픈: `bot down (8798 port closed)`
  - HTTP 오류: `bot down (8798 HTTP-401)` 형태
  - 계약 실패: `bot down (8798 contract fail)`

## 5) 바리인 연속성(예외)

- `/voice continuity`는 5회 시도 기준으로 성공/실패 스탬프를 보여줌.
- 로그 이벤트 포맷은 `index:event=<항목>:status=<성공/실패>:code=<원인코드>:label=<라벨>`로 고정.
- 원인카테고리: 끊김, 지연, 오탐, 재연결 실패, 선점, 인터럽트 절단.
