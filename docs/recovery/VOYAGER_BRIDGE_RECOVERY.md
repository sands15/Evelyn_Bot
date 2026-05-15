# Voyager Bridge Recovery

## 핵심 정리

이번 작업은 단순히 LLM 어댑터만 바꾸는 일이 아니었다.

Voyager는 아래 3층이 함께 맞아야 실제로 돈다.

1. **Action adapter layer**
   - Voyager ActionAgent -> Codex Gateway -> Codex CLI
   - 포트: `8787`
2. **Voyager orchestration layer**
   - `voyager_service.py` -> `upstream_voyager_runner.py`
   - 포트: `8765`
3. **Minecraft control plane**
   - VoyagerEnv -> mineflayer HTTP bridge -> Minecraft bot/session
   - 기본 포트: `3000`

즉, Codex 어댑터만 바꿔도 나머지 런타임 가정(venv, checkpoint, skill library, node bridge, mineflayer plugin, HTTP startup)이 그대로 남아 있어서 연쇄적으로 깨질 수 있다.

## 이번에 고친 것

### 1) 런처/기동 구조
- `start_voyager.bat`를 Evelyn 기본 스택과 분리
- Voyager 관련 프로세스를 visible window 기준으로 수정
- Codex gateway 전용 런처 추가

### 2) Codex gateway
- `codex exec` fallback 경로 복구
- Codex CLI 경로 탐색 보강 (`PATH`, `%APPDATA%\npm`, 명시 env)
- `/codex/action` 실요청 `OK` 응답까지 과거 검증 완료

### 3) Voyager runner startup 안정화
- runner 상태 파일/traceback 기록 보강
- `psutil` 누락으로 즉사하던 문제 완화
- 빈 checkpoint인데 resume 하려다 죽는 문제 수정
- 빈 `skill_library/skill/skills.json` resume 문제 수정

### 4) mineflayer bridge 방어코드
- `/start` 직후 연결 race 완화를 위해 HTTP 재시도 추가
- `express`, `body-parser` 없을 때 내장 HTTP fallback 추가
- `mineflayer-pvp`, `minecrafthawkeye`를 optional plugin으로 처리

## 현재 남은 핵심 blocker

현재 남은 본질적인 문제는 **adapter**가 아니라 **Minecraft control plane**이다.

증상:
- Voyager runner가 내부적으로 `127.0.0.1:3000/start` 에 붙으려다가 실패한 기록이 있음
- 즉, VoyagerEnv가 띄우는 mineflayer HTTP bridge가 실제 요청을 받을 준비가 안 되거나, 실행 직후 내려가거나, 의존성이 더 부족할 수 있음

정리하면:
- **Codex layer**: 거의 복구됨
- **Voyager service/runner layer**: 즉사 문제 상당수 복구됨
- **Mineflayer 3000 control plane**: 아직 최종 복구 필요

## 다음 확인 우선순위

1. 최신 패치 기준으로 mineflayer bridge가 실제로 `3000`에서 listen 하는지 재검증
2. `/start` 요청 수락 여부 확인
3. 필요 시 mineflayer 쪽 추가 누락 dependency 또는 startup exception 추적
4. 그 다음에야 Voyager -> Codex -> Minecraft end-to-end 완료 판정 가능

## 최신 상태 (2026-05-07 evening)

최신 재검증에서 다음이 확인됐다.

- `25565` 로컬 Minecraft 서버 리슨 확인
- `8765` Voyager service 정상 기동
- `8787` Codex gateway 정상 기동
- Voyager runner가 실제로 Minecraft에 붙어 observation을 읽음
  - inventory / position / health / hunger / nearby entities / nearby blocks 확인
- passive goal 기준 현재 task가 `Check inventory status`까지 진행됨

추가로 잡아서 고친 blocker:
- `control_primitives_context`가 `.txt`만 찾다가 `exploreUntil.js`를 못 읽는 문제 수정
- CurriculumAgent QA cache Chroma vectordb 와 `qa_cache.json` 불일치 시 자동 복구하도록 수정

현재 보수적 판정:
- **Voyager -> Minecraft observation 경로는 복구된 것으로 봐도 됨**
- **Codex gateway 단독 요청은 이미 성공 검증됨**
- 이후 active task 재검증에서 `ActionAgent -> Codex` 요청과 성공적인 코드 생성도 확인됨
- 최신 torch task 기준으로 inventory가 `torch 4 -> 8`, `coal 10 -> 9`, `stick 1 -> 0`로 바뀐 흔적이 있어, 생성된 in-world action 실행도 최소 1회는 실제로 성공한 것으로 보임
- 현재 남은 문제는 raw connectivity 보다는 **completed_tasks / last_result / success propagation** 쪽 루프 마감 신뢰성 문제에 더 가까움
- 추가 로컬 패치: `VoyagerEnv.reset()`/mineflayer `/start` 경로는 이제 이미 연결된 봇을 재사용해서 불필요한 disconnect/reconnect를 피한다. 명시적 `/stop`만 실제 disconnect를 유발한다.
