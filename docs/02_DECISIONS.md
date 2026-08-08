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
  만들고 Control Page, Host Supervisor, Local Bridge에만 전달한다. Bot API에는 키를
  주지 않고, authenticated Bridge가 보고한 content-free fence digest를 Host lease와
  durable consent state에 대조해 admission을 판정한다.
- 이유: 공유 폴더의 read/write 권한만으로 캡처 권한이나 physical OFF 증거를 위조할
  수 없어야 하며, raw owner/lease 값이나 음성 데이터를 저장할 필요도 없어야 한다.
- 근거: [[VOICE_CAPTURE_CONSENT]],
  `evelyn_core/runtime/evelyn_core/voice_capture_consent.py`,
  `tests/runtime/test_host_supervisor.py`
- 영향: artifact는 content-free digest와 인증 tag만 보존한다. 키 누락·오류,
  cross-scope replay, stale·replacement와 status rollback은 fail-closed하며 일반
  자식 프로세스에는 키를 상속하지 않는다. Bot API가 캡처 lease를 새로 서명할
  권한은 없으며 공개 상태에서도 fence digest를 제거한다.

## 2026-08-02 — Local Voice admission을 캡처 동의 세대와 선형화

- 상태: 승인
- 결정: durable Local Voice reservation·claim proof를 발급 당시 capture fence
  digest에 묶고, consent state write와 마지막 reserve/claim은 stable OS claim lease로
  직렬화한다. durable reservation이 있는 token은 exact reservation ref·ingress turn과
  `reservation_verified=true` receipt만 소비한다. OFF 계열 전이는 manager 재시작으로
  알 수 없는 같은 scope의 `reserved` row도 durable purge한다.
- 이유: 동의 A에서 발급한 token이 동의 B에서 부활하거나, 철회와 accepted text 저장이
  엇갈리거나, Bot 재시작 때문에 미소비 reservation이 대화 권한으로 남아서는 안 된다.
- 근거: [[LOCAL_VOICE_ADMISSION_CONTRACT]], [[VOICE_CAPTURE_CONSENT]],
  `evelyn_core/runtime/evelyn_core/local_voice_admission.py`,
  `tests/runtime/test_fast_control_ingress_integration.py`
- 영향: claim이 먼저 durable commit되면 token은 terminalize하고 관측 이벤트 오류가
  이를 503/retryable 상태로 되돌리지 않는다. 철회가 claim lease를 제때 얻지 못하면
  메모리에서 먼저 `revoking`으로 닫고 physical OFF를 계속 시도한다. raw audio와
  transcript는 이 계약이나 보고서에 추가로 저장하지 않는다.

## 2026-08-03 — 기본 TTS를 실제 OmniVoice로 고정

- 상태: 승인
- 결정: 내부·호스트 계약 `tts:8880`은 유지하면서 기본 모델을
  `k2-fsa/OmniVoice`로 교체한다. offline model cache는 revision
  `c5fdb5ccb189668d56333f77ba2629f4cd7535f4`를 read-only로 제공하고 health의
  `model_revision`까지 exact 일치해야 한다. VoxCPM2의 host `8881` 서비스는
  opt-in 호환성·진단용으로만 보존하며 자동 또는 runtime fallback으로 사용하지 않는다.
- 이유: 기존 클라이언트가 이미 OmniVoice clone·24 kHz PCM streaming 계약을
  사용하므로 호출 계층을 바꾸지 않고 실제 모델과 표시·health의 불일치를 없앤다.
- 근거: `docker-compose.fast-control.yml`, `docker/Dockerfile.omnivoice`,
  `evelyn_core/runtime/service_manifest.json`,
  `tests/runtime/test_docker_compose_contract.py`
- 영향: 기본 TTS build는 검토된 외부 Python 소스 20개의 SHA-256 allowlist와 고정된
  직접 runtime 의존성 버전을 사용한다. 시작 시 고정 revision의 필수 model snapshot
  경로 13개도 SHA-256으로 검증한다. 전이 wheel과 CUDA base image digest까지 고정한
  완전 재현 build라는 뜻은 아니다. profile은 read-only이며
  profile API와 validation 오류 응답은 입력 원문을 숨기고 운영 로그에는 합성 text,
  경로, session/turn 식별자를 남기지 않는다. 기본 합성은 sentence streaming을 사용하고 실험적 blockwise 경로는
  client disconnect cancellation이 안전해질 때까지 비활성화한다. Compose는 TTS image를
  `pull_policy: never`로 외부에서 받지 않으며 공식 path-safe builder가 누락되었거나 새로
  요청된 image를 만든다. Supervisor 복구는 이미 있는 image만 사용한다. 8881 서비스를
  시작해도 기존 client는 `tts:8880`에서 reroute되지 않는다. image build/recreate와 실제
  clone-stream smoke 전에는 live 전환 완료로 보고하지 않는다.

## 2026-08-08 — Minecraft 기본 판단은 local, Codex는 검증 전 비활성

- 상태: 승인
- 결정: Mindcraft와 legacy Voyager의 기본 action backend는 local Qwen으로 유지한다.
  Codex Gateway는 별도 Docker profile에만 두고, pinned image에서 tool registry와
  secret canary가 검증되기 전에는 health not-ready, action 503, subprocess 0을
  강제한다. host-native/custom shell gateway는 지원하지 않는다.
- 이유: Minecraft chat과 recovery context는 신뢰할 수 없는 입력을 포함한다.
  `read-only` sandbox는 같은 principal의 credential·runtime file 읽기를 차단하지
  않으므로 filesystem-capable Codex CLI를 기본 경로에 두면 관계 연속성을 위한 기억과
  자격증명을 외부 model output으로 노출할 수 있다.
- 근거: `external/mindcraft_evelyn/src/models/evelyn_planner.js`,
  `external/mindcraft_evelyn/src/models/codex_gateway.js`,
  `evelyn_core/runtime/evelyn_core/codex_gateway_server.py`,
  `docker-compose.fast-control.yml`, 관련 runtime/Mindcraft 회귀
- 영향: local planning·recovery는 계속 동작하며 기본 Minecraft 시작은 Codex credential을
  요구하지 않는다. persistent memory summary는 기본 경로에서 로드하거나 새로 만들지
  않는다. Codex 품질 경로는 verified no-tools boundary나 목적 제한 broker가 생길 때까지
  의도적으로 사용할 수 없다.

## 2026-08-09 — Mindcraft history는 process-local, LLM은 fixed Bot API broker로 제한

- 상태: 승인
- 결정: 기본 Mindcraft는 bounded ephemeral history만 사용하고 `load_memory=false`와
  no-memory-mount를 유지한다. planner recovery도 저장하지 않는다. Node는 direct model
  endpoint를 호출하지 않고 전용 token-file authenticated Bot API broker만 사용하며,
  broker가 fixed local/router upstream과 core conversation filter를 소유하고
  `memory_exposure_request`를 frame consumer의 exact ACK까지 유지한다. process-local
  generation exposure는 turn 첫 await 전부터 awaited final route/action sink까지 보호한다.
  inter-agent ingress·timer queue도 같은 generation에 묶고 clear에서 폐기한다. recovery
  step은 exact history snapshot의 process-local one-shot issuance만 실행 결과로 소비한다.
- 이유: core outbound/deletion primitive는 Python broker에서 재사용하되, 불완전한 durable
  history를 새로 만들지 않는 것이 삭제·편집 후 부활과 근거 없는 재사용을 막는 최소 경계다.
- 근거: [[MINDCRAFT_MIGRATION]], `external/mindcraft_evelyn/src/agent/history.js`,
  `external/mindcraft_evelyn/src/utils/evelyn_history_boundary.js`,
  `external/mindcraft_evelyn/history_sink_boundary.patch`,
  `external/mindcraft_evelyn/src/models/evelyn_planner.js`,
  `evelyn_core/runtime/evelyn_core/mindcraft_llm_broker.py`, `docker-compose.fast-control.yml`
- 영향: legacy memory/archive/log는 읽거나 rebase·삭제하지 않으며 cleanup은 사용자 승인
  migration으로 분리한다. 현재 core memory 입력이 없는 broker request projection은 strict
  `not_used` receipt를 쓰며 ACK는 frame 소비까지만 증명한다. durable bound-receipt history는
  별도 계약으로 남긴다. recovery token과 raw command는 저장하지 않으며 restart 후 이어 쓰지 않는다.
  goal/status artifact는 enum code·count/boolean만 남긴다. `!clearChat`은 대화 유래 상태만
  비우며 자율 목표의 영구 정지는 `!endGoal` 계약을 사용한다.
