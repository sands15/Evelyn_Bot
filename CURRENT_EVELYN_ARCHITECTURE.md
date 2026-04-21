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
- `C:\Evelyn\evelyn_core\text.py`
- `C:\Evelyn\evelyn_voice\client.py`

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
6. Main-LLM 탭을 먼저 열고 `timeout /t 1 /nobreak >nul` 후 나머지 탭을 같은 WT window에 연다.

현재 통합 시작 구조의 핵심은:

- **의도된 구조는 한 개 WT 창 + 5개 탭**
- 각 LLM 탭은 WT에서 직접 `wsl.exe bash run_*.sh` 를 실행한다.
- TTS/Bot 탭은 WT에서 직접 PowerShell 스크립트를 실행한다.

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
- `MAIN_LLM_MODEL=/home/sands12/.cache/huggingface/hub/models--unsloth--gemma-4-E4B-it-GGUF/.../gemma-4-E4B-it-Q5_K_M.gguf`

서브 LLM:
- `SUB_LLM_GPU=0`
- `SUB_LLM_PORT=9821`
- `SUB_LLM_CONTEXT=8192`
- `SUB_LLM_REASONING_BUDGET=96`
- `SUB_LLM_MODEL=/home/sands12/.cache/huggingface/hub/models--LGAI-EXAONE--EXAONE-3.5-7.8B-Instruct-GGUF/.../EXAONE-3.5-7.8B-Instruct-Q8_0.gguf`

라우터 LLM:
- `ROUTER_LLM_GPU=0`
- `ROUTER_LLM_PORT=9822`
- `ROUTER_LLM_CONTEXT=4096`
- `ROUTER_LLM_REASONING_BUDGET=96`
- `ROUTER_LLM_MODEL=/home/sands12/.cache/huggingface/hub/models--unsloth--gemma-4-E2B-it-GGUF/.../gemma-4-E2B-it-UD-Q6_K_XL.gguf`

TTS:
- `OMNIVOICE_VENV=C:\Users\Admin\omnivoice-server\.venv`
- `OMNIVOICE_PROFILE_DIR=%~dp0..\omnivoice_profiles`
- `TTS_PORT=8880`

기타:
- `START_WAIT_TIMEOUT_SEC=120`
- `START_WAIT_INTERVAL_SEC=2`
- `OPUS_ERROR_TO_SILENCE=false`
- `STT_USE_RAW_48K=false`
- `VOICE_CONSOLE_ONLY_STT_AND_REPLY=true` 일 때 콘솔 허용 prefix만 출력한다.

### 2-2. Main-LLM
현재 `run_main_llm.sh` 는:

1. `set -euo pipefail`
2. `eval "$VENV_ACT"`
3. `export CUDA_VISIBLE_DEVICES="$MAIN_LLM_GPU"`
4. 모델 파일 존재 여부 검사
5. `cd "$LLAMA_DIR"`
6. `exec ./build/bin/llama-server`

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

### 2-3. Router-LLM
`run_router_llm.sh` 는:
- GPU `0`
- 포트 `9822`
- reasoning `on`
- reasoning budget `96`
- context `4096`

### 2-4. Sub-LLM
`run_sub_llm.sh` 는:
- GPU `0`
- 포트 `9821`
- reasoning `on`
- reasoning budget `96`
- context `8192`

### 2-5. TTS
`start_tts.ps1` 는:
- 프로젝트 루트를 `C:\Evelyn` 으로 잡는다.
- OmniVoice profile dir / venv / port 를 잡는다.
- `$env:CUDA_VISIBLE_DEVICES = '1'`
- `python -m omnivoice_server.cli --host 127.0.0.1 --port 8880 --device cuda --profile-dir <dir>` 를 실행한다.

### 2-6. Bot
`start_bot.ps1` 는 아래 순서로 대기 후 실행한다.

1. `9820` Main-LLM
2. `9822` Router-LLM
3. `9821` Sub-LLM
4. `8880` TTS
5. `main.py`

즉 현재 Bot은 **자기 탭 안에서 모델/TTS 준비를 모두 기다린 뒤 main.py 를 실행하는 구조**다.

---

## 3. 현재 역할 분담

현재 코드 기준 역할은 아래다.

- **Main LLM (`9820`)**: 사용자에게 보이는 실제 답변 생성
- **Router LLM (`9822`)**: route 판단 + cognitive_state 생성
- **Sub/Summary LLM (`9821`)**: rolling summary / durable facts / open questions 갱신
- **OmniVoice TTS (`8880`)**: 음성 합성
- **Bot (`main.py`)**: 디스코드 이벤트, STT, wake gating, memory, routing, TTS 재생

중요한 현재 사실:
- route 계산은 여전히 존재한다.
- 하지만 실제 최종 답변 경로는 대부분 메인 LLM으로 수렴한다.
- cognitive_state 는 실행 경로 일부를 바꾸기 시작했지만, 최종 자연어 생성은 여전히 메인 LLM이 담당한다.

---

## 4. 현재 텍스트 입력 구조

### 4-1. on_message 기본 동작
현재 텍스트 입력은 `main.py` 의 `on_message()` 에서 처리한다.

텍스트가 일반 대화 경로로 들어가는 조건은 아래 둘 중 하나다.

1. `contains_wake_word(message.content)` 가 참
2. `message.reference` 로 가져온 원문이 봇 메시지인 경우

추가로 현재는 **길드 command prefix로 시작하는 메시지는 대화 라우팅에서 제외**된다.
즉 `!재시작` 같은 메시지는 일반 대화로 처리하지 않고 바로 `bot.process_commands(message)` 로 넘긴다.

### 4-2. 텍스트 처리 순서
현재 순서는 아래와 같다.

1. 세션 키/메모리 키 계산
2. command prefix 메시지면 즉시 command 처리
3. wake word 또는 bot-reply 여부 검사
4. 사용자 텍스트 정리
5. 길드 락 획득
6. `ask_llm_once(...)` 로 메인 응답 생성
7. 채널에 visible text 전송
8. history append
9. memory update 예약
10. search follow-up 예약
11. voice client가 있으면 TTS로도 읽기
12. 마지막에 `bot.process_commands(message)`

### 4-3. 현재 텍스트 답변 LLM 경로
`ask_llm_once()` 는 아래를 한다.

1. `prepare_llm_messages()` 호출
2. cognitive_state가 ask 이고 `question_for_user` 가 있으면 그 질문을 user content처럼 사용
3. `build_main_response_guidance()` 를 붙여 최종 user prompt 생성
4. 메인 LLM non-stream 요청
5. 응답 본문 사용
6. 본문이 없으면 reasoning에서 추출 시도
7. 그것도 없으면 fallback

즉 텍스트에서는 현재 **메인 LLM non-stream** 이 기본이다.

---

## 5. 현재 음성 입력 파이프라인

현재 음성 입력은 `EvelynVoiceClient` 와 `main.py` 의 `process_member_audio()` 체인에서 처리한다.

### 5-1. Voice receive 런타임 구조
`evelyn_voice/client.py` 에서 listen 시작 후 다음 3개 루프가 살아 있다.

- `_receive_loop()`
- `_decrypt_loop()`
- `_utterance_loop()`

`_receive_loop()` 는 RTP packet을 받고 `media_queue` 로 넣는다.
`_decrypt_loop()` 는 reorder 처리 후 packet을 utterance state로 보낸다.
`_utterance_loop()` 는 발화 종료 조건이 만족된 utterance를 `_process_utterance_packets()` 로 넘긴다.

### 5-2. SSRC별 utterance state
현재 SSRC별 state는 대략 아래 필드를 가진다.

- `in_utterance`
- `last_voice_like_at`
- `utterance_started_at`
- `packets`
- `body_packets`
- `preroll`
- `last_onset_drop_at`
- `consecutive_onset_drops`

여기서 중요한 현재 변경점은:
- **onset 평가는 `body_packets` 기준**으로 한다.
- `first_packet_wait_ms` 도 body first packet 기준으로 계산한다.
- utterance close 시 `preroll.clear()` 한다.
- onset drop 시에도 `preroll.clear()` 한다.
- 직전 onset drop 직후 새 utterance는 preroll snapshot 없이 시작한다.

즉 현재는 예전보다 preroll carry-over를 강하게 줄인 상태다.

### 5-3. process_member_audio() 초반
`process_member_audio()` 초반에는 아래를 한다.

1. `member.bot`, `guild is None` 같은 기본 차단
2. `prepare_stt_audio(pcm_bytes)` 로 16k 오디오 생성
3. **debug audio ingress 저장**
4. 현재 guild TTS가 재생 중이면 `stop_active_tts_playback(... reason="new_user_audio")`
5. room/session/metrics 계산

현재 debug audio 저장 정책은 **ingress-only** 다.
즉 한 턴당 저장 파일은 아래 한 세트만 유지한다.

- `*_raw48k.wav`
- `*_stt16k.wav`
- `*.json`

성공/실패에 따라 stage별 별도 wav/json을 여러 개 만들지 않는다.
메타 JSON만 현재 상태로 덮어쓴다.

### 5-4. active speaker / owner 상태
현재 room 단위로 다음 상태가 있다.

- `room_owner_user_ids`
- `room_owner_until`
- `room_reply_in_progress`
- `room_recent_speaker_stats`

추가 함수:
- `update_room_speaker_activity()`
- `pick_active_speaker()`
- `room_state_snapshot()` 에 `active_speaker_user_id` 포함

현재 규칙:
- owner가 살아 있고 최근 0.5초 안에 말했으면 owner를 active speaker로 유지한다.
- 아니면 wake priority, voiced_ms, rms, last_packet_at 순으로 active speaker를 고른다.
- `should_reply_to_voice()` 는 active speaker가 아닌 사용자면 wake 없이 통과시키지 않는다.

즉 현재는 단순 wake-only가 아니라 **room owner + active speaker 정책**이 같이 작동한다.

### 5-5. 현재 음성 전처리와 차단
기본 경로는 `STT_USE_RAW_48K=false` 이므로 16k 경로다.

전처리 후 현재 아래를 본다.

1. 전체 raw 길이
   - 너무 짧으면 차단
2. VAD / waveform stats
3. unstable audio 여부
4. wake probe
5. full STT
6. short transcription ignore
7. final full text veto / reply gate

### 5-6. wake probe 현재 구조
현재 wake probe는 다음 순서다.

1. `detect_wake_word_sync()` 실행
2. `wake_probe_text`, `wake_confirm_text`, `wake_detected`, `wake_match_mode`, `wake_alias`, `wake_reject_reason` 획득
3. strict confirm required면 unstable audio에서 exact 외를 reject 가능
4. hard drop reason 검사 전 **근접 오타 완화** 수행

현재 근접 오타 완화는:
- `fuzzy_leading_wake_alias(wake_probe)` 또는 `fuzzy_leading_wake_alias(wake_confirm)` 가 있으면
- `wake_detected=True`
- `wake_match_mode="fuzzy"`
- `wake_alias=<wake>`
로 승격한다.

`fuzzy_leading_wake_alias()` 는 현재:
- leading token similarity `>= 0.72`
- 또는 compact 전체 similarity `>= 0.78`
이면 근접 wake로 본다.

즉 현재는 `이불린`, `비블린` 류를 exact miss로 바로 죽이지 않도록 한 상태다.

### 5-7. full STT 현재 구조
현재 full STT는 다음 순서다.

1. partial transcript 추출
   - 최근 1.2초 + overlap 0.3초 window 사용
2. `session_partial_stt_text`, `session_committed_stt_text` 갱신
3. full STT 실행
4. 후처리
5. committed transcript 갱신
6. short noise 판단
7. final wake/full-text/reply gate

즉 현재는 **partial / committed / full-final** 상태를 분리해 추적한다.

### 5-8. onset gating 현재 구조
`evelyn_voice/client.py` 에서 onset 관련 핵심 신호는 아래다.

- `onset_packet_ok`
- `onset_clean_run_max`
- `segment_started_with_concealment`
- `segment_first_clean_decode_ms`
- `first_packet_wait_ms`
- `onset_robotic`
- `onset_artifact_score`
- `onset_vad_prob`
- `onset_rms`
- `stale_penalty_active`
- `robotic_probe_candidate`

현재 중요한 변경점:
- `stale_penalty_active = stale_onset and not (onset_packet_ok and first_clean_window_ok)`
- 즉 clean window와 packet health가 좋으면 stale penalty를 일부 완화한다.

현재 `robotic_probe_candidate` 는 다음 쪽에서 살아난다.
- concealment 없음
- `onset_packet_ok`
- `first_clean_window_ok`
- stale이 아니거나 stale penalty가 비활성
- `len(body_packets) >= 12`
- robotic 이더라도
  - `vad_prob >= 0.16`
  - 또는 `len(body_packets) >= 12`
  - 또는 `total_payload >= 8000`

즉 현재는 clean non-concealed owner-followup 계열 robotic onset을 예전보다 더 쉽게 STT probe로 통과시킨다.

### 5-9. 짧은 조각 처리
현재 `main.py` 에는 `is_short_followup_candidate()` 가 있다.

조건:
- owner follow-up active
- wake 아님
- 텍스트가 짧음
- audio_sec가 짧음

이 경우 `should_ignore_short_transcription()` 에 걸려도 완전 noise drop으로만 보지 않고,
현재는 메타에 `short_followup_candidate=True` 를 남기고 debug 저장에도 표시한다.

### 5-10. final reply gate
full STT 후에는 아래를 거친다.

- `should_ignore_short_transcription()`
- owner follow-up가 아니면 `extract_leading_wake_alias(text)` 로 final wake 재검사
- wake alias 없으면 `full_text_veto`
- 그 다음 `should_reply_to_voice()`

즉 wake probe를 통과해도 final full text가 wake 없이 시작하면 veto될 수 있다.

---

## 6. 현재 음성 답변 생성 구조

현재 음성 답변은 `ask_llm_and_speak_streaming()` 이 담당한다.

### 6-1. first response / follow-up 분리
현재 구조는 **first_then_followup** 모드다.

1. `build_first_response()`
   - 메인 LLM non-stream
   - 짧은 첫 응답 생성
2. `build_followup_response()`
   - 이미 말한 first_response를 포함한 전용 prompt로 보충 응답 생성
3. 둘을 합쳐 최종 answer 생성
4. 문장 단위 TTS 큐잉

현재 first response 쪽은 더 빠르게 하려고:
- `temperature = 0.0`
- `max_tokens = min(40, VOICE_LLM_MAX_TOKENS)`

follow-up 쪽은:
- `temperature = 0.0`
- `max_tokens = min(64, VOICE_LLM_MAX_TOKENS)`

### 6-2. duplicate follow-up 억제
현재는:
- `normalize_compare_text()`
- `is_duplicate_followup()`
를 써서 first response와 사실상 같은 follow-up이면 버린다.

또한 follow-up prompt 자체도:
- 첫 응답과 겹치지 않는 보충만 말하라
- 첫 문장을 반복하지 마라
- 새 정보 없으면 빈 응답
을 명시한다.

### 6-3. 현재 TTS 재생 구조
`stream_tts_sentences()` 는:
- sentence queue
- prepared queue
- prefetch task
- playback task
를 사용한다.

추가로 guild별 `active_tts_playbacks` 를 추적한다.
현재 새 사용자 음성이 들어오면:
- `stop_active_tts_playback(guild_id, reason="new_user_audio")`
로 기존 TTS를 중단한다.

즉 현재는 **interruption 가능한 TTS playback tracking** 이 들어간 상태다.

---

## 7. 현재 route / cognitive 구조

### 7-1. route
`prepare_llm_messages()` 는 route를 계산한다.
현재 route 후보는:
- `main_direct`
- `sub_hint`
- `sub_wait`

하지만 route는 여전히 대부분 실행 하드 분기보다 메타 판단값에 가깝다.

### 7-2. cognitive_state
현재 cognitive_state는 `update_cognitive_state()` 가 만든다.
출력은 현재 아래 액션을 포함할 수 있다.

- `answer`
- `ask`
- `wait`
- `search_then_answer`

즉 예전 문서보다 현재는 `search_then_answer` 까지 실제 액션 후보에 포함된다.

### 7-3. cognitive action 실제 영향
현재 `policy_response_for_state()` 는 아래 short-circuit를 가진다.

- `ask` -> `question_for_user` 반환
- `wait` -> `응, 계속 말해줘.` 또는 `잠깐, 이어서 말해줘.`
- `search_then_answer` -> `금방 찾아보고 바로 알려줄게.`

즉 cognitive action은 이제 단순 진단이 아니라 일부 경우 **직접 짧은 응답을 결정**한다.

또 `search_then_answer` 면 `schedule_search_followup(..., force=True)` 로 실제 검색 후속작업을 강제 예약한다.

---

## 8. 현재 메모리 구조

메모리 루트는 `BOT_MEMORY_DIR` 또는 기본적으로 `C:\Evelyn\bot_memory` 계열이다.
길드별 디렉터리는 `guild_<id>` 형식이다.

현재 사용 파일:
- `rolling_summary.txt`
- `raw_transcript.jsonl`
- `durable_facts.jsonl`
- `open_questions.jsonl`
- `cognitive_state.json`
- `vault\facts.jsonl`
- `vault\questions.jsonl`
- `vault\raw\YYYY-MM-DD.jsonl`

현재 저장 흐름:
1. 턴 종료 후 raw transcript append
2. `update_long_term_memory()` 비동기 실행
3. `update_cognitive_state()` 비동기 실행

즉 메모리/인지 상태는 답변 후에도 다시 비동기 갱신된다.

Sub/Summary 모델은 여전히 사용자에게 답하지 않고 메모리 관리만 담당한다.

---

## 9. 현재 디버그 / 콘솔 출력 구조

현재 `VOICE_CONSOLE_ONLY_STT_AND_REPLY=true` 일 때 콘솔 허용 prefix는 최소화되어 있다.
현재 병목 판단용으로 남기도록 설정한 것은 아래다.

- `🎤 [...]`
- `💬 [Evelyn]`
- `[VOICE LATENCY]`
- `[VOICE BOTTLENECK]`
- `[FULL STT ENTER]`
- `[STT RESULT][wake]`
- `[STT RESULT][partial]`
- `[STT RESULT][full-final]`

현재 `[TURN TRACE]` 는 `VOICE_CONSOLE_ONLY_STT_AND_REPLY=true` 일 때 출력되지 않는다.

즉 병목 판단용 최소 로그만 남기는 방향으로 현재 정리되어 있다.

---

## 10. 현재 코드 기준 핵심 요약

현재 Evelyn 구조를 아주 짧게 요약하면 아래다.

1. **실행 구조**
   - WT 1창 5탭 구조를 기본 의도로 가진다.

2. **응답 구조**
   - 실제 사용자 답변은 메인 LLM이 만든다.
   - Router는 route/cognitive를 계산한다.
   - Sub는 메모리 갱신용이다.

3. **음성 구조**
   - wake probe -> partial/full STT -> final reply gate -> first response + follow-up -> streaming TTS
   - active speaker / room owner / interruptible TTS가 함께 작동한다.

4. **최근 중요한 변화**
   - active speaker 도입
   - partial/committed STT 도입
   - `search_then_answer` 실제 반영
   - first response / follow-up 분리
   - TTS interrupt 추적
   - preroll carry-over 완화
   - wake near-miss fuzzy 완화
   - robotic short follow-up 완화
   - debug audio ingress-only 단순화
   - 병목용 콘솔 로그 최소화

5. **현재 여전히 중요한 사실**
   - 최종 자연어 생성은 여전히 메인 LLM 중심이다.
   - route는 일부 행동에 영향이 생겼지만, 전체 실행 분기를 완전히 갈라놓는 구조는 아니다.
   - 음성 품질 문제는 지금도 wake / onset gating / LLM latency 조정이 핵심 튜닝 포인트다.
