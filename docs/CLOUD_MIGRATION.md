# Evelyn Cloud Transfer Contract

Last reviewed: 2026-08-01

## 결론

Evelyn의 소스와 가벼운 제어 계층은 클라우드에서 계속 개발하거나 빌드할 수
있다. 그러나 현재 제품은 Windows 마이크·스피커, GPU 모델, 개인 음성 프로필,
로컬 화면과 장기 기억 vault에 의존하는 local-first 시스템이다. 따라서 기본
배치는 다음과 같은 hybrid 구조다.

- 클라우드: 소스 저장소, CI, 이미지 빌드, 선택적인 비밀 없는 테스트 서비스
- 로컬 Windows: Host Supervisor, Local I/O Bridge, 마이크·스피커, 화면 관찰
- 신뢰된 로컬 또는 전용 GPU 호스트: Main LLM, STT, TTS, Vision
- 로컬 private storage: 기억 vault, runtime artifacts, 음성 프로필, 자격증명

Control Page와 Bot API를 인증·TLS·네트워크 제한 없이 공용 인터넷에 직접
노출하지 않는다. 실제 운영 위치를 바꿀 때도 원문 음성, transcript, 개인 기억을
소스 번들에 넣지 않는다.

## 안전한 소스 번들

`tools/cloud_source_export.py`는 일반 `git archive` 대신 사용한다. 루트의 고정
커밋과 `.gitmodules`의 gitlink가 지정한 Mindcraft 커밋을 함께 묶는다. 로컬
서브모듈 checkout이 없거나, dirty이거나, 고정 커밋과 다르면 실패한다.

기본 실행은 clean worktree만 허용한다.

```powershell
git submodule update --init --recursive
python .\tools\cloud_source_export.py `
  --output C:\Temp\evelyn-cloud-source.zip
```

검증 중 uncommitted 변경을 의도적으로 제외하고 현재 커밋만 묶으려면 다음처럼
실행할 수 있다. 최종 전달 번들에는 이 옵션을 사용하지 않는 것이 원칙이다.

```powershell
python .\tools\cloud_source_export.py `
  --output C:\Temp\evelyn-cloud-source.zip `
  --allow-dirty
```

성공 결과에는 루트 커밋, 서브모듈 수, 소스 파일 수와 최종 ZIP SHA-256이
표시된다. ZIP 안의 `cloud-source-manifest.json`에는 다음 정보만 들어간다.

- schema와 루트 commit
- 서브모듈 path, credential-free HTTPS URL, pinned commit
- 소스 파일 수, 총 byte 수, 정렬된 소스 내용의 SHA-256

시간, 사용자 이름, 로컬 경로, branch 이름과 uncommitted 내용은 manifest에
저장하지 않는다. ZIP 항목의 시간과 순서도 고정되어 같은 입력은 같은 번들을
만든다.

## Fail-closed 차단 규칙

추적 파일이라도 아래 내용이 하나라도 있으면 ZIP을 만들지 않는다.

- `.env`, credential/key 파일, database, model weight
- `runtime_artifacts`, log, recording, cache, virtual environment,
  `node_modules`
- 임의 음성 파일. 저장소의 두 공개 테스트 fixture만 exact-path allowlist로
  허용한다.
- private-key header 또는 OpenAI, AWS, GitHub, Slack, Google,
  Hugging Face의 고신뢰도 token 형태
- 절대 경로, `..`, backslash, 중복 경로, 특수 archive entry
- credential을 포함할 수 있는 submodule URL, dirty/unpinned submodule,
  지원하지 않는 nested submodule

검사 실패 메시지는 파일 경로와 rule 이름만 표시하고 발견한 credential 값은
출력하지 않는다. 이 검사는 전용 secret scanner를 완전히 대체하지 않는다.
클라우드 제공자의 repository secret scanning도 함께 켜야 한다.

## 검증

```powershell
python -m unittest tests.tools.test_cloud_source_export -v
python -m compileall -q tools\cloud_source_export.py tests\tools
git diff --check
```

최종 ZIP을 만든 뒤에는 별도 위치에서 다음을 확인한다.

1. 출력 JSON의 `dirtyWorktreeExcluded`가 `false`인지 확인한다.
2. ZIP SHA-256을 배포 기록에 보존한다.
3. manifest의 root/submodule commit이 의도한 commit인지 확인한다.
4. ZIP을 격리 디렉터리에 풀어 Docker/CI build를 실행한다.
5. 런타임 비밀은 source가 아닌 cloud secret manager 또는 로컬 전용 mount로
   주입한다.

## Git 원격 저장소를 사용할 때

공개 Mindcraft 저장소는 pinned submodule로 유지할 수 있다. 루트 저장소를
private remote에 push한 뒤 clone하는 환경에서는 다음이 필요하다.

```bash
git clone --recurse-submodules <private-evelyn-repository>
git submodule update --init --recursive
```

루트 private repository credential을 Mindcraft 컨테이너나 Evelyn runtime에
전달하지 않는다. CI credential은 source checkout과 image publish에 필요한
최소 권한으로 분리하고, 장기 token을 이미지 layer나 build argument에 넣지
않는다.

## 아직 자동화하지 않는 범위

이 도구는 클라우드 계정 생성, remote 추가, push, object storage upload,
서비스 공개와 실제 사용자 데이터 이전을 수행하지 않는다. 어느 공급자와 어떤
운영 범위를 사용할지 정한 뒤, 별도의 명시적 승인으로 수행한다. 특히 기억 vault,
음성 프로필과 runtime artifacts 이전은 source transfer와 분리해 암호화·보존·
삭제 계약을 먼저 정해야 한다.
