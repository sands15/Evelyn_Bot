# Evelyn Skills Contract

Evelyn의 skill 시스템은 **코어 음성 파이프라인을 대체하기 위한 구조가 아니라**, 그 바깥의 확장 기능을 붙이기 위한 구조다.

## 코어와 skills의 경계
코어로 유지하는 것 (`main.py` 중심):
- STT
- router subLLM
- main LLM
- TTS
- 이들을 잇는 핵심 orchestration

skills로 확장하는 것:
- Minecraft
- 특수 도메인 기능
- 외부 executor 연동
- 보조 search workflow
- follow-up / automation
- 커스텀 route 기반 기능

## 최소 확장 규약
Evelyn skill의 최소 규약은 아래 4개다.

- `name`: 스킬 고유 이름
- `routes`: 이 스킬이 담당하는 route tuple
- `sources`: 허용 입력 source tuple (`text`, `voice` 등)
- `execute(context)`: 실제 실행 함수

## 공통 결과 규약
실제 실행 결과는 가능하면 `SkillResult`를 반환한다.

중요 필드 예시:
- `skill`
- `route`
- `status`
- `display_text`
- `answer_text`
- `dedupe_key`
- `followup_route`
- `followup_payload`
- `followup_delay_ms`
- `metadata`
- `payload`

## 충돌 정책
### skill name 충돌
- 기본 정책: **중복 이름 금지**
- 이미 등록된 이름이 있으면 등록 시 에러 발생
- 의도적으로 교체하려면 `replace=True`

### route ownership
- 현재 기본 방향은 **route ownership**이다.
- core-owned route와 extension-owned route를 분리해 충돌을 구조적으로 줄인다.
- 자세한 기준은 `C:\Evelyn\ROUTE_OWNERSHIP_POLICY.md` 참고.

### route 충돌
- route 중복은 현재 코드상 허용되지만, 구조적으로는 지양한다.
- 여러 skill이 같은 route를 광고하는 방식보다, route 1개당 대표 skill 1개를 권장한다.
- priority 기반 arbitration은 아직 미구현이며, 현재는 즉시 도입하지 않는다.
- 먼저 실제 충돌 사례와 운영상 필요를 본 뒤, 그다음 문서 초안 -> 구현 순서로 간다.

## 최소 외부 스킬 예시
```python
from evelyn_core.skills.base import SkillResult

name = "calendar"
routes = ("calendar",)
sources = ("text", "voice")
description = "Calendar lookup and scheduling skill."

async def execute(context):
    user_text = context.extras.get("user_text", "")
    return SkillResult(
        skill=name,
        route="calendar",
        display_text=f"calendar request: {user_text}",
        answer_text=f"calendar request: {user_text}",
        dedupe_key=f"calendar|{user_text}",
    )
```

## 로드 방법
### 1. Python 모듈로 로드
```python
from evelyn_core.skills.loader import load_skill_module
load_skill_module("my_package.my_skill")
```

### 2. 파일로 로드
```python
from evelyn_core.skills.loader import load_skill_file
load_skill_file(r"C:\my-skills\calendar_skill.py")
```

### 3. 디렉토리 전체 로드
```python
from evelyn_core.skills.loader import load_skills_from_directory
load_skills_from_directory(r"C:\my-skills", recursive=True)
```

## override가 필요할 때
```python
load_skill_module("my_package.my_skill", replace=True)
load_skill_file(r"C:\my-skills\calendar_skill.py", replace=True)
```

## 자동 로드
`evelyn_core.skills` import 시 아래 환경변수를 읽는다.

- `EVELYN_SKILL_MODULES`: `os.pathsep` 로 구분된 모듈 목록
- `EVELYN_SKILL_PATHS`: `os.pathsep` 로 구분된 파일/폴더 목록

예시 (Windows):
```powershell
$env:EVELYN_SKILL_PATHS = "C:\my-skills;C:\other-skill.py"
```

파일은 단일 스킬 파일로, 폴더는 내부의 `.py` 파일들을 스캔해서 로드한다.
