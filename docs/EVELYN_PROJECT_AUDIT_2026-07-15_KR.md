# 이블린 프로젝트 종합 감사 및 점수 평가

문서 상태: **Historical baseline**
후속 수정 현황은 `docs/CURRENT_STATE.md`, 미해결 위험은 `docs/ACTIVE_RISKS.md`를 기준으로 한다. 아래 수치와 판단은 감사 실행 시점의 스냅샷이며, 이후 수정 결과로 소급 변경하지 않는다.

평가일: 2026-07-15 KST  
대상: `C:\Evelyn` 전체 저장소, 현재 로컬 Docker 런타임, Windows 로컬 I/O 브리지  
평가 관점: 기능 수보다 재현성, 실패 안전성, 보안, 테스트 신뢰도, 유지보수성을 우선

## 1. 최종 판단

종합 점수는 **66 / 100**이다.

이블린은 기능적으로는 이미 고급 개인 에이전트다. 로컬 LLM 3계층, STT, TTS, 음성 입출력, 메모리, 비전, Control-Page, Live2D, Minecraft/Voyager까지 실제 런타임으로 연결되어 있다. 감사 시점에는 핵심 서비스 10개가 모두 Docker `healthy`였고, Control/Main/Router/Sub/TTS/STT/Vision/Chat/Voice 준비 상태도 모두 참이었다.

그러나 엔지니어링 완성도는 기능 완성도보다 낮다. 가장 큰 문제는 다음 네 가지다.

1. **복구 기준점 부재:** 수정된 추적 파일 48개와 미추적 파일 112개가 한 작업 트리에 남아 있다.
2. **보안 경계 부족:** Control-Page API가 임의 Origin을 허용하고, 종료·채팅·메모리 수정 API에 별도 인증이 없다.
3. **회귀선 불합격:** 전체 테스트 854개 중 실패 3개와 import 오류 4개가 발생한다.
4. **구조적 복잡도:** `main.py`가 8,785줄이며 핵심 음성 처리 함수 하나가 약 729줄이다.

따라서 현재 상태는 **“정훈이 직접 관리하면 실사용 가능한 강력한 개인용 시스템이지만, 안전한 릴리스나 다른 PC 재현이 가능한 제품 상태는 아니다.”**로 평가한다.

2026-06-09 평가의 65점과 비교하면 기능, 상태 진단, Docker 실행, 로컬 음성, Live2D는 분명히 개선됐다. 하지만 같은 기간에 대규모 미커밋 변경과 새 모듈이 누적되었고 보안·릴리스 기준선이 따라오지 못해 종합 점수는 66점으로 제한한다.

## 2. 점수표

| 영역 | 점수 | 비중 | 가중 점수 | 판단 |
|---|---:|---:|---:|---|
| 제품 정체성과 기능 폭 | 88 | 10% | 8.80 | 개인 에이전트로서 차별성이 강함 |
| 런타임 안정성과 관측성 | 82 | 15% | 12.30 | 현재 핵심 런타임은 건강하고 진단 정보가 풍부함 |
| 음성/STT/TTS 경험 | 72 | 12% | 8.64 | 실제 작동하지만 하드웨어·잡음·에코 경계는 계속 조정 중 |
| Control-Page와 Live2D UX | 80 | 8% | 6.40 | 실사용 가능한 운영 화면과 캐릭터 표현을 갖춤 |
| 메모리와 대화 문맥 | 72 | 10% | 7.20 | 기능은 깊지만 복잡도와 데이터 관리 부담이 큼 |
| 비전·Minecraft/Voyager | 62 | 8% | 4.96 | 서비스는 존재하나 외부 런타임 경계가 자주 degraded가 됨 |
| 아키텍처와 유지보수성 | 50 | 15% | 7.50 | 분리 작업은 진행됐지만 중심부가 여전히 거대하고 결합도가 높음 |
| 테스트와 품질 보증 | 56 | 10% | 5.60 | 테스트 양은 많으나 전체 회귀선과 CI가 녹색이 아님 |
| 보안과 개인정보 보호 | 43 | 7% | 3.01 | 로컬 바인딩은 좋지만 웹/API 신뢰 경계가 약함 |
| 재현성·배포·버전 관리 | 35 | 5% | 1.75 | 현재 가장 불안한 영역 중 하나 |
| **합계** |  | **100%** | **66.16** | **종합 66점** |

## 3. 감사에서 확인한 사실

### 저장소와 코드 규모

- 추적 파일: 1,565개
- 수정·삭제 상태의 추적 파일: 48개
- 미추적 파일: 112개, Git 상태의 미추적 항목 86개
- 마지막 커밋: `4f687bc`, 2026-06-23, `Extract voice delivery runtime`
- `main.py`: 8,785줄
- `docs/index.html`: 4,435줄
- `docs/assets/evelyn-page.js`: 4,859줄
- `evelyn_core/runtime/evelyn_core`: Python 중심 약 156개 파일
- 테스트 파일: 약 138개, 선언된 `test_*` 함수 약 916개
- 모듈 수준 환경변수 조회: Python `os.getenv` 호출 약 382개
- `main.py`와 `config.py`에 중복 정의된 대문자 설정 이름: 12개

### 현재 실행 상태

- Docker 컨테이너 10개 모두 `running / healthy`
- 최근 30분 컨테이너 로그의 `Traceback`, `CRITICAL`, `Unhandled`, `ERROR`, `failed` 패턴: 10개 컨테이너 모두 0건
- Control/Main/Router/Sub/TTS/STT/Vision/Chat/Voice 준비 상태: 모두 참
- Control-Page 부팅 표시: 100%
- Windows 로컬 I/O 브리지와 마이크 캡처: 준비됨
- Control-Page, Live2D JS, Live2D model3 자산: HTTP 200
- Voyager HTTP 서비스 컨테이너는 healthy지만 runner, bridge, Minecraft TCP 경계는 비활성 상태

### 정적 검사와 의존성

- Python `compileall`: 통과
- `pip check`: 설치된 패키지 간 충돌 없음
- `git diff --check`: 통과, 다수 파일에 LF/CRLF 변환 경고 존재
- `node --check` (`evelyn-page.js`, `evelyn-live2d.js`): 통과
- 추적 파일 대상 토큰·개인키 휴리스틱: 발견 없음
- 루트 Node 의존성 감사: 취약 패키지 10개
  - high 4개
  - moderate 6개
  - critical 0개
  - 핵심 경로: `mineflayer -> minecraft-protocol -> prismarine-auth -> xboxlive-auth -> axios 0.21.4`
- Python CVE 감사 도구는 현재 환경에 설치되어 있지 않아 수행하지 못함

### 전체 테스트 결과

`C:\Evelyn\.venv\Scripts\python.exe -m unittest discover -s tests` 결과:

- 실행: 854
- 성공: 846
- 실패: 3
- import 오류: 4
- 건너뜀: 1

실패 3개:

1. `test_local_speaker_uses_streaming_sentence_tts_with_full_answer_fallback`
   - 구현이 `main.py`에서 runtime 모듈로 이동했지만 테스트가 여전히 `main.py` 소스 문자열을 검사한다.
2. `test_main_routes_local_only_mic_without_discord_target`
   - 위와 같은 source-contract 노후화 문제다.
3. `test_normalize_stt_language`
   - 구현은 `en -> English`로 정규화하지만 테스트는 기본값 `en` 보존을 기대한다.

import 오류 4개:

- `discord.py` 미설치로 3개 테스트 모듈 import 실패
- `Pillow` 미설치로 비전 테스트 1개 import 실패

루트 `requirements.txt`에는 `discord.py`가 선언되어 있지만 현재 `.venv`에는 설치되어 있지 않다. `Pillow`는 Docker용 Discord/Vision requirements에는 있으나 루트 requirements에는 없다. 이는 **개발환경과 실행환경의 계약 불일치**다.

## 4. 가장 위험한 문제

### P0-1. 현재 작업 트리는 복구 가능한 릴리스가 아니다

심각도: **높음**  
영향: 코드 유실, 부분 복구, 잘못된 재빌드, 운영 이미지와 소스 불일치

현재 48개 추적 파일이 수정됐고 112개 파일이 미추적 상태다. 미추적 목록에는 Live2D 자산, 새 runtime 모듈, 새 테스트가 포함된다. 현재 동작하는 Docker 이미지가 있어도 디스크 손상, 잘못된 정리, 다른 PC 이동 시 같은 상태를 재구성할 보장이 없다.

개선:

1. 현재 상태를 별도 백업 또는 Git 브랜치로 즉시 체크포인트한다.
2. 변경을 기능 단위로 나눠 커밋한다.
3. Live2D 바이너리·텍스처의 Git 포함 여부와 외부 백업 정책을 명시한다.
4. 릴리스 태그와 실제 Docker 이미지 빌드 기준 커밋을 연결한다.

### P0-2. Control-Page의 로컬 웹 보안 경계가 약하다

심각도: **높음**  
영향: 악성 웹페이지를 열었을 때 상태·메모리 조회, 메모리 수정, 채팅/TTS 요청, 종료 요청 가능성

실제 HTTP 검사에서 외부 Origin을 넣어도 다음 헤더가 반환됐다.

```text
Access-Control-Allow-Origin: *
Access-Control-Allow-Methods: GET,POST,OPTIONS
```

Control-Page에는 다음 변경성 API가 있다.

- `/api/control-page/shutdown`
- `/api/control-page/chat`
- `/api/control-page/memory/{note_id}`
- `/api/control-page/open-memory-vault`
- `/api/control-page/runtime-repair/apply`

runtime repair에는 확인 토큰이 있으나 종료·채팅·메모리 수정에는 동일한 보호가 없다. 서비스가 `127.0.0.1`에만 바인딩된 것은 외부 네트워크 노출을 줄이지만, 브라우저를 통한 localhost 공격까지 막지는 않는다.

또한 repair preview가 확인 토큰을 응답으로 내려주는 구조이므로, 임의 Origin이 preview 응답을 읽을 수 있는 현재 CORS 설정에서는 확인 토큰만으로 웹 공격자를 분리하기 어렵다.

개선:

1. CORS 허용 Origin을 `http://127.0.0.1:8799`와 `http://localhost:8799`로 제한한다.
2. 모든 변경성 API에 세션별 난수 토큰 또는 CSRF 토큰을 요구한다.
3. `Origin`/`Host` 검증과 JSON Content-Type 강제를 추가한다.
4. 종료·재시작·메모리 수정은 짧은 수명의 확인 토큰을 별도로 사용한다.
5. 상태·메모리 API도 민감 정보 최소화 기준을 둔다.

### P0-3. 전체 테스트 기준선이 녹색이 아니다

심각도: **높음**  
영향: 리팩터링 회귀 감지 실패, “테스트 통과” 표현의 범위 혼동

집중 테스트는 자주 통과하지만 전체 발견 테스트는 실패한다. 특히 32개 테스트 파일이 구현 소스의 `read_text()` 문자열을 검사한다. 이런 테스트는 동작 계약보다 파일 배치에 결합되어 정상 리팩터링 뒤에도 실패하기 쉽다.

개선:

1. 현재 7개 비정상 테스트부터 0개로 만든다.
2. source-string 테스트를 import/API 동작 테스트로 전환한다.
3. 루트 테스트 환경 requirements 또는 lock을 별도로 만든다.
4. GitHub Actions에 최소 `compileall + unittest discover + node --check`를 추가한다.
5. 실제 프로세스 통합 테스트를 선택 실행에서 정기 실행으로 승격한다.

## 5. 구조와 유지보수성

### 5.1 `main.py` 분리는 진행됐지만 중심부는 여전히 과대하다

`main.py`는 8,785줄이다. `_process_member_audio_impl`은 약 729줄로 STT, 노이즈 판정, wake/follow-up, 세션, 라우팅, 상태 기록을 한 함수에서 다룬다. 한 경로를 수정하면 다른 경계가 영향을 받을 가능성이 높다.

큰 객체·함수 예시:

- `LocalIoBridge`: 약 756줄 클래스
- `_process_member_audio_impl`: 약 729줄 함수
- `AutonomyEngine`: 약 630줄 클래스
- `UpstreamDirectBridge`: 약 602줄 클래스
- `build_status` in Voyager: 약 412줄 함수
- `prepare_llm_messages_from_runtime`: 약 326줄 함수
- `execute_main_llm_streaming_turn`: 약 303줄 함수

개선:

1. 음성 hot path를 `capture -> filter -> STT -> transcript policy -> route -> delivery` 단계 객체로 분리한다.
2. 단계 간 입력/출력을 dataclass로 고정한다.
3. 각 단계가 상태 딕셔너리를 직접 변경하지 않고 이벤트나 결과 객체를 반환하게 한다.
4. `main.py`는 composition root와 Discord event wiring만 남긴다.

### 5.2 설정이 분산되어 드리프트 위험이 있다

- Python `os.getenv` 호출이 약 382개다.
- PowerShell 런처의 `$env:` 참조도 약 134개다.
- `main.py`와 `config.py`에 설정 12개가 중복 정의된다.
- 로컬 경로 `C:/Users/Admin/...`가 Compose와 테스트에 직접 고정되어 있다.
- Python requirements는 대부분 하한만 있고 상한 또는 lock이 없다.
- Docker base image도 digest가 아닌 이동 가능한 tag다.

개선:

1. 설정 스키마를 한 모듈로 통합하고 시작 시 유효성 검사를 수행한다.
2. 머신 경로를 `.env.local` 또는 profile 파일로 이동한다.
3. Python lock 파일과 Docker 이미지 digest를 도입한다.
4. 실제 적용 설정을 비밀값 없이 상태 페이지에서 확인할 수 있게 한다.

### 5.3 예외가 조용히 삼켜지는 구간이 많다

Python 핵심 경로에서 `except Exception` 패턴이 약 348개이고, `except ...: pass` 형태는 약 70개다. 복구성 코드는 예외를 흡수할 필요가 있지만, 원인·카운터·최근 오류를 남기지 않으면 장기간 조용한 기능 저하로 이어진다.

개선:

1. 정말 무시 가능한 예외만 `contextlib.suppress`와 이유 주석을 사용한다.
2. 나머지는 구조화된 오류 코드, 카운터, 최근 오류 시각을 남긴다.
3. hot path에서는 로그 폭주를 막기 위해 rate limit을 적용한다.
4. `logging.exception` 또는 동등한 traceback 보존 경로를 마련한다.

## 6. 보안·개인정보·의존성

### 6.1 음성 디버그 저장 기본값

`VOICE_DEBUG_SAVE_AUDIO` 기본값이 `true`다. 현재 `debug_audio`가 약 0.33GiB, 2,558개 파일이다. 개인 음성은 일반 로그보다 민감도가 높으므로 디버그 기본 저장은 개인정보 관점에서 불리하다.

개선:

- 운영 기본값을 `false`로 바꾸고 진단 세션에서만 일시 활성화한다.
- 파일별 만료 시간, 최대 용량, 사용자 표시를 추가한다.
- 원본 PCM과 STT 텍스트를 함께 남길 때 접근 권한과 삭제 기준을 명시한다.

### 6.2 Node/Minecraft 의존성

`npm audit`는 high 4개, moderate 6개를 보고했다. 직접 취약점이라기보다 Mineflayer 인증 체인의 오래된 하위 의존성에서 발생한다. 현재 `fixAvailable=false`이므로 단순 `npm audit fix`로 해결되지 않는다.

개선:

1. Mineflayer 최신 호환 버전을 별도 브랜치에서 검증한다.
2. Microsoft/Xbox 인증 토큰을 최소 권한·별도 계정으로 제한한다.
3. Voyager/Minecraft 컨테이너의 외부 통신과 파일 마운트를 최소화한다.
4. 취약점 예외를 무기한 무시하지 말고 검토 날짜와 허용 사유를 기록한다.

### 6.3 Codex 자격증명 마운트

Codex gateway 컨테이너에 호스트의 `.codex/auth.json`과 `config.toml`이 읽기 전용으로 마운트된다. 읽기 전용은 변조를 막지만 탈취 가능성까지 막지는 않는다. gateway는 localhost에만 노출되어 있어 위험이 줄지만, 컨테이너 또는 gateway 코드가 침해되면 인증 자료가 영향 범위에 들어간다.

개선:

- 전용 최소 권한 credential 또는 짧은 수명 토큰을 사용한다.
- gateway API에 호출자 인증을 추가한다.
- 컨테이너 네트워크·파일시스템 권한을 최소화한다.

## 7. 테스트와 품질 보증의 약점

장점은 테스트 양이 많다는 것이다. 단점은 실행 가능한 전체 품질 게이트가 없다는 것이다.

- GitHub Actions는 현재 GitHub Pages 배포만 수행한다.
- 테스트 파일 32개가 소스 문자열을 직접 읽는다.
- 실제 프로세스 통합 테스트 파일은 매우 적고, 실제 `main.py` smoke는 환경변수 없이는 skip된다.
- 실제 sounddevice가 등장하는 테스트 파일은 3개 수준이며 대부분 fake stream이다.
- 커버리지 도구와 정적 분석기(ruff, mypy, bandit, pip-audit)가 현재 `.venv`에 없다.
- `.venv` 자체가 루트 requirements와 일치하지 않는다.

권장 품질 게이트:

```text
Gate A: compileall + node --check + git diff --check
Gate B: 전체 unittest 100% 통과
Gate C: Bot API/Control-Page 실제 프로세스 smoke
Gate D: STT -> chat -> TTS -> local playback 5회 연속 하드웨어 검증
Gate E: 보안/의존성 감사와 localhost API Origin 검사
```

“집중 테스트 통과”와 “전체 프로젝트 통과”를 반드시 구분해 보고해야 한다.

## 8. 운영·저장공간·복구의 약점

### 현재 저장 규모

- `runtime_artifacts`: 약 1.47GiB, 5,619개 파일
- `debug_audio`: 약 0.33GiB, 2,558개 파일
- `node_modules`: 약 0.38GiB
- Python venv 4개 합계: 약 1.2GiB 이상
- 단일 `upstream_bridge_runner.log`: 약 181MiB

retention 도구는 존재하지만 명시적 수동 실행만 지원한다. Chrome/CDP 테스트 프로필이 다수 남아 있고, runtime_artifacts의 큰 비중을 차지하지만 현재 보존 문서의 기본 규칙에 충분히 포함되지 않는다.

개선:

1. 주기적인 dry-run 보고와 사용자 승인 기반 정리를 분리한다.
2. 로그는 크기 기반 rotation을 실행 중에 적용한다.
3. Chrome/CDP 프로필은 테스트 종료 후 정리하거나 공용 프로필 하나를 재사용한다.
4. Control-Page에 디스크 사용량과 정리 후보를 표시한다.

### 재시작 정책

Compose 서비스의 `restart` 정책은 모두 `no`다. 보이는 수동 런처라는 운영 원칙에는 맞지만, 단일 프로세스 crash나 재부팅 뒤 자동 복구성은 낮다.

권장안은 전체 자동 재시작이 아니라 계층별 정책이다.

- LLM/TTS/STT/Vision: `unless-stopped` 또는 안전한 제한 재시작 검토
- Bot API/Control-Page: health 기반 제한 재시작
- Voyager/Minecraft action: 사용자 승인 없이는 자동 행동 재개 금지
- Windows 로컬 I/O: 명확한 단일 인스턴스와 보이는 복구 창 유지

## 9. 문서화의 약점

문서는 많고 설계 의도도 풍부하다. 그러나 현재 상태, 목표 구조, 과거 계획이 한꺼번에 남아 있어 사실 확인 비용이 크다.

- `DOCUMENTATION_INDEX.md`의 마지막 검토는 2026-06-15다.
- `CURRENT_EVELYN_ARCHITECTURE.md`에는 `structural-change` 브랜치 기준이 남아 있지만 현재 브랜치는 `main`이다.
- 통합 계획 문서는 3,000줄 이상으로 과거 기록과 현재 할 일이 섞여 있다.
- `MAIN_PY_DECOMPOSITION_TARGET_KR.md`는 진행 로그가 길어 현재 남은 작업을 빠르게 파악하기 어렵다.

개선:

1. `CURRENT_STATE.md`: 실제 현재 상태만 1~2페이지로 유지한다.
2. `ACTIVE_RISKS.md`: 미해결 위험과 담당·기한만 유지한다.
3. 완료된 진행 로그는 `docs/archive`로 이동한다.
4. 문서마다 `Current / Target / Historical` 상태를 헤더에 강제한다.
5. 코드·테스트·문서의 기준 커밋 해시를 기록한다.

## 10. 개선 우선순위

### 즉시: 0~2일

1. 현재 전체 상태를 별도 브랜치와 외부 백업으로 체크포인트한다.
2. 전체 테스트의 실패 3개와 import 오류 4개를 해결한다.
3. Control-Page CORS를 localhost allowlist로 제한한다.
4. 종료·메모리 수정·재시작 API에 CSRF/확인 토큰을 추가한다.
5. `VOICE_DEBUG_SAVE_AUDIO=false`를 운영 기본값으로 검토한다.

### 단기: 1~2주

1. GitHub Actions에 최소 회귀 게이트를 추가한다.
2. Python 개발·실행 requirements를 분리하고 lock을 만든다.
3. `main.py`의 729줄 음성 처리 함수를 단계별 파이프라인으로 분해한다.
4. `main.py`/`config.py` 중복 설정 12개를 제거한다.
5. runtime_artifacts와 Chrome 프로필 정리 정책을 자동 보고한다.
6. Node 취약 의존성의 업그레이드 가능성을 별도 호환성 테스트한다.

### 중기: 1~2개월

1. 실제 음성 E2E 5회 연속 테스트를 표준 릴리스 체크로 만든다.
2. 메모리·음성·비전 데이터 보존 정책을 UI에서 제어하게 한다.
3. 서비스별 최소 권한·자격증명 분리·호출 인증을 적용한다.
4. source-string 테스트를 행동 계약 테스트로 점진 전환한다.
5. 운영 문서를 현재/목표/역사로 재편한다.

## 11. 장점

단점 중심 평가지만 다음 강점은 분명하다.

- 로컬 우선 설계와 기능 통합 수준이 높다.
- 핵심 서비스가 컨테이너 단위로 분리되고 healthcheck를 갖춘다.
- Control-Page가 상태와 복구 정보를 실제로 제공한다.
- 음성 파이프라인에 지연·STT·TTS 상태 계측이 들어가 있다.
- 메모리, 비전, Minecraft가 단순 데모가 아니라 runtime contract로 연결돼 있다.
- Live2D 모델과 음성 상태가 실제 UI에서 연동된다.
- 테스트 수와 설계 문서는 개인 프로젝트 범위를 크게 넘는다.
- 추적 파일에서 명백한 토큰·개인키는 발견되지 않았다.

이 장점 때문에 이블린은 폐기 후 재작성할 프로젝트가 아니다. **기능 추가 속도를 잠시 낮추고 보안·릴리스·회귀 기준선을 고정하면 점수가 가장 빠르게 오른다.**

## 12. 목표 점수와 승급 조건

### 75점: 안정적인 개인용 제품

- 전체 테스트 100% 통과
- 현재 작업 트리 체크포인트와 재현 가능한 설치 절차
- Control-Page 변경성 API 보호
- 실제 음성 E2E 5회 연속 통과
- 저장공간 rotation 실행

### 85점: 다른 사용자에게 넘길 수 있는 수준

- 새 PC에서 문서만으로 재설치 가능
- CI와 릴리스 태그가 Docker 이미지에 연결됨
- 주요 서비스 crash 자동 복구와 행동 서비스의 안전한 재개 정책
- 보안·개인정보 보존 정책 문서화
- `main.py`가 실질적인 composition root로 축소됨
- Minecraft/Voyager까지 계층별 복구와 명확한 degraded 표시

## 13. 감사 한계

이번 평가는 읽기 중심 감사와 안전한 로컬 검사를 사용했다. 다음은 수행하지 않았다.

- 실제 Discord 계정 연결과 음성 채널 E2E
- Minecraft 서버를 새로 실행한 실제 행동 검증
- 카메라/화면 비전 정확도 벤치마크
- Python 패키지 CVE 감사(`pip-audit` 미설치)
- 장시간 부하·메모리 누수·GPU OOM 테스트
- 외부 침투 테스트 또는 실제 변경성 API 공격
- 사용자 메모리 내용, 음성 파일 내용, 개인 토큰의 수동 열람

따라서 66점은 “현재 코드와 실행 상태에서 확인 가능한 보수적 점수”다. 장시간 실사용 데이터가 쌓이면 음성 안정성과 메모리 품질 점수는 다시 조정해야 한다.

## 14. 한 줄 결론

**이블린은 기능적으로 80점대지만, 복구·보안·테스트·릴리스 공학이 30~50점대라 종합 66점이다. 다음 기능보다 기준점 고정과 안전 경계 보강이 우선이다.**

## 15. 감사 후 개선 이력

### 2026-07-15: P0-1 복구 기준점 완료

- 브랜치: `checkpoint/evelyn-audit-2026-07-15`
- 커밋: `cbec7ec986874df51a517a4fa8106881c3b00440`
- 태그: `evelyn-checkpoint-2026-07-15`
- 외부 Git bundle과 SHA-256 검증본을 별도 보관했다.

### 2026-07-15: P0-2 Control-Page 보안 소스 보강 완료

작업 브랜치: `hardening/control-page-security-2026-07-15`

적용 내용:

1. `Access-Control-Allow-Origin: *`를 제거하고, 실제로 허용된 Origin만 응답에 반영한다.
2. 기본 Host/Origin을 loopback으로 제한하고 DNS rebinding 형태의 비-loopback Host를 차단한다.
3. `/api/control-page/session`에서 프로세스별 난수 CSRF 토큰을 발급한다.
4. 모든 Control-Page 변경 요청은 `X-Evelyn-CSRF-Token`과 `application/json`을 요구한다.
5. 프런트엔드는 변경 요청 전에 토큰을 가져오며, 서버 재시작으로 토큰이 바뀐 경우 403에서 한 번 갱신 후 재시도한다.
6. 내부 Bot API `8798`은 브라우저 Origin 요청과 비-JSON 변경 요청을 거부한다. 서버 프록시와 Local Bridge의 Origin 없는 JSON 호출은 유지한다.
7. 추가 Origin이 꼭 필요할 때만 `CONTROL_PAGE_ALLOWED_ORIGINS`에 정확한 Origin을 쉼표로 지정한다. 기본값은 비워 둔다.

검증 범위:

- 같은 Origin 읽기와 CSRF 포함 변경 요청 성공
- 외부 Origin, 누락 토큰, 비-JSON 변경 요청 차단
- DNS rebinding Host 차단
- 실제 public/internal 앱 wiring 검사
- 프런트엔드 토큰 갱신 계약과 JavaScript 구문 검사

운영 반영:

- 사용자 승인 후 `evelyn-bot-api`, `evelyn-control-page` 두 이미지만 재빌드하고 컨테이너를 재생성했다.
- 실제 `8799`에서 정상 Origin GET 200과 정확한 Origin 반영, 외부 Origin 403, DNS rebinding Host 403, CSRF 누락 POST 403, 정상 CSRF dry-run POST 200을 확인했다.
- 실제 `8798`에서 Origin 없는 서버 GET 200, 브라우저 Origin GET 403, 비-JSON POST 415를 확인했다.
- 재배포 후 이블린 컨테이너 10개가 모두 healthy이고 Control-Page boot progress가 100%임을 확인했다.

P0-2의 브라우저 기반 localhost 공격 경계는 운영에도 반영됐다. 다만 로컬 악성 프로세스 자체를 막는 서비스 간 비밀키 인증은 별도 후속 과제다.

### 2026-07-15: P0-3 전체 테스트 기준선 녹색화 완료

작업 브랜치: `stabilization/test-baseline-2026-07-15`

수정 내용:

1. 로컬 마이크와 OmniVoice 구현이 runtime 모듈로 이동한 뒤에도 `main.py`의 예전 문자열만 찾던 테스트를 실제 wiring 위치에 맞췄다.
2. 비전 품질 표식이 `vision_runtime.py`로 이동한 뒤 남아 있던 source-contract 테스트를 현재 구현 위치에 맞췄다.
3. STT 언어 별칭 계약을 구현과 일치시켜 기본 `en`도 `English`로 정규화됨을 검증한다.
4. 루트 `requirements.txt`에 누락된 `Pillow>=11.0`을 추가했다.
5. 현재 `.venv`에 `discord.py 2.7.1`, `Pillow 12.3.0`을 설치하고 `pip check`를 통과했다.

최종 전체 테스트:

```text
Ran 929 tests
OK (skipped=1)
```

- 실패: 0
- 오류: 0
- 건너뜀: 1
- 이전 import 오류 뒤에 숨었던 테스트 61개도 정상 발견·실행됐다.

이로써 P0-3은 현재 개발환경 기준으로 닫혔다. 다만 자동 CI, 버전 lock, source-string 테스트의 전면적인 행동 테스트 전환은 후속 과제로 남는다.

### 2026-07-15: P1-1 음성 디버그·로그 보존 경계 소스 보강

작업 브랜치: `hardening/audio-log-retention-2026-07-15`

확인된 원인:

1. `VOICE_DEBUG_SAVE_AUDIO` 기본값이 `true`여서 운영 중 원본 마이크와 STT WAV가 계속 저장될 수 있었다.
2. 기존 길드별 상한은 WAV만 개별 삭제해 연결된 JSON 메타데이터를 남겼다. 실제 `debug_audio`에는 WAV 461개와 JSON 2,097개가 섞여 있었고, 두 길드에서 JSON이 크게 누적됐다.
3. 기존 retention CLI는 `runtime_artifacts`만 기본 대상으로 삼아 별도 `debug_audio` 루트에는 적용되지 않았다.
4. Voyager 상태판은 stdout이 파일일 때도 1초마다 전체 화면을 기록했다. 그 결과 `upstream_bridge_runner.log` 하나가 189,955,706바이트까지 증가했다.

적용한 소스 경계:

- 음성 디버그 저장 기본값을 `false`로 변경했다.
- raw WAV, STT WAV, JSON을 하나의 논리 묶음으로 관리한다.
- 저장이 명시적으로 켜진 경우 길드별 200묶음, 7일, 256MiB 상한을 적용하고 최신 10묶음은 보존한다.
- 음성 전용 CLI는 기본 dry-run이며 `--apply`가 있어야 삭제한다.
- Voyager 로그는 25MiB에서 회전하고 백업 4개를 유지한다.
- 파일로 리디렉션된 상태판은 30초에 한 번만 출력한다.
- runtime retention은 `.log.1` 같은 회전 백업도 포함한다.

실데이터 dry-run 결과:

- `debug_audio`: 2,066묶음, 342,672,040바이트 후보
- `runtime_artifacts`: 12개, 196,367,554바이트 후보
- `logs`: 6개, 3,631,909바이트 후보
- dry-run 전후 세 루트의 파일 수와 총 바이트가 동일했다. 실제 삭제는 0건이다.
- 집중 테스트 23개와 전체 회귀 937개를 통과했다(`OK`, 건너뜀 1).

현재 실행 중인 컨테이너와 프로세스는 재시작하지 않았다. 따라서 새 기본값과 출력 제한은 다음 승인된 재배포/재시작 이후 적용된다. 기존 후보 삭제도 별도 승인 전에는 수행하지 않는다.

### 2026-07-15: P1-1 운영 반영과 기존 후보 정리 완료

사용자 승인 후 다음 범위만 운영에 반영했다.

1. `evelyn-bot-api`, `evelyn-voyager` 이미지만 재빌드하고 `--no-deps --force-recreate`로 재생성했다.
2. 나머지 8개 이블린 컨테이너의 시작 시각은 유지됐다.
3. 새 이미지에서 음성 저장 기본값 `false`, Voyager 로그 25MiB/백업 4개, 파일 상태판 30초 제한을 직접 확인했다.

삭제 직전 dry-run은 기존 기록과 동일했다. 가장 큰 로그 2개의 크기·수정 시각은 관찰 중 변하지 않았고, Voyager health는 `runner_alive=false`, 음성 파일 수도 변하지 않았다.

- 음성: 2,066묶음 / 342,672,040바이트 삭제
- `runtime_artifacts`: 12개 / 196,367,554바이트 삭제
- `logs/turn_trace`: 6개 / 3,631,909바이트 삭제
- 합계: 2,084개 항목 또는 묶음 / 542,671,503바이트 삭제
- 삭제 실패: 0

삭제 후 세 경로의 dry-run은 모두 후보 0개·0바이트였다. 각 유효 길드의 최신 음성 10묶음은 보존됐고, 이블린 컨테이너 10개는 모두 healthy, Control-Page boot progress는 100%였다.

### 2026-07-15: P1-2 Node/Minecraft 의존성 안전 갱신

실제 Mineflayer 실행 기준은 루트 `package.json`과 `package-lock.json`이다. vendored Voyager manifest에는 lock과 `node_modules`가 없고, Mineflayer entrypoint가 루트 package 기준 `require` fallback을 사용한다.

갱신 전 루트 `npm audit` 기준선:

- 의존성 96개
- high 4개
- moderate 6개
- critical 0개
- `npm audit fix` 자동 수정 가능 항목 0개

강제 override 없이 현재 semver 범위 안에서 lock을 갱신했다.

- `minecraft-data`: `3.109.1` -> `3.111.0`
- `minecraft-protocol`: `1.66.0` -> `1.66.2`
- `prismarine-auth`: `2.7.0` -> `3.1.1`
- `@xboxreplay/xboxlive-auth`: `3.3.3` -> `5.1.0`
- 취약한 `axios 0.21.4` 체인은 제거됐다.

갱신 후:

- 의존성 93개
- high 0개
- moderate 8개
- critical 0개
- `npm ci --ignore-scripts` 재현 설치 성공
- Node `v24.18.0`에서 Mineflayer, Minecraft data, pathfinder, collectblock, tool 플러그인 로딩 계약 성공
- vendored Mineflayer entrypoint `node --check` 성공
- 관련 보존/Voyager/Minecraft 집중 테스트 61개 통과
- 전체 회귀 937개 통과(`OK`, 건너뜀 1)

남은 8개 moderate는 최신 공개 Mineflayer `4.37.1`의 인증 체인에서 `@azure/msal-node`와 `yggdrasil`이 오래된 `uuid`를 요구해 발생한다. npm은 모두 `fixAvailable=false`로 판정했다. 강제 `uuid` override나 `npm audit fix --force`는 Microsoft/Xbox 로그인 경로를 깨뜨릴 수 있어 적용하지 않았다.

별도 격리 설치로 vendored Voyager manifest도 감사했다. 생산 설치 기준 12개 취약점(high 1, moderate 11)이 나왔지만, 해당 디렉터리는 현재 운영 설치 경로가 아니다. 이 manifest의 개발 도구·선택 플러그인 재분류와 upstream 동기화는 별도 작업으로 남긴다.

Minecraft 서버와 Voyager runner가 비활성인 상태였으므로 실제 게임 접속·Microsoft/Xbox 로그인·인게임 행동 E2E는 이번 갱신에서 실행하지 않았다. 대신 현재 운영 Node 버전, 재현 설치, 모듈·플러그인 로딩, Python 전체 회귀를 안전 검증 범위로 사용했다.

### 감사 후 보수적 임시 점수

체크포인트, Control-Page 보안 운영 반영, 전체 테스트 녹색화를 반영한 임시 점수는 **69 / 100**이다. 원 감사의 66점은 당시 상태를 보존하며, 이 값은 전체 재감사가 아닌 세 P0 개선 항목만 보수적으로 반영한 값이다.
