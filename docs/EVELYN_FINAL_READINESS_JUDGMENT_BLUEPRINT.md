# EVELYN 최종 완성도 판단 로직 청사진

작성일: 2026-06-12

목표: 이블린의 완성도를 “기능이 있는가”로 판단하지 않고, 정훈이 터미널을 보지 않아도 **켜고, 쓰고, 고치고 신뢰할 수 있는 상태인지**로 판단한다.  
최우선 규칙: `8799 = Control-Page`, `8798 = Bot API(main.py 본체)`는 절대 혼동되지 않아야 한다.

---

## 1. 판단 철학

### 1.1 핵심 질문
이블린의 완성도는 다음 질문을 만족해야 올라간다.

1. 정규 경로만으로 서비스를 실행했을 때, 운영자가 왜 안 되는지 즉시 알 수 있는가  
2. 상태 메시지가 실제 실행 상태와 일치하는가(특히 `8799`와 `8798` 분리)  
3. 문제가 발생했을 때 “어느 버튼, 어떤 순서, 어느 수준의 위험”로 복구할 수 있는가  
4. 자동 복구보다 “예측 가능한 수동 복구”가 더 중요할 때도 실패가 안전한가

### 1.2 완성도 산정의 기본 원칙
- 기능 유무는 0차 평가: 기본 값.
- 운영 완성도는 다음 4축으로 평가:
  - **분리된 서비스 정체성 일치성**
  - **상태판독의 결정론적 정확성**
  - **실패 진단의 이해 가능성**
  - **복구 경로의 보수성**
- “페이지가 열리는 것”은 **최종 가용성의 일부**일 뿐이다.  
  `8799`가 살아도 `8798`이 죽어 있으면 실제 업무는 불완전하다.

---

## 2. Service Identity / Port Contract

### 2.1 기본 규약
모든 판단은 다음 계약서를 기준으로 한다.  
예외는 기록(사유/시각/변경자) 없이 허용되지 않는다.

| ID | 역할 | 기본 포트 | 필수성 | 책임 주체 | health 엔드포인트 |
|---|---|---:|---|---|---|
| `control_page` | 사용자 UI 노출 | `8799` | 핵심 | `control_page_server.py` | `/health` 또는 페이지 응답 확인 |
| `bot_api` | Evelyn 본체 API / `main.py` | `8798` | 핵심 | `main.py` | `/api/control-page/state` |
| `main_llm` | 주요 응답 모델 | `9820` | 핵심 | LLM 서브시스템 | `/v1/models` |
| `router_llm` | 라우팅 모델 | `9822` | 핵심 | LLM 서브시스템 | `/v1/models` |
| `sub_llm` | 보조 모델 | `9821` | 핵심 | LLM 서브시스템 | `/v1/models` |
| `tts` | 음성 합성 | `8880` | 핵심 | TTS 레이어 | `/health` |
| `vision` | 비전 보조(선택) | `8891` | 선택 | Vision 레이어 | `/health` |
| `voyager` | 마인크래프트/브리지 보조 | `8765` | 선택 | Voyager 레이어 | 서비스별 |
| `codex_gateway` | 코덱스 연계 | `8787` | 선택 | 연계 게이트웨이 | 서비스별 |

### 2.2 고정 규칙
- `8799`는 언제나 Control-Page의 포트이다.
- `8798`은 언제나 Bot API의 포트이다.
- 두 포트를 동일 서비스로 바인딩하지 않는다.
- `top-level ok`, `botReady`, `mainReady`는 반드시 위 포트 상태와 논리적으로 일치해야 한다.

### 2.3 위반 검출 규칙
아래가 하나라도 참이면 즉시 `critical_contract_violation`을 띄워야 한다.
- 두 ID가 같은 포트를 소유하는 경우(예: 8799/8798 역전)
- `bot_api`가 내려가 있는데 `control_page`는 up으로 표시됨
- `8799 up`만을 근거로 `main.py` 실행이 완료된 것으로 판단함
- 런처가 `control_page`만 켜고 `main.py`를 시작하지 않음

---

## 3. Health State Taxonomy

### 3.1 상태 값

| 상태 | 정의 | `ready` |
|---|---|---:|
| `ready` | 핵심 서비스가 기대 동작을 수행 가능 | `True` |
| `degraded` | 핵심은 대체로 동작하나 일부 장애/지연 존재 | `False` |
| `down` | 필수 서비스 중 하나 이상 실패 | `False` |
| `recovering` | 최근 시작/재시작 직후로 검사 중 | `False` |
| `unknown` | 검사 자체가 불가 또는 계약 위반 | `False` |

### 3.2 서비스별 상태 세분화
- `up`/`partial`/`down`/`timeout`/`unreachable`/`error`를 내부 체크 결과로 보관.
- Control-Page/Bot API는 상태 판별 시 반드시 **TCP + HTTP**를 함께 본다.
  - `8799` 포트가 열려 있어도 `/` 혹은 `/health`가 응답하지 않으면 `partial` 또는 `degraded`.
  - `8798` 포트가 열렸더라도 `/api/control-page/state` 응답 실패면 `partial`.

### 3.3 전체 상태 집계 규칙
- 전체 상태 `ready`는 다음이 모두 참일 때만 True:
  1) `control_page` up  
  2) `bot_api` up  
  3) 핵심 LLM 3개 up  
  4) `tts` up  
  5) 치명적 진단 없음
- 위 조건 1~4 중 하나라도 False면 `down` 또는 `degraded`.
- `recovering`은 최근 시작 후 30초 이내거나 재시작 추적기가 가동 중일 때 설정.

---

## 4. Readiness 판단 알고리즘

### 4.1 입력
- `service_manifest` (포트, 헬스 경로, 필수성)
- `probe_result`: 각 서비스에 대한 TCP/HTTP 검사 결과
- 최근 상태 캐시(마지막 성공 시각, 마지막 치명 진단)

### 4.2 판정 알고리즘 (의사코드)

```python
def evaluate_readiness(snapshot):
    services = load_and_validate_manifest()
    probe_map = run_checks(services)  # TCP + HTTP

    for s in services:
        s.ready = classify_service_ready(s, probe_map[s.id])
        s.state = classify_service_state(s, s.ready, probe_map[s.id])

    # 계약 검증(우선순위 제일 높음)
    if not contract_ok(services, probe_map):
        return overall_state="down", ready=False, reason="contract_violation"

    core_ok = all(services[id].ready for id in CORE_REQUIRED_IDS)

    # 핵심 분기 (8799/8798 분리 규칙)
    cp = services["control_page"]
    bot = services["bot_api"]

    if cp.ready and not bot.ready:
        return overall_state="degraded", ready=False, reason="CP_UP_BOT_DOWN"

    if cp.ready and bot.ready:
        core_ready = all(services[id].ready for id in ["main_llm", "router_llm", "sub_llm", "tts"])
        if core_ready:
            if has_optional_error():
                return overall_state="degraded", ready=False, reason="OPTIONAL_DEGRADED"
            return overall_state="ready", ready=True, reason="ALL_CORE_OK"
        return overall_state="degraded", ready=False, reason="CORE_SERVICE_DOWN"

    if cp.ready is False and bot.ready is False:
        return overall_state="down", ready=False, reason="CONTROL_AND_API_DOWN"

    return overall_state="recovering", ready=False, reason="STARTUP_OR_RECOVERING"
```

### 4.3 Top-level `ok` 생성 규칙
- `ok=True`는 `overall_state == ready`일 때만.
- `ok=False`면 UI는 반드시 `state_summary`를 노출.

### 4.4 모순 방지 규칙
- `botReady=true`이면서 `bot_api` 진단이 down이면 실패.
- `Control-Page 업` 문구는 `control_page`가 up일 때만 표시.
- `top-level ok`와 `botReady`는 서로 모순될 수 없음.

---

## 5. Failure Diagnosis Matrix

| 코드 | 조건 | 상태 | 사용자 메시지 | 우선 조치 |
|---|---|---|---|---|
| `CP_UP_BOT_DOWN` | `control_page=up`, `bot_api=down` | degraded | Control-Page는 열렸지만 Evelyn 본체가 응답하지 않습니다. | `8798` Bot API 재시작 |
| `CP_DOWN_BOT_UNKNOWN` | `control_page=down` | down | Control-Page가 응답하지 않습니다. 페이지에 접속할 수 없습니다. | Control-Page 재시작 |
| `BOTALIVE_BUTHTTP_DOWN` | `bot_api tcp up`, `bot_api http timeout` | degraded | Bot API 포트는 열려 있으나 상태 API가 멈췄습니다. | `main.py` 재시작 또는 로그 확인 |
| `CP_BOT_PROXY_TIMEOUT` | page proxy 타임아웃, bot 직접 호출은 성공 | degraded | Control-Page→Bot API 경로가 지연되어 반응이 끊겼습니다. | 프록시 타임아웃/패킷 점검 |
| `LLM_MODEL_ENDPOINT_MISSING` | `main/route/sub` 중 하나가 down | degraded | 해당 LLM 엔진이 응답하지 않습니다. | 해당 포트/프로세스 재기동 |
| `TTS_PIPE_BLOCKED` | tts up이지만 PCM/재생 미완료 반복 | degraded | 답장 텍스트는 생성되나 재생이 지연됩니다. | TTS 재시작/디스패처 상태 점검 |
| `MANIFEST_PORT_COLLISION` | 8799/8798 또는 필수포트 충돌 | down | 포트 계약이 깨져 서비스 구성이 모순됩니다. | 설정 및 런처 점검 |
| `MANUAL_INTERVENTION_REQUIRED` | `unknown` 또는 반복 실패 | unknown | 증상이 반복되어 수동 개입이 필요합니다. | 가이드 모드로 복구 절차 제공 |

### 5.1 진단 우선순위
1. `critical_contract_violation`
2. `down` 상태 핵심 서비스
3. `partial` / `timeout`
4. `optional` 장애

---

## 6. Launcher / Restart / Shutdown 판단 로직

### 6.1 Launcher 시작 정책
1. 계약 로드 후 포트-서비스 맵을 확정한다.
2. Control-Page(`8799`)와 Bot API(`8798`)를 독립 체크.
3. `control_page`가 안 켜져 있으면 시작.
4. `bot_api`가 안 켜져 있으면 시작(`control_page` 상태와 상관없음).
5. 필수 LLM/TTS는 기본값으로 확인, 실패면 상태만 표시하고 강제 시작하지 않는다.

즉, `8799 up`만 보고 `main.py` 실행을 건너뛰면 안 된다.

### 6.2 Restart 규칙
- **저위험**: 재시도(reprobe), 캐시 갱신
- **중위험**: 개별 서비스 재시작 (`main.py`, `tts`, `control_page_server`)
- **고위험**: 관련 하위 프로세스 정리 + 재기동(사용자 승인 필요)

재시작 조건:
- `required` 상태가 연속 2회 `down`
- 또는 `recovering`이 임계시간(예: 120초) 초과

### 6.3 Shutdown 규칙
- 종료 시 명시 종료 순서:
  1) 부가 서비스 optional(vision/voyager/codex_gateway)
  2) Bot API(`8798`)
  3) Control-Page(`8799`)
- OpenClaw/Codex/공용 런타임은 명시적 제외 목록으로 보호.
- `kill python.exe` 같은 광역 종료 금지. 포트+명령행 근거가 있는 프로세스만 대상.

---

## 7. Control-Page UI 상태와 표시 문구

### 7.1 요약 배너
- `ready`: `✅ Evelyn이 정상 상태입니다.`
- `degraded`: `⚠️ 일부 서비스가 불안정합니다. 동작 가능 범위를 확인하세요.`
- `recovering`: `🔄 부팅/복구 진행 중입니다. 잠시 후 자동 재점검됩니다.`
- `down`: `🛑 핵심 서비스가 멈췄습니다. 시작이 필요합니다.`
- `unknown`: `❓ 상태를 확인할 수 없습니다. 점검을 시작합니다.`

### 7.2 서비스 라인 가시성
아래 순서로 표시(필수 먼저):
1. Control-Page (`8799`)
2. Bot API (`8798`)
3. Main LLM / Router LLM / Sub LLM
4. TTS
5. 선택 서비스

각 라인은 다음을 함께 표시:
- 상태 배지 (`READY / DEGRADED / DOWN / UNKNOWN`)
- 마지막 업데이트 시각
- 포트
- 최근 1건 진단

### 7.3 문구 예시(필수)
- `control_page=up`, `bot_api=down`:  
  `Control-Page는 열렸지만 본체(Bot API)가 응답하지 않습니다. Bot API를 시작하거나 프로세스 상태를 확인해 주세요.`
- `bot_api=up`, `tts=down`:  
  `텍스트 응답은 가능하지만 음성 출력이 지연/실패할 수 있습니다.`
- `unknown` 진단:  
  `상태 점검에 실패했습니다. 마지막 성공 점검 후 변경이 있었는지 다시 확인합니다.`

### 7.4 불일치 금지 규칙
- `botReady=true`인 상태에서 “Bot API unavailable” 같은 문구를 노출하지 않는다.
- `control_page up`이 아닌데도 `Bot API available` 같은 문구를 노출하지 않는다.

---

## 8. Self-Repair / Guided Repair 방향

### 8.1 단계별 모드
1. **Guide Only**: 원클릭 액션 없이 “어디를 확인할지” 설명.
2. **Dry-Run**: 실행 전 `어떤 명령을`, `무슨 결과를` 기대하는지 미리 노출.
3. **Confirmed Apply**: 사용자 확인 후 제한된 수동 실행.

### 8.2 우선순위 규칙
- 자동 종료/재시작은 고위험이므로 기본 비활성.
- 우선 `start_if_down` 류의 동작만 제시.
- Bot API 미동작은 사용성이 핵심이므로 상대적으로 우선.

### 8.3 추천 복구 액션 매핑
- CP_UP_BOT_DOWN → “Bot API 시작”
- CONTROL_PAGE_DOWN → “Control-Page 시작”
- BOTALIVE_BUTHTTP_DOWN → “main.py 상태 점검 + 재시작”
- MANIFEST_PORT_COLLISION → “포트 계약 점검(8799/8798 분리 재적용)”

### 8.4 동작 안전성
- 재시작 전 `cooldown`(예: 30초) 및 최근 종료/시작 기록 확인
- 오탐을 줄이기 위해 1회 복구 제안 실패 시 즉시 고위험 조치 제한
- 복구 이력(log)은 언제, 누가, 어떤 상태에서 실행했는지 남김

---

## 9. 테스트/검증 기준

### 9.1 단위 테스트
- 계약 검사
  - `control_page` 기본 포트=8799, `bot_api` 기본 포트=8798 고정
  - 포트 충돌/중복 검출
  - required 필드 누락 검출
- 상태 판정
  - `8799 up + 8798 down` -> `degraded` 및 CP_UP_BOT_DOWN 진단
  - `8798 http timeout` -> `partial`/`degraded`
  - `unknown` 상태에서 `ok=false` 및 문장 존재

### 9.2 통합 테스트
- `/api/control-page/state` 응답이 `runtime.serviceHealth` 포함
- legacy `runtime.services.botReady/mainReady/...`와 신규 health의 모순 없는지 교차 검사
- Control-Page에서 8799/8798 라인 분리 렌더링 확인
- `bot_api` 재시작 제안이 있는지 확인

### 9.3 런타임 점검 시나리오
1) 8799만 켜진 상태에서 시작 시나리오  
2) 8798만 켜진 상태에서 프록시/타임아웃 시나리오  
3) 포트 오버라이드 설정(환경변수)과 기본 포트 복구 시나리오  
4) 선택 서비스 장애 시 전체 ready 유지 여부 확인

### 9.4 합격 기준
- 8799/8798 혼합 오류를 테스트 없이 배포 불가
- top-level ready/OK 와 라우터 메시지 무모순
- 진단 코드와 UI 문구가 1:1 매핑

---

## 10. 점수 단계(65/75/85) 판정 기준

### 10.1 점수 모델 (예시)
- 서비스 분리·정체성 준수: 25점
- 준비도 판정 정확도: 20점
- 진단 정확성/일치성: 20점
- UI 설명력: 15점
- 복구 지도 정확성: 10점
- 테스트 자동화 커버리지: 10점

### 10.2 단계별 게이트

| 점수대 | 판정 | 문턱 조건 |
|---|---|---|
| 65점 이상 | 운영 시작 가능 | 포트 계약 기본 준수, `ok` 모순 제거, 핵심 상태 노출 |
| 75점 이상 | 실사용 신뢰 가능 | `8799/8798` 분리 고정, degraded 진단/문구 정합, 최소 복구 가이드 제공 |
| 85점 이상 | 팀에 넘겨도 설명 가능한 상태 | 테스트 누락 없음, self-repair 가이드 완결, optional 장애 영향 격리, 상태 변동 안정성 |

### 10.3 점수 산정 주의
- 화면이 떠도 `bot_api`가 죽어 있으면 +점수 하락(최소 20점 감점 가중)
- `botReady`와 텍스트 진단 불일치 발생 시 즉시 -15점
- `recovering`을 down로 오판하는 빈도가 높으면 회복성 점수 감점

---

## 11. 구현 단계 로드맵

### Phase 0 (1주차): 기준 수렴
- 본 청사진 반영, 계약서 확정
- 8799/8798 분리 표준 문구/로그 포맷 확정
- 기존 문서와 스키마에서 계약 충돌 항목 정리

### Phase 1 (1~2주): 판정 레이어 정식화
- 서비스 계약 로더/검증 추가
- Health state 판정기(ready/degraded/down/recovering/unknown)
- 모순 금지 규칙(ready/ok/botReady) 적용

### Phase 2 (2~3주): UI 바인딩
- Control-Page 요약 배너 및 서비스 목록 정렬
- 진단 코드별 문구 매핑
- 이전 상태 스키마와 신규 상태 동시 제공

### Phase 3 (3~4주): guided repair
- dry-run 가이드 및 수행 이력
- `CP_UP_BOT_DOWN` 중심의 운영 스크립트 연계
- 실행 전 동의 토큰/확인 단계

### Phase 4 (지속): 품질 강화
- 테스트 자동화 확대(포트 오인, partial, timeout, shutdown 순서)
- 점수 임계값(65/75/85) 모니터링 및 월간 회고 반영

---

## 부록: 최종 판단 규정 한 줄 요약
**정훈이 터미널을 보지 않고도 페이지에서 “내가 지금 무엇을 신뢰하고 쓸 수 있는지”를 즉시 판단할 수 있으면 `ready`,  
그렇지 않으면 `degraded/down`로 떨어지는 구조가 이블린 최종 완성도의 기준이다.**
