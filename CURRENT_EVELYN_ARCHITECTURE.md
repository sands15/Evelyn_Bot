# CURRENT_EVELYN_ARCHITECTURE

이 문서는 **현재 저장소 코드가 실제로 하는 일**만 적는다.
설계 의도, 희망 동작, README 설명, 예전 구조는 섞지 않는다.
기준 파일은 주로 아래다.

- `C:\Evelyn\start.bat`
- `C:\Evelyn\evelyn_core\start.bat`
- `C:\Evelyn\evelyn_core\start_env.bat`
- `C:\Evelyn\evelyn_core\start_main_llm.bat`
- `C:\Evelyn\evelyn_core\start_router_llm.bat`
- `C:\Evelyn\evelyn_core\start_sub_llm.bat`
- `C:\Evelyn\evelyn_core\start_tts.bat`
- `C:\Evelyn\evelyn_core\start_tts.ps1`
- `C:\Evelyn\evelyn_core\start_bot.bat`
- `C:\Evelyn\evelyn_core\start_bot.ps1`
- `C:\Evelyn\evelyn_core\run_main_llm.sh`
- `C:\Evelyn\evelyn_core\run_router_llm.sh`
- `C:\Evelyn\evelyn_core\run_sub_llm.sh`
- `C:\Evelyn\main.py`
- `C:\Evelyn\evelyn_core\config.py`
- `C:\Evelyn\evelyn_core\memory.py`

---

## 1. 현재 시작 구조

### 1-1. 루트 진입점
루트의 `C:\Evelyn\start.bat` 는 아래 한 줄만 한다.

- `call "%~dp0evelyn_core\start.bat" %*`

즉 실제 통합 시작 로직은 `evelyn_core\start.bat` 에 있다.

### 1-2. 통합 시작 배치(`evelyn_core\start.bat`)
현재 `evelyn_core\start.bat` 는 다음 순서로 동작한다.

1. `start_env.bat` 를 호출해서 공통 환경변수를 채운다.
2. `%OMNIVOICE_PROFILE_DIR%` 가 없으면 만든다.
3. `wt.exe` 사용 가능 여부를 검사한다.
4. **Windows Terminal이 없으면** 아래 5개 스타터를 순서대로 `call` 한다.
   - `start_main_llm.bat`
   - `start_router_llm.bat`
   - `start_sub_llm.bat`
   - `start_tts.bat`
   - `start_bot.bat`
5. **Windows Terminal이 있으면** `WT_WINDOW=evelyn` 을 사용해서 **한 개의 named WT window** 에 5개 탭을 붙인다.
   - Main-LLM 탭: `wsl.exe bash /mnt/c/Evelyn/evelyn_core/run_main_llm.sh`
   - Router-LLM 탭: `wsl.exe bash /mnt/c/Evelyn/evelyn_core/run_router_llm.sh`
   - Sub-LLM 탭: `wsl.exe bash /mnt/c/Evelyn/evelyn_core/run_sub_llm.sh`
   - TTS 탭: `powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_tts.ps1"`
   - Bot 탭: `powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_bot.ps1"`
6. Main-LLM 탭을 먼저 `start "" "%WT_EXE%" -w %WT_WINDOW% new-tab ...` 로 열고, `timeout /t 1 /nobreak >nul` 후 나머지 4개 탭을 같은 `-w %WT_WINDOW%` 로 연다.

현재 통합 시작 구조의 핵심은:

- **의도된 구조는 한 개 WT 창 + 5개 탭**
- 각 LLM 탭은 **WT에서 직접 `wsl.exe bash run_*.sh`** 를 실행한다.
- TTS/Bot 탭은 **WT에서 직접 PowerShell 스크립트** 를 실행한다.

---

## 2. 현재 서비스별 실행 구조

### 2-1. 공통 환경값 (`start_env.bat`)
기본값은 현재 아래처럼 들어간다.

- `LLAMA_DIR=/mnt/c/Users/Admin/llama.cpp`
- `VENV_ACT=source ~/venvs/vllm-env/bin/activate`

메인 LLM:
- `MAIN_LLM_GPU=1`
- `MAIN_LLM_PORT=9820`
- `MAIN_LLM_CONTEXT=4096`
- `MAIN_LLM_N_PARALLEL=1`
- `MAIN_LLM_REASONING=off`
- `MAIN_LLM_REASONING_BUDGET=0`
- `MAIN_LLM_MODEL=/home/sands12/.cache/huggingface/hub/models--unsloth--gemma-4-E4B-it-GGUF/snapshots/ce152932ac27bc40bc9c727386760424d50bb456/gemma-4-E4B-it-Q5_K_M.gguf`

서브 LLM:
- `SUB_LLM_GPU=0`
- `SUB_LLM_PORT=9821`
- `SUB_LLM_CONTEXT=8192`
- `SUB_LLM_REASONING_BUDGET=96`
- `SUB_LLM_MODEL=/home/sands12/.cache/huggingface/hub/models--LGAI-EXAONE--EXAONE-3.5-7.8B-Instruct-GGUF/snapshots/c618bf67338171760c72c3f109f2900cb7d79855/EXAONE-3.5-7.8B-Instruct-BF16.gguf`

라우터 LLM:
- `ROUTER_LLM_GPU=0`
- `ROUTER_LLM_PORT=9822`
- `ROUTER_LLM_CONTEXT=4096`
- `ROUTER_LLM_REASONING_BUDGET=96`
- `ROUTER_LLM_MODEL=/home/sands12/.cache/huggingface/hub/models--unsloth--gemma-4-E2B-it-GGUF/snapshots/f064409f340b34190993560b2168133e5dbae558/gemma-4-E2B-it-UD-Q6_K_XL.gguf`

TTS:
- `OMNIVOICE_VENV=C:\Users\Admin\omnivoice-server\.venv`
- `OMNIVOICE_PROFILE_DIR=%~dp0..\omnivoice_profiles`
- `TTS_PORT=8880`

기타:
- `START_WAIT_TIMEOUT_SEC=120`
- `START_WAIT_INTERVAL_SEC=2`
- `OPUS_ERROR_TO_SILENCE=false`
- `STT_USE_RAW_48K=false`

### 2-2. Main-LLM
현재 `run_main_llm.sh` 는:

1. `set -euo pipefail`
2. `eval "$VENV_ACT"`
3. `export CUDA_VISIBLE_DEVICES="$MAIN_LLM_GPU"`
4. 모델 파일 존재 여부 검사
5. `cd "$LLAMA_DIR"`
6. `exec ./build/bin/llama-server` 실행

실행 인자:
- `-m "$MAIN_LLM_MODEL"`
- `--host 0.0.0.0`
- `--port "$MAIN_LLM_PORT"`
- `--flash-attn on`
- `-ngl 999`
- `-c "$MAIN_LLM_CONTEXT"`
- `-np "$MAIN_LLM_N_PARALLEL"`
- `--reasoning "$MAIN_LLM_REASONING"`
- `--reasoning-budget "$MAIN_LLM_REASONING_BUDGET"`

`start_main_llm.bat` 는:
- 먼저 `:port_ready` 로 `%MAIN_LLM_PORT%` 가 이미 열려 있으면 종료 코드 2로 빠진다.
- `--inline` 이면 `wsl.exe bash -lc "%WSL_CMD%"` 를 직접 실행한다.
- inline이 아니면 WT 탭 또는 cmd 창에서 `wsl.exe bash /mnt/c/Evelyn/evelyn_core/run_main_llm.sh` 를 연다.
- 탭 제목은 `Main-LLM` 이다.

### 2-3. Router-LLM
현재 `run_router_llm.sh` 는:
- `CUDA_VISIBLE_DEVICES="$ROUTER_LLM_GPU"`
- `llama-server`
- 포트 `9822`
- reasoning `on`
- reasoning budget `96`
- context `4096`

`start_router_llm.bat` 는 Main과 같은 구조다.
차이는 모델/포트/제목만 Router-LLM 쪽 값이라는 점이다.

### 2-4. Sub-LLM
현재 `run_sub_llm.sh` 는:
- `CUDA_VISIBLE_DEVICES="$SUB_LLM_GPU"`
- `llama-server`
- 포트 `9821`
- reasoning `on`
- reasoning budget `96`
- context `8192`

`start_sub_llm.bat` 도 Router와 같은 구조다.
차이는 모델/포트/제목만 Sub-LLM 쪽 값이라는 점이다.

### 2-5. TTS
현재 `start_tts.ps1` 는:

1. 프로젝트 루트를 `C:\Evelyn` 으로 잡고 `Set-Location`
2. `profileDir` 를 `%OMNIVOICE_PROFILE_DIR%` 또는 `C:\Evelyn\omnivoice_profiles` 로 정함
3. `venvDir` 를 `%OMNIVOICE_VENV%` 또는 `C:\Users\Admin\omnivoice-server\.venv` 로 정함
4. `ttsPort` 를 `%TTS_PORT%` 또는 `8880` 으로 정함
5. `$env:CUDA_VISIBLE_DEVICES = '1'`
6. 프로필 디렉터리가 없으면 생성
7. `python.exe -m omnivoice_server.cli --host 127.0.0.1 --port $ttsPort --device cuda --profile-dir $profileDir`

`start_tts.bat` 는:
- 먼저 `%TTS_PORT%` 가 이미 listening 이면 종료 코드 2로 빠진다.
- WT가 있으면 `start_tts.ps1` 를 새 TTS 탭에서 실행한다.
- WT가 없으면 별도 PowerShell 창에서 `start_tts.ps1` 를 실행한다.

### 2-6. Bot
현재 `start_bot.ps1` 는:

1. 프로젝트 루트를 `C:\Evelyn` 으로 잡고 `Set-Location`
2. `Wait-Port` 함수로 아래 순서대로 127.0.0.1 포트를 기다린다.
   - `9820` Main-LLM
   - `9822` Router-LLM
   - `9821` Sub-LLM
   - `8880` OmniVoice-TTS
3. `DISCORD_BOT_TOKEN` 이 없으면 throw
4. `.venv\Scripts\python.exe` 가 있으면 그것으로 `main.py` 실행
5. 없으면 `py -3 main.py` 실행

현재 `start_bot.bat` 는:
- `--inline` 이 아니면 WT 탭 또는 별도 PowerShell 창에서 `start_bot.ps1` 를 연다.
- `--inline` 이면 배치 내부에서 포트 대기 후 `main.py` 를 실행한다.

즉 현재 Bot은 **자기 탭 안에서 스스로 main/router/sub/tts 준비를 기다린 다음 main.py 를 실행하는 구조**다.

---

## 3. 현재 답장 구조의 핵심 요약

현재 코드 기준으로 사용자에게 실제 답을 만드는 경로는 **메인 LLM 하나**다.

- 메인 답변 생성: `LLM_SERVER_URL` (`127.0.0.1:9820`)
- 라우터 판단 / cognitive 상태 생성: `ROUTER_LLM_URL` (`127.0.0.1:9822`)
- 장기기억 요약/사실/열린질문 업데이트: `SUMMARY_LLM_URL` (`127.0.0.1:9821`)
- 음성 합성: OmniVoice (`127.0.0.1:8880`)

즉 현재 역할은 실제 코드 기준으로 아래다.

- **Main**: 사용자에게 보이는 실제 답변 생성
- **Router**: route JSON 생성 + cognitive_state JSON 생성
- **Sub(SUMMARY)**: rolling summary / durable facts / open questions 저장
- **TTS**: 음성 합성
- **Bot**: 디스코드 이벤트 처리, STT, 메모리, 라우팅 호출, TTS 재생

중요한 현재 사실:

- `prepare_llm_messages()` 는 항상 `classify_llm_route_async()` 를 호출한다.
- 하지만 현재 코드에서는 그 route 값을 **실제 분기 실행에 거의 사용하지 않고**, 주로 로그로 남긴다.
- 현재 응답 경로는 route가 `main_direct / sub_hint / sub_wait` 여도 결국 **메인 LLM** 으로 간다.
- 즉 현재 route는 **실행 분기보다는 진단/메타값에 가깝다.**

---

## 4. 현재 텍스트 답장 구조

현재 텍스트 답장은 `main.py` 의 `on_message()` 에서 처리한다.

### 4-1. 텍스트 트리거 조건
텍스트 입력은 아래 둘 중 하나일 때만 답한다.

1. `contains_wake_word(message.content)` 가 참인 경우
2. `message.reference` 로 가져온 원문이 봇 자신의 메시지인 경우

둘 다 아니면 `await bot.process_commands(message)` 후 바로 return 한다.

즉 현재 텍스트 일반 메시지에는 상시 반응하지 않는다.

### 4-2. 텍스트 처리 순서
현재 순서는 아래와 같다.

1. `last_text_channel_ids[message.guild.id] = message.channel.id` 저장
2. wake word 또는 bot reply 여부 검사
3. 사용자 텍스트 정리
   - wake word면 `strip_voice_wake_word(message.content)`
   - 아니면 `message.content.strip()`
   - 비면 `"부르셨나요?"`
4. `get_conversation_history(message.guild.id)` 로 길드별 대화 히스토리 준비
5. `guild_locks[guild_id]` 락 획득
   - 이미 락이 걸려 있으면 `⏳ 지금 다른 응답을 처리 중이야. 잠깐만.` 전송 후 종료
6. `message.channel.typing()` 안에서
   - `AUTO_JOIN_VOICE` 가 true면 `ensure_voice_client(message)` 로 음성 채널 자동 연결 시도
   - `answer = await ask_llm_once(...)`
   - `plain_answer = strip_omnivoice_tags(answer)` (비면 원문 answer)
   - `await message.channel.send(visible_text(answer))`
7. 락 안에서 후처리
   - `append_history(...)`
   - `schedule_memory_update(...)`
   - `schedule_search_followup(...)`
   - `vc` 가 있으면 `await speak_answer(vc, answer)` 로 음성 채널에도 읽음
8. 마지막에 `await bot.process_commands(message)`

### 4-3. 현재 텍스트 답장의 실제 LLM 경로
`ask_llm_once()` 는 아래 순서로 간다.

1. `prepare_llm_messages()` 호출
2. cognitive_state가 `ask` 이고 `question_for_user` 가 있으면
   - 원래 user_text 대신 `question_for_user` 를 `guided_user_text` 로 사용
3. `build_main_response_guidance(cognitive_state, source=source)` 를 붙여 최종 user message 생성
4. 메인 LLM (`LLM_SERVER_URL`) 로 non-stream 요청
5. 응답 본문이 있으면 그대로 반환
6. 본문이 없으면 reasoning에서 답변을 추출 시도
7. 그것도 없으면 fallback
   - user_text 비어 있으면 `응, 듣고 있어.`
   - 아니면 `응, 잠깐만.`

즉 현재 ask 행동은 **router/cognitive가 직접 사용자에게 질문하지 않고**, 메인 LLM에 `question_for_user` 를 실제 user content처럼 넣어서 메인 LLM이 질문을 말하게 만드는 구조**다.**

---

## 5. 현재 음성 답장 구조

현재 음성 답장은 `process_member_audio()` 에서 처리한다.

### 5-1. 음성 입력 시작 조건
이 함수는 `EvelynVoiceClient` 가 사용자 PCM을 발화 단위로 넘겼을 때 불린다.

초기 차단:
- `member is None` 이면 return
- `member.bot` 이면 return
- `guild is None` 이면 return

### 5-2. 음성 전처리
현재 `STT_USE_RAW_48K` 기본값은 false 이다.
그래서 기본 경로는:

- `audio16k = prepare_stt_audio(pcm_bytes)`
- `audio_for_wake = audio16k`
- `stt_sampling_rate = TARGET_RATE`
- `wake_sampling_rate = TARGET_RATE`

### 5-3. 음성 차단/필터링
현재 아래 검사를 통과해야 한다.

1. 전체 raw 길이
   - `raw_seconds <= VOICE_MIN_TOTAL_SEC` 면 무시
2. VAD
   - `VAD_ENABLED` 이고 `is_probably_silent(...)` 면 기본적으로 무시
   - 단 waveform override 조건이면 계속 진행
3. wake probe 실행
   - `detect_wake_word_sync(audio_for_wake, sampling_rate=wake_sampling_rate)`
4. 환경음 후보 / filler 후보 / 반복소음 후보여도 **현재는 로그만 찍고 full STT는 계속 진행**
5. wake가 미검출이어도 **현재는 full STT를 계속 진행**
6. full STT 후 `should_ignore_short_transcription()` 검사
7. 최종적으로 `should_reply_to_voice()` 검사

`should_reply_to_voice()` 의 현재 규칙은 아래다.

- `bot_speaking_guilds` 에 있으면 차단
- 최근 TTS 종료 직후 `POST_TTS_IGNORE_SEC` 안이면 차단
- 텍스트가 비면 차단
- wake_detected 가 false 이고 wake word도 없으면 차단
- 길이가 너무 짧고 wake_detected도 false면 차단
- 최근 reply cooldown 안이면 차단
- 직전 voice text 와 유사하면 차단

통과하면 `last_voice_text[guild_id]` 와 `last_voice_reply_at[guild_id]` 를 갱신한다.

### 5-4. 음성 STT 구조
현재 음성 STT는 아래다.

1. wake probe
   - `slice_audio_window(..., WAKE_AUDIO_SEC)`
   - `transcribe_audio16k_sync(..., stage="wake")`
2. wake 2차 확인
   - wake 첫 히트가 있으면 `WAKE_CONFIRM_AUDIO_SEC` 로 재전사
3. full STT
   - `transcribe_audio16k_sync(..., stage="full")`
4. full-rescore
   - `STT_FULL_RESCORING_ENABLED` 가 true 면
   - 더 큰 token 허용량으로 `stage="full-rescore"` 한 번 더 돌림
   - `choose_full_stt_candidate(primary_text, rescore_text, wake_probe=wake_probe)` 로 최종 텍스트 선택
5. 후처리
   - `apply_stt_post_corrections(text, wake_detected=wake_detected)`
6. 짧은 잡음 판정 후 무시 가능
7. 디버그 오디오/메타 저장 가능

### 5-5. 음성 답변 생성 구조
음성에서 실제 답변은 `ask_llm_and_speak_streaming()` 이 한다.

순서:

1. `ask_llm_streaming(...)` 으로 메인 LLM 스트리밍 요청
2. 스트림으로 받은 delta_text 를 `split_tts_sentences()` 로 문장 단위 분리
3. 문장이 나오면 `sentence_queue` 에 넣음
4. 별도 playback task인 `stream_tts_sentences()` 가 queue에서 문장을 꺼내 TTS 생성 및 재생
5. 전체 스트림이 끝나면 최종 answer를 반환

현재 음성은 **메인 LLM 출력 스트림을 받아 문장 단위로 TTS를 먼저 시작하는 구조**다.
즉 텍스트처럼 완성 후 한번에 읽지 않고, 음성에서는 streaming path를 쓴다.

### 5-6. 음성 후처리
답변이 끝나면 현재 아래를 한다.

- `append_history(guild_id, history_user_text, plain_answer)`
- `schedule_memory_update(...)`
- `schedule_search_followup(...)`

즉 음성도 텍스트와 마찬가지로 답변 후 메모리 저장과 검색 후속작업 예약을 한다.

---

## 6. 현재 LLM 메시지 준비 구조

현재 모든 메인 답변 전에는 `prepare_llm_messages()` 가 돈다.

### 6-1. route 계산
`prepare_llm_messages()` 는 맨 먼저 `classify_llm_route_async()` 를 호출한다.

현재 route 후보는:
- `main_direct`
- `sub_hint`
- `sub_wait`

#### fallback heuristic
`classify_llm_route_fallback()` 의 현재 규칙:

- voice 입력이고 `should_force_voice_context_route(text)` 가 false 면 무조건 `main_direct`
- 짧은 text 입력이면 `main_direct`
- 문맥 marker 개수와 길이에 따라 `sub_hint` 또는 `sub_wait`

#### router LLM 실제 사용 조건
`classify_llm_route_async()` 는 다음일 때 router LLM을 **안 쓰고 fallback만 반환**한다.

- `source == "voice"` 이고 `should_force_voice_context_route(user_text)` 가 false
- 또는 `ROUTER_LLM_ENABLED == false`

즉 음성은 기본적으로 router LLM route 호출을 생략하고 fallback route만 쓴다.

#### 현재 route의 실제 영향
현재 코드에서는 route를 계산하고 로그를 찍지만, 그 route에 따라 메인 답변 경로를 실제 분기하지 않는다.
즉 현재 route는 **실행 경로를 바꾸는 하드 분기점이 아니라 메타 판단값**이다.

### 6-2. cognitive_state 계산
현재 `prepare_llm_messages()` 는 `guild_id is not None` 이면 **매번 `update_cognitive_state(guild_id, user_text)`** 를 await 한다.

즉 현재는:
- 길드 입력이면 text/voice 모두 fresh cognitive_state를 다시 계산한다.
- 예전처럼 일부 route에서만 계산하는 구조가 아니다.

### 6-3. memory context 합성
`build_memory_context()` 는 아래를 읽어 system prompt 쪽에 붙인다.

- `rolling_summary.txt`
- 최근 hot `raw_transcript.jsonl`
- vault raw 중 관련 항목
- 현재 `cognitive_state.json` 또는 fresh cognitive_state
- 관련 `durable_facts`
- 관련 `open_questions` / `open_loops`

이 memory context는 메인 LLM system prompt에 병합된다.

### 6-4. ask gating
`apply_ask_gating()` 는 현재 `action == "ask"` 인 state에 대해 아래 조건을 본다.

- `question_for_user` 가 비어 있으면 gate
- `confidence < threshold` 면 gate

기본 threshold는 현재 config 기준으로:
- `ASK_CONFIDENCE_THRESHOLD_TEXT = 0.00`
- `ASK_CONFIDENCE_THRESHOLD_VOICE = 0.00`

즉 현재 기본값으로는 confidence threshold 때문에 ask가 막히지는 않는다.
막히는 경우는 보통 `question_for_user` 가 비었을 때다.

ask가 gate 되면:
- voice source: `wait`
- text source: `answer`

으로 바뀐다.

---

## 7. 현재 cognitive 구조

현재 cognitive는 `update_cognitive_state()` 가 담당한다.

입력 자료:
- 기존 `cognitive_state.json`
- `rolling_summary.txt`
- 최근 raw transcript
- 최근 durable facts
- 최근 open questions
- 현재 사용자 입력

출력 JSON 형식:
- `action`: `answer | ask | wait`
- `confidence`
- `user_intent`
- `state_summary`
- `question_for_user`
- `main_prompt_hint`
- `reason_brief`
- `retrieved_context_ids`

실패 시 현재 동작:
- context size 에러면 compact retry 시도
- 그래도 실패하면 fallback state 저장
  - 기본 fallback action은 `answer`
  - confidence는 `0.5`
  - `main_prompt_hint` 는 `짧고 자연스럽게 답해라.`

성공하면:
- state를 normalize
- 부족한 `state_summary` 는 기존 state 또는 user_text 로 메움
- 부족한 `main_prompt_hint` 는 `짧고 자연스럽게 답해라.` 로 메움
- `cognitive_state.json` 저장

현재 cognitive의 의미는:
- 메인 LLM이 지금 **답해야 하는지**, **짧게 되물어야 하는지**, **더 듣는 편이 자연스러운지** 정하는 내부 상태다.
- 하지만 최종 말은 여전히 메인 LLM이 만든다.

---

## 8. 현재 메모리 구조

메모리 루트는 `BOT_MEMORY_DIR` 또는 기본적으로 `C:\Evelyn\bot_memory` 계열이다.
길드별 디렉터리는 `guild_<id>` 형식이다.

현재 사용 파일:
- `rolling_summary.txt`
- `raw_transcript.jsonl`
- `durable_facts.jsonl`
- `open_questions.jsonl`
- `open_loops.jsonl`
- `cognitive_state.json`
- `vault\facts.jsonl`
- `vault\questions.jsonl`
- `vault\raw\YYYY-MM-DD.jsonl`

### 8-1. 현재 저장 흐름
사용자-봇 한 턴이 끝나면 `schedule_memory_update()` 가 아래를 한다.

1. `append_raw_transcript_rows(...)`
   - hot raw transcript와 일자별 vault raw 둘 다 저장
2. `asyncio.create_task(update_long_term_memory(...))`
3. `asyncio.create_task(update_cognitive_state(...))`

즉 장기 메모리 갱신과 cognitive 갱신은 **답변 뒤 비동기 태스크** 로도 한 번 더 돈다.

### 8-2. 장기 메모리 업데이트
`update_long_term_memory()` 는 SUMMARY_LLM_URL (`9821`) 에 JSON 지시를 보내서 아래를 만든다.

- `summary_update`
- `durable_facts`
- `open_questions`

이 결과로:
- rolling summary 갱신
- durable facts 추가 저장
- open questions 추가 저장

즉 현재 SUMMARY 모델은 **사용자에게 답하지 않고 메모리 관리만 한다.**

### 8-3. 열린 질문 닫기
현재 `resolve_open_question_rows(guild_id, *reference_texts)` 가 있다.

현재 사용처는 검색 후속답변 쪽이다.
검색 태스크가 결과를 찾고 answer를 만든 뒤:
- `resolve_open_question_rows(guild_id, query, answer)`
를 호출해 관련 open question / open loop / vault question 항목을 제거한다.

---

## 9. 현재 검색 구조

현재 검색은 **메인 답변이 검색을 약속하는 표현을 썼을 때만** 예약된다.

### 9-1. 검색 예약 조건
`schedule_search_followup()` 는 아래일 때만 동작한다.

- `guild_id` 가 존재하고
- `answer_promises_search(answer)` 가 true

현재 promise marker 예시:
- `찾아볼게`
- `찾아보고`
- `검색해볼게`
- `확인해볼게`
- `알아볼게`
- `찾는 중`
- `찾아보고 있어`
- `자료 찾아볼게`

completed marker 예시:
- `찾아봤`
- `검색해봤`
- `확인해봤`
- `알아봤`
- `찾아보니`
- `검색해보니`
- `결과는`

completed marker가 있으면 follow-up search를 예약하지 않는다.

### 9-2. 검색 실행
현재 검색은 `run_search_followup()` 가 한다.

순서:
1. `search_duckduckgo(query)` 로 DuckDuckGo Instant API 시도
2. 부족하면 `https://html.duckduckgo.com/html/` HTML 검색 파싱
3. 결과를 `answer_from_search_results()` 로 메인 LLM에 짧게 요약시키기
4. `resolve_open_question_rows(guild_id, query, answer)` 로 관련 열린 질문 닫기
5. `cognitive_state.json` 을 강제로 `action="answer"`, `reason_brief="search_completed"` 쪽으로 덮어쓰기
6. `deliver_proactive_followup(...)` 호출

### 9-3. 검색 후속답변 전달
`deliver_proactive_followup()` 는 현재 아래를 한다.

1. `channel_id` 가 있으면 해당 텍스트 채널에 `visible_text(answer)` 전송
2. 길드 voice client가 연결 중이면 `speak_answer(vc, answer)` 로 음성 발화
3. `append_history(...)`
4. `schedule_memory_update(...)`

즉 검색 follow-up 은 현재 **사용자의 추가 질문이 없어도 스스로 텍스트/음성 후속답변을 할 수 있는 유일한 명시적 경로**다.

### 9-4. 현재 proactive 성격의 정확한 범위
현재 코드에서 자발적으로 새 답을 다시 말하는 구조는 기본적으로 이 검색 후속답변 태스크 쪽이다.
일반 메모리 업데이트나 cognitive 업데이트가 끝났다고 스스로 새 메시지를 보내지는 않는다.

---

## 10. 현재 TTS 구조

현재 메인 TTS 생성 함수는 `create_omnivoice_source()` 다.

동작:
1. `clean_tts_text(text)`
2. `OmniVoicePCMStream` 생성
3. 내부 producer task 생성
4. `POST {OMNIVOICE_SERVER_URL}/v1/audio/speech`
   - `model = OMNIVOICE_MODEL`
   - `input = text`
   - `voice = OMNIVOICE_VOICE`
   - `response_format = pcm`
   - `stream = OMNIVOICE_STREAM`
   - `language = OMNIVOICE_LANGUAGE` (있으면)
5. 200 응답이면 chunk를 `source.feed_pcm24_mono(chunk)` 로 계속 밀어넣음
6. `OMNIVOICE_VOICE` 가 `clone:` 으로 시작하고 실패하면 `auto` voice 로 한 번 더 요청
7. 둘 다 실패하면 `source.fail(e)` 로 오디오 소스에 에러 기록
8. 성공하면 `source.finish()`

현재 TTS 재생은:
- 텍스트/검색 follow-up 경로에서는 `speak_answer()`
- 음성 스트리밍 경로에서는 `stream_tts_sentences()`

을 통해 이뤄진다.

`warmup_tts_server()` 는 봇 on_ready 시 아래를 한다.
- `/health` 체크
- `안녕` 한 번 스트리밍 TTS 요청
- 첫 chunk가 오면 warmup 완료로 간주

즉 현재 TTS는:
- 별도 OmniVoice 서버 프로세스가 따로 뜨고
- 봇은 HTTP PCM stream client 역할만 한다.

---

## 11. 현재 디스코드 음성 연결 구조

현재 음성 연결은 커스텀 `EvelynVoiceClient` 를 쓴다.

핵심 흐름:
- `ensure_voice_client(message)` 또는 `join/rejoin` 명령
- `ensure_listening_voice_client(guild, target_channel)`
- 필요하면 `connect_evelyn_voice_client()`
- 연결되면 `vc.on_user_audio = process_member_audio`
- `vc.listen()` 시작

즉 현재 구조에서 실제 음성 입력 진입점은 `EvelynVoiceClient -> process_member_audio()` 연결이다.

---

## 12. 현재 conversation history 구조

메인 LLM용 in-memory history 는 `guild_histories[guild_id]` 에 유지된다.
형식은 OpenAI style list 이며, 첫 항목은 항상 `SYSTEM_PROMPT` 를 담는 system message다.

현재 `append_history()` 는 매 턴마다 아래 두 줄을 메모리 history에 추가한다.
- `{"role": "user", "content": clean_text(user_text)}`
- `{"role": "assistant", "content": clean_text(answer)}`

그리고 `trim_history()` 로 `MAX_HISTORY_ITEMS` 상한을 유지한다.

즉 현재는:
- 파일 기반 memory context
- 길드별 in-memory chat history

둘 다 같이 메인 LLM 프롬프트 형성에 기여한다.

---

## 13. 현재 명령어 구조

현재 주요 명령어는 아래가 있다.

- `들어와` / `join`
- `다시들어와` / `rejoin`
- `나가` / `leave`
- `재시작` / `restart`
- `종료` / `shutdown` / `quit` / `exit`
- `상태` / `status`
- `접두사` / `prefix`
- `초기화` / `reset`

현재 텍스트 일반 대화는 wake word 또는 bot reply 기준이고,
명령어는 command prefix 기준으로 처리된다.
기본 prefix 는 config 기본값상 `!` 다.

---

## 14. 현재 구조에서 중요한 사실만 다시 요약

1. **실제 사용자 답변 생성기는 main LLM 하나다.**
   - main: `9820`
   - router: `9822`
   - sub(summary): `9821`

2. **router는 현재 route와 cognitive JSON을 만든다.**
   하지만 route는 현재 메인 응답 경로를 실질적으로 분기하지 않는다.

3. **sub(summary) 모델은 현재 메모리 관리 전용이다.**
   - rolling summary
   - durable facts
   - open questions
   저장에만 직접 쓰인다.

4. **메인 답변 전에는 길드 입력이면 매번 fresh cognitive_state를 await 한다.**

5. **ask 행동이 나오면 메인 LLM이 그 질문을 말한다.**
   router/cognitive가 직접 사용자에게 따로 메시지를 보내지 않는다.

6. **음성은 full STT 후 메인 LLM 스트리밍 + 문장 단위 TTS 스트리밍 구조다.**

7. **자발적 후속발화는 현재 검색 follow-up 경로가 가장 명시적이다.**
   일반 memory/cognitive 완료만으로는 스스로 새 메시지를 보내지 않는다.

8. **통합 시작 구조의 현재 목표는 한 WT 창 + 5개 탭이다.**
   - Main-LLM
   - Router-LLM
   - Sub-LLM
   - TTS
   - Bot

9. **Bot 탭은 실제 실행 전에 4개 백엔드 포트를 기다린다.**
   - 9820
   - 9822
   - 9821
   - 8880

10. **voice search follow-up 은 last_text_channel_ids 를 참조해서 텍스트 채널에도 후속결과를 보내려 한다.**
    즉 음성에서 시작한 검색 follow-up도 마지막 텍스트 채널 ID를 사용할 수 있다.

---

## 15. 이 문서의 의미

이 문서는 "현재 코드가 실제로 어떻게 동작하는가"만 적은 스냅샷이다.
즉 아래를 뜻하지 않는다.

- 이 구조가 최종 설계라는 뜻 아님
- README 설명과 완전히 일치한다는 뜻 아님
- 원하는 동작이라는 뜻 아님

오직 현재 파일 기준의 실제 구조만 기록한다.
