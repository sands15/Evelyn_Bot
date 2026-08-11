# Voice validation capture consent

로컬 음성 검증 마법사는 시스템의 일반 마이크 설정과 별도로 시간 제한 동의를
요구한다. 이 계약은 Control Page에서 시작한 `voice-p0.v1` 로컬 검증에만
적용된다. 일반 채팅의 `/mic on`은 캡처를 직접 활성화하지 않고 Control Page의
음성 검증 시작·청취 동의 영역을 열고 포커스한다. 검증 캡처와 authenticated watchdog,
durable consent/host-lease fence가 모두 current일 때만 다시 동의를 요구하지 않고 현재
ON 상태를 알린다. Bot API 재시작 뒤에도 panel command generation을 바꿔 열린 브라우저가
새 명령을 놓치지 않는다. `/mic off`는 복구·철회 경로로 계속 허용한다.

## 안전 기본값

- Local I/O Bridge는 환경의 `LOCAL_MIC_ENABLED` 값과 무관하게 실제 캡처를 끈
  상태로 시작한다. 인증된 동의 제어가 유일한 ON 권한이다.
- Control Page는 동의 manager를 만들거나 상태를 읽기 전에 stable
  `owner_claim.lock`의 process-lifetime OS lock을 nonblocking으로 획득한다.
  이미 owner가 있거나 lock 경계를 만들 수 없으면 각각 고정 오류
  `voice_capture_owner_conflict`, `voice_capture_owner_lock_unavailable`로 기동을
  중단하고 동의 상태·heartbeat·마이크를 변경하지 않는다.
- 브라우저가 직접 Bot API의 마이크 제어 endpoint를 호출할 수 없다.
- Control Page의 모든 동의 변경 API는 기존 Origin/Host/CSRF 검사를 거친다.
- Control Page→Bot API 제어, Local Bridge→Bot API 상태 보고, Control Page↔Windows
  Host의 캡처 lease 증명은 서로 다른 process-scoped credential을 사용한다. 마지막
  HMAC 키는 launcher 세대마다 항상 새로 만들고 Control Page, Supervisor, Bridge에만
  전달한다. 값 자체는 문서·artifact·상태·로그에 저장하지 않는다.
- Bridge heartbeat는 전체 payload의 목적 제한 HMAC, exact schema, 32자리
  instance/action ID, 단조 `statusSeq`, 유한 시각과 캡처 상태 불변식을 통과해야만
  freshness와 Supervisor의 stop evidence를 갱신한다. 부분·손상·중복·역전·이전
  instance 보고는 권위 상태를 바꾸지 않는다.
- preview token은 메모리에만 존재하며 120초 뒤 만료되고 한 번만 사용할 수 있다.
  새 preview는 이전 것을 모두 무효화하며, 발급 당시의 정확한 validation session/
  state/local-surface binding이 apply 시점에도 같아야 한다.
- 동의는 검증 세션에 연결되기 전 최대 5분, 연결된 뒤 최대 30분 유효하다.
- 검증 성공·실패·중단, 명시적 철회, 임대 만료, Control Page 정상 종료 또는
  새 프로세스의 stale-owner 복구 시 마이크 OFF를 요청하고 Bridge ACK를 확인한다.
- 마이크 제어 ACK는 exact revision, 일회성 `actionId`, Bridge instance digest,
  적용 상태와 pending 명령 부재를 함께 확인한다. ON은 `captureReady=true`와
  실제 capture thread 미종료, OFF는 top-level/nested `captureStopped=true`와
  active/ready false가 모두 확인돼야 한다.
- OFF 발급은 Bot API의 `disableGeneration`을 먼저 증가시킨다. ON은 직전 GET에서
  받은 process epoch+generation fence와 동의 목적을 정확히 제시해야 하므로,
  취소·철회 뒤 늦게 도착한 과거 ON은 적용되지 않는다.
- ON 또는 사후 validation bind 중 취소·예외·terminal 전환이 발생하면 동일
  consent lock 아래 recovery→exact OFF를 완료하기 전 성공을 반환하지 않는다.
- unbound active 동의는 canonical idle validation에서만 잠시 유지된다. Discord-only
  세션 시작, 다른/누락 session, bound session의 preflight·unknown·terminal 상태는
  모두 revoke한다. confirm/retry/abort가 상태 변경 뒤 I/O 예외를 내도 같은 lock에서
  즉시 recovery→OFF를 수행하고 고정된 content-free 503만 반환한다.

## API

- `GET /api/control-page/voice-capture-consent`
- `POST /api/control-page/voice-capture-consent/preview`
- `POST /api/control-page/voice-capture-consent/apply`
- `POST /api/control-page/voice-capture-consent/revoke`

preview/apply의 허용 scope는 `voice_validation_local` 하나뿐이다. Control Page의
음성 capability는 활성 동의가 없을 때 `local_mic_consent_required` blocker와
확인형 동의 action을 제공한다. 이미 생성된 preflight 세션은 동의 적용 후 다른
blocker가 없으면 같은 세션으로 `running` 전환된다.

## 저장 계약

`runtime_artifacts/voice_capture_consent/state.json`에는 다음 제어 메타데이터만
저장한다.

- FSM state와 scope
- 무작위 lease/process owner 식별자
- validation session ID
- 요청·활성·만료·철회 시각
- 짧은 오류 코드와 철회 이유

원문 음성, 오디오 경로, 사용자 발화, prompt, STT transcript는 저장하지 않는다.
같은 디렉터리의 `owner_claim.lock`은 OS 잠금용 stable 1-byte regular file이며
삭제하거나 atomic replace하지 않는다. owner ID, credential, transcript나 음성은
담지 않는다. Windows에서는 byte-range lock, POSIX에서는 `flock`을 사용하고 lock
backend·stable inode·regular-file 검증이 불가능하면 fail-closed한다. runtime artifact
retention은 규칙의 범위나 파일 나이와 무관하게 `owner_claim.lock`을 정리 후보에서
제외한다.

같은 디렉터리의 `claim_lease.lock`은 durable consent state 변경과 Bot API의 마지막
fence 확인+local-voice reservation/claim을 선형화한다. Control Page writer는 이미
선형화된 claim을 최대 2초 기다린다. 그 안에 잠금을 얻지 못하거나 state write가
실패하면 메모리에서 먼저 `revoking/untrusted`로 닫고 physical OFF를 계속 시도한다.
Bot API는 잠금을 기다리지 않으며 busy·unavailable이면 원문 claim 전에 content-free
503으로 닫힌다. 이 파일도 stable 1-byte regular file이며 retention이 삭제하거나
atomic replace하지 않는다.
상태 파일은 누적 로그가 아니라 단일 최신 상태이며, ON 요청 전에 `enabling`,
OFF 요청 전에 `revoking`을 먼저 기록해 재시작 시 fail-closed 복구할 수 있게
한다. durable writer는 replace 전 temporary file을 `fsync`하고, Windows에서는
write-through replace, POSIX에서는 replace 뒤 parent directory sync를 수행한다.

상태 load는 `verified | missing | untrusted`로 구분한다. 파일 누락, symlink,
schema/필드/불변식 오류, invalid UTF-8, 과도한 JSON 중첩, 비유한 시각과 stale
owner는 비활성의 증거가 아니다. 이 경우 메모리와 durable 상태를 `revoking`으로
두고 exact OFF ACK 뒤에만 새 `inactive`를 기록한다. OFF 실패·취소·상태 저장
실패는 recovery 상태로 남아 monitor가 다시 시도한다.

## 제어 요청과 상태 보고 계약

- `GET /api/local-bridge/mic`과 `POST /api/local-bridge/mic`은 내부 제어 bearer를
  요구한다. ON은 `purpose=voice_capture_consent`와 exact `enableFence`가 추가로
  필요하다.
- `POST /api/local-bridge/status`는 독립된 reporter bearer를 요구한다.
- Local mic capture 실패의 상태·warning은 fixed code와 exception type만 포함하고,
  예외 메시지·장치 설정 경로를 heartbeat, Control Page 또는 LLM context에 넣지 않는다.
- 제어 요청과 ACK는 같은 `revision`, `actionId`, `bridgeInstanceDigest`, `enabled`를
  가져야 한다. waiter가 기다리는 동안 global current request가 바뀌거나 Bridge가
  더 높은 revision을 보고하면 `mic_control_superseded`로 즉시 실패한다.
- 새 Bridge instance는 기존 instance보다 큰 `startedAt`으로만 교체할 수 있다.
  같은 instance의 `statusSeq`는 항상 증가해야 하며 거부된 보고는 `updatedAt`을
  갱신하지 않는다.
- launcher 재실행은 이전 Supervisor generation을 정상 종료한 뒤 새 bearer를
  상속한 단일 Supervisor/Bridge generation만 시작한다.
- launcher는 credential을 전역 자식 환경에 두지 않는다. Docker `up` 생성 순간에는
  서비스별 Compose allowlist에 필요한 채널만, Supervisor 생성 순간에는 reporter와
  캡처 HMAC 키만 주입한 뒤 즉시 제거한다. Control Page가 여는 종료·재시작 helper,
  브라우저와 경로 opener도 캡처 HMAC 키를 제거한 환경을 사용한다.
- Supervisor의 개별 Docker 복구는 항상 `--no-deps`로 대상 서비스만 시작한다.
  Discord 복구에만 실제 Discord token을 전달하고 LLM/STT/TTS 복구에는 비밀이 아닌
  `local-only-disabled` 자리값을 사용하므로 Bot API/Control Page를 빈 내부 token으로
  재생성하지 않는다.
- Local Bridge는 reporter 외 credential을 받지 않고 Minecraft·shutdown 자식에는
  credential을 전달하지 않는다. 단, Host lease 검증과 status 서명에 필요한 캡처
  HMAC 키만 Bridge 프로세스에 추가로 허용한다. 전체 재시작은 Bridge의 전용 종료
  코드를 받은 Supervisor가 소유하며, 짧은 handoff에 Discord token과 명시적 Codex
  credentials 경로만 전달한다. 새 launcher는 새 캡처 HMAC 세대를 만든다.

## Control Page hard-crash watchdog

- owner lock cleanup context는 동의 context보다 먼저 시작되고 나중에 끝난다.
  정상 종료나 cleanup 취소에서는 monitor/heartbeat의 실제 writer를 모두 drain하고
  exact OFF 철회와 durable 상태 전환을 시도한 뒤에만 lock을 반납한다. 프로세스
  hard-crash에서는 커널이 lock을 회수하며 successor가 기존 active lease를 복구하지
  않고 기존 startup recovery 계약으로 OFF부터 수행한다.
- Control Page는 startup과 상태 전환 때, 캡처가 가능할 때는 1초마다
  `owner_heartbeat.json`을 갱신한다. 파일은 state, owner/lease의 SHA-256 digest,
  만료·heartbeat 시각과 HMAC만 가진 content-free projection이며 최대 4 KiB다.
- Local Bridge는 각 status tick과 마이크 ON 전·후에 strict schema, 목적 제한 HMAC,
  4초 freshness, 원래 owner/lease digest binding을 검사한다. 누락·손상·symlink·
  stale·expired·replacement는 모두 캡처 권한 부재다.
- Bot API에는 캡처 HMAC 키를 전달하지 않는다. bearer-authenticated Bridge status가
  보고한 content-free fence digest를 `owner_heartbeat.json`의 projection과 durable
  consent state에 3자 일치시키고, current Bridge/mic/watchdog 상태와 함께 local
  admission 발급·claim을 판정한다. 공개 status에서는 fence digest를 제거한다.
- 권한을 잃으면 Bridge는 새 입력을 받지 않고 admission 상태를 폐기한 뒤 exact mic
  stop을 수행한다. stop 자체가 실패하면 종료 코드 76으로 Bridge 프로세스를 즉시
  끝내 OS가 캡처 handle을 회수하게 한다.
- Supervisor는 현재 자식 PID와 시작 시각, 서명된 전체 Bridge status, 고정된
  `bridgeInstanceId`, 관측한 `statusSeq` high-water, watchdog 시각과 nested/top-level
  physical OFF를 모두 확인한 경우에만 stop을 `verified`로 게시한다. status 입력은
  최대 128 KiB로 제한한다.
- 검증 세션 밖의 비정상 종료는 기존 10분당 3회 예산으로 disabled-default Bridge를
  복구한다. 검증 중 종료는 같은 attempt를 자동 재개하지 않고 validation 오류와
  `manual_intervention_required`로 남긴다.

## 캡처 동의와 발화 admission의 분리

활성 동의는 검증에 필요한 마이크 캡처만 허용한다. 주변 발화를 대화·도구·
변경성 동작에 전달하는 권한은 별도의
`docs/LOCAL_VOICE_ADMISSION_CONTRACT.md`가 판정한다.

- 일반 로컬 음성은 정확한 선행 호출어 `이블린`을 요구한다.
- 호출을 소비한 뒤의 follow-up은 45초 동안만 유효하다.
- shutdown/restart, mic 변경, Minecraft 변경 의도는 follow-up 중에도 새
  호출어를 요구한다.
- 검증 예외는 현재 session/step/attempt/attemptId와 기대 transcript 판정이
  모두 일치할 때만 허용되고, 10초 일회성 capability 소비 시 binding을 다시
  확인한다. 이 예외는 일반 45초 follow-up 창을 열거나 갱신하지 않는다.
- capability는 응답 전에 raw text/token 없는 durable reservation을 만든다. 발급
  전·reservation 직전·직후와 claim 직전에 위 capture fence를 다시 확인하므로,
  철회 race나 Bot 재시작은 exact reservation이 없으면 대화를 시작할 수 없다.
- 동의 철회·mic-off·bridge instance 교체는 아직 소비하지 않은 admission,
  reservation과 follow-up 상태를 exact revoke한다.
- durable reservation/claim proof는 발급 consent fence digest를 포함한다. Bot
  manager가 재시작하거나 동의가 A→B로 교체돼도 A token은 B proof로 claim할 수
  없고, OFF/restart/shutdown은 process-local 목록에 없는 `reserved` row도 scope
  단위로 purge한다.

이 경계의 합성 테스트와 공개 브라우저 source-spoof 차단은 구현됐지만 실제
마이크·스피커 10턴과 silence의 live E2E는 아직 사용자 청취 확인과 함께
검증하지 않았다. 최종 current-source hardening 묶음은 267개(skip 1), CI-equivalent
전체 discover는 3044개(skip 20)가 통과했다.

## 아직 남은 검증·강화 경계

- apply는 mic 활성 뒤 runtime health 수집을 기다리는 동안 consent lock을 잡는다.
  수집 전체 deadline 부재는 드문 lock starvation 위험이다.
- Supervisor가 게시하는 signed stop evidence는 아직 별도 downstream verifier가
  없으며 진단 계약으로만 쓰인다. startup ambiguity의 freshness probe도 HMAC을
  검증하지 않아 공유 폴더 writer가 시작을 거부시키는 availability 공격은 가능하지만
  캡처 권한이나 physical stop 증거를 만들 수는 없다.
- source 회귀는 현재 Windows 프로세스 간 owner 경합과 crash 회수를 검증했다.
  실행 중 서비스 교체와 실제 Docker bind mount의 container 간 경합, Windows native
  Control Page와 Linux container를 섞은 lock coherence는 live 검증하지 않았다.

따라서 source 수준 hard-crash와 다중 owner capture P0는 닫혔지만, 배포된 프로세스
경합과 실제 장치 E2E까지 완료됐다는 뜻은 아니다.
