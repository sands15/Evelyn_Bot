# main.py 분리 목표

Last reviewed: 2026-07-15

## 2026-07-15 분해 재개

- 기준 브랜치 `stabilization/evaluation-findings-2026-07-15`의 검증 완료 커밋 `8ec01e3`에서
  `refactor/main-py-decomposition-2026-07-15` 브랜치를 만들었다.
- 감사에서 가장 큰 단일 hot path로 지적된 `_process_member_audio_impl`의 첫 단계를 분리했다.
  - 새 모듈: `evelyn_core/runtime/evelyn_core/voice_audio_ingress_runtime.py`
  - 새 계약: `VoiceAudioIngressDeps`, `VoiceAudioIngressResult`
  - 이동 책임: member/guild 검증, ingress raw debug 저장, turn metrics 생성, reply 중 다른 화자 차단,
    STT 입력 리샘플링, 최소 길이 판정, transport/tail fragment 판정, waveform 통계, VAD override/drop.
  - `main.py`는 live 설정/콜백을 조립하는 `build_voice_audio_ingress_runtime_deps()`와
    `prepare_voice_audio_ingress_from_runtime(...)` 호출만 유지한다.
- 정확한 크기 변화:
  - `main.py`: 8,793줄 → 8,653줄
  - `_process_member_audio_impl`: 729줄 → 548줄
- 검증:
  - 새 경계 단위 테스트 8개 통과
  - `tests/voice` 237개 통과
  - `tests/discord_io` 78개 통과
  - 실제 `main.py` 프로세스 smoke를 포함한 전체 unittest 958개 통과, 실패/오류/건너뜀 0
  - `PYTHONWARNINGS=error::ResourceWarning`, `py_compile`, `git diff --check` 통과
- 런타임/컨테이너 재시작과 외부 push는 하지 않았다.
- 다음 절개 경계는 같은 함수의 wake probe/환경음 판정, TTS interrupt, full STT/transcript 확정 순서다.

- [22:59 KST] cron checkpoint: `build_guild_runtime_reset_deps` 빌더 본문을
  `evelyn_core/runtime/evelyn_core/guild_runtime_reset.py`로 이전.
  - 변경 파일: `main.py`, `evelyn_core/runtime/evelyn_core/guild_runtime_reset.py`,
    `tests/core/test_guild_runtime_reset.py`.
  - `main.py`는 thin builder wrapper만 유지.
  - 런타임/봇 재시작 없음.

- [23:09 KST] `should_ignore_short_transcription`/`is_short_followup_candidate`의 오디오 길이 계산을 main.py 래퍼에서 분리해
  `evelyn_core/runtime/evelyn_core/discord_session_policy_runtime.py`로 위임.
  - 변경 파일: `main.py`, `evelyn_core/runtime/evelyn_core/discord_session_policy_runtime.py`, `docs/MAIN_PY_DECOMPOSITION_TARGET_KR.md`.
  - `build_discord_session_policy_runtime_deps()`에 `audio_duration_fn` DI 주입 추가 및 런타임 함수에서 `pcm_bytes` 기반 오디오 초 계산 처리.
  - 검증: `py_compile(main.py, evelyn_core/runtime/evelyn_core/discord_session_policy_runtime.py)`,
    `pytest tests/discord_io/test_discord_session_policy_runtime.py`.
  - 런타임/봇 재시작 없음.

- [23:19 KST] `build_discord_settings_runtime_deps` 생성 책임을
  `evelyn_core/runtime/evelyn_core/discord_settings_runtime.py`로 이동.
  - 변경 파일: `main.py`, `evelyn_core/runtime/evelyn_core/discord_settings_runtime.py`,
    `tests/discord_io/test_discord_settings_runtime.py`, `docs/MAIN_PY_DECOMPOSITION_TARGET_KR.md`.
  - `main.py`는 `build_discord_settings_runtime_deps_from_main`를 thin wrapper로 유지하고 `_payload` 별칭 import를 제거해 런타임 의존성 빌더의 책임 분리.
  - 검증: `py_compile(main.py evelyn_core/runtime/evelyn_core/discord_settings_runtime.py tests/discord_io/test_discord_settings_runtime.py)`,
    `pytest tests/discord_io/test_discord_settings_runtime.py`, `git diff --check`.
  - 런타임/봇 재시작 없음.

- [23:29 KST] `build_voice_main_llm_streaming_deps` 본문을
  `evelyn_core/runtime/evelyn_core/voice_route_execution.py`로 이동.
  - 변경 파일: `main.py`, `evelyn_core/runtime/evelyn_core/voice_route_execution.py`, `docs/MAIN_PY_DECOMPOSITION_TARGET_KR.md`.
  - `main.py`는 `build_voice_main_llm_streaming_deps_from_runtime(...)`를 호출하는 thin wrapper로 유지.
  - 검증: `py_compile main.py evelyn_core/runtime/evelyn_core/voice_route_execution.py`,
    `pytest tests/voice/test_voice_turn_orchestrator.py`, `git diff --check`.
  - 런타임/봇 재시작 없음.

- [23:46 KST] `context_policy_for_fast_path_policy`의 의존성 경계를
  `evelyn_core/runtime/evelyn_core/fast_path_policy.py`로 정리.
  - 변경 파일: `main.py`, `evelyn_core/runtime/evelyn_core/fast_path_policy.py`,
    `tests/core/test_fast_path_policy_runtime.py`, `docs/MAIN_PY_DECOMPOSITION_TARGET_KR.md`.
  - `main.py`는 wrapper에서 `build_fast_path_policy_runtime_deps()`를 주입해 runtime API 호출만 수행.
  - runtime 함수는 `clean_text` 직접 전달에서 `deps.clean_text` 기반으로 위임 경로 일원화.
  - 검증: `py_compile main.py evelyn_core/runtime/evelyn_core/fast_path_policy.py tests/core/test_fast_path_policy_runtime.py`,
    `pytest tests/core/test_fast_path_policy_runtime.py`, `git diff --check`.
  - 런타임/봇 재시작 없음.

- [23:48 KST] `build_control_page_status_reply` 본문을
  `evelyn_core/runtime/evelyn_core/control_page_status_runtime.py`로 이전.
  - 변경 파일: `main.py`, `evelyn_core/runtime/evelyn_core/control_page_status_runtime.py`,
    `tests/ui/test_control_page_status_runtime.py`, `docs/MAIN_PY_DECOMPOSITION_TARGET_KR.md`.
  - `main.py`는 `build_control_page_status_reply`에서 `build_control_page_status_reply_from_runtime`만 호출.
  - 런타임 API는 `get_control_page_minecraft_snapshot` 주입된 DI로 snapshot 조회 후
    `build_control_page_status_text_from_runtime` 위임.
  - 검증: `py_compile main.py evelyn_core/runtime/evelyn_core/control_page_status_runtime.py tests/ui/test_control_page_status_runtime.py`,
    `pytest tests/ui/test_control_page_status_runtime.py`.
  - 런타임/봇 재시작 없음.

## 2026-06-29 지속 작업 (19:39 KST)

- 22:39 KST continuation checkpoint: `resolve_stt_torch_dtype`, `normalize_stt_language` 본문을 main.py에서 제거하고 STT 본문에서 runtime API를 직접 호출.
  - 변경 파일: `main.py`, `evelyn_core/runtime/evelyn_core/stt_model_runtime.py`.
  - `transcribe_audio16k_sync`의 `STT_FORCE_LANGUAGE` 분기에서 `normalize_stt_language_from_runtime(None, default_language=STT_LANGUAGE)` 호출로 변경.
  - 검증: `py_compile main.py`, `py_compile tests/voice/test_stt_model_runtime.py`, `pytest tests/voice/test_stt_model_runtime.py`.
  - 런타임/봇 재시작 없음.
  - `main.py` 라인 수: 8788.

- 22:49 KST continuation checkpoint: `response_output_policy` 관련 thin wrapper 정리 확대.
  - 이동 후보: `sanitize_model_output`, `extract_answer_from_reasoning` 본문을 `evelyn_core/runtime/evelyn_core/response_output_policy.py` 쪽으로 위임 (`sanitize_model_output_from_runtime`, `extract_answer_from_reasoning_from_runtime` 추가).
  - `main.py`는 `build_response_output_policy_runtime_deps()`에서 `MAIN_LLM_STOP_TOKENS`와 `cleanup_assistant_display_artifacts`를 주입한 뒤 런타임 API를 호출하도록 정리.
  - `ResponseOutputPolicyRuntimeDeps`에 `model_output_stop_tokens`, `sanitize_model_output_cleanup_fn` 필드 추가.
  - `tests/core/test_response_output_policy.py`에 런타임 경로 주입 검증 케이스 추가.
  - 검증: `py_compile main.py evelyn_core/runtime/evelyn_core/response_output_policy.py`, `pytest tests/core/test_response_output_policy.py`, `git diff --check`.
  - 런타임/봇 재시작 없음.

- 22:30 KST continuation checkpoint: session key thin-wrapper 블록(`runtime_session_key`, `make_text_session_key`, `make_text_reply_slot_key`,
  `make_voice_room_session_key`, `make_voice_session_key`, `make_room_memory_key`,
  `make_person_memory_key`, `make_session_memory_key`)을 `evelyn_core/runtime/evelyn_core/session_key_runtime.py`로 이전.
  - `evelyn_core/session_key_runtime.py`에 main 호출용 디폴트 의존성 빌드 경로와 wrapper를 추가(`runtime_session_key`, `make_*` 계열).
  - `main.py`에서는 해당 wrapper 본문만 제거하여 thin wrapper 직접 호출 경로로 정리.
  - 변경 파일: `main.py`, `evelyn_core/runtime/evelyn_core/session_key_runtime.py`, `docs/MAIN_PY_DECOMPOSITION_TARGET_KR.md`.
  - 검증: `py_compile main.py evelyn_core/runtime/evelyn_core/session_key_runtime.py`, `pytest tests/core/test_session_key_runtime.py`.
  - 런타임/봇 재시작 없음.

- 22:19 KST continuation checkpoint: `build_main_response_guidance` 본문을 `evelyn_core/runtime/evelyn_core/voice_response_runtime.py`로 분리.
  - 변경 파일: `main.py`, `evelyn_core/runtime/evelyn_core/voice_response_runtime.py`, `tests/voice/test_voice_response_runtime.py`, `docs/MAIN_PY_DECOMPOSITION_TARGET_KR.md`.
  - `main.py`는 thin wrapper만 유지하며 `build_main_response_guidance_runtime_deps()`를 통해 runtime 의존성 주입 후 `build_main_response_guidance_from_runtime(...)` 호출.
  - 검증: `py_compile main.py evelyn_core/runtime/evelyn_core/voice_response_runtime.py tests/voice/test_voice_response_runtime.py`,
    `pytest tests/voice/test_voice_response_runtime.py` 대상 실행, `git diff --check`.
  - 런타임/봇 재시작 없음.

- 22:09:18 KST continuation checkpoint: `detect_wake_word_sync` 본문을 `evelyn_core/runtime/evelyn_core/stt_text_runtime.py`로 이전하고 `main.py`는 thin wrapper만 유지.
  - `main.py`: `detect_wake_word_sync` 래퍼 주입 경로 정리, `stt_text_runtime.detect_wake_word_sync_from_runtime` 호출로 위임.
  - `evelyn_core/runtime/evelyn_core/stt_text_runtime.py`: `detect_wake_word_sync_from_runtime` 신규 추가.
  - `tests/voice/test_stt_text_runtime.py`: `detect_wake_word_sync_from_runtime` 핵심 분기 2건 추가 (`exact`/`probe_miss`).
  - 검증: `py_compile main.py C:\Evelyn\evelyn_core\runtime\evelyn_core\stt_text_runtime.py tests/voice/test_stt_text_runtime.py` 통과, `pytest tests/voice/test_stt_text_runtime.py`는 현재 환경의 `numpy` 미설치로 BLOCKED, `git diff --check` 통과.
  - 런타임/봇 재시작 없음.

- 21:58 KST continuation checkpoint: `main.py`의 `extract_json_object` 본문과 사용 빈도가 낮은 4개 래퍼를 런타임 쪽으로 정리.
  - `evelyn_core/runtime/evelyn_core/response_output_policy.py`에 `extract_json_object` 본체와 `extract_json_object_from_runtime`을 추가하고,
    `main.py`는 `extract_json_object_from_runtime` 호출만 남김.
  - `main.py`에서 실제 동작 본문이 없는 래퍼 4개(`cleanup_assistant_display_artifacts`, `user_explicitly_mentions_minecraft`,
    `answer_contains_minecraft_leak`, `answer_simple_local_chat_query`)와 `normalize_search_key` wrapper를 삭제해 thin-wrapper 중심으로 정리.
  - 변경 파일: `main.py`, `evelyn_core/runtime/evelyn_core/response_output_policy.py`, `docs/MAIN_PY_DECOMPOSITION_TARGET_KR.md`.
  - `main.py` 라인 수: 7949.
  - 검증: `py_compile main.py evelyn_core/runtime/evelyn_core/response_output_policy.py`,
    `pytest tests/core/test_response_output_policy.py`, `git diff --check`.
  - 런타임/봇 재시작 없음.

- 21:49 KST continuation checkpoint: `build_voice_timing_runtime_deps` 빌더를 `evelyn_core/runtime/evelyn_core/voice_timing_runtime.py`로 이동해 런타임 DI 빌더 책임 분리.
  - `main.py`는 `VoiceTimingRuntimeDeps` 인스턴스 생성 본문을 제거하고
    `build_voice_timing_runtime_deps(...)` 호출부를 런타임 helper로 위임.
  - 변경 파일: `main.py`, `evelyn_core/runtime/evelyn_core/voice_timing_runtime.py`, `docs/MAIN_PY_DECOMPOSITION_TARGET_KR.md`.
  - `main.py` 라인 수: 7979.
  - 검증: `py_compile(main.py, evelyn_core/runtime/evelyn_core/voice_timing_runtime.py)`,
    `pytest tests/voice/test_voice_timing_runtime.py` (4 passed), `git diff --check`.
  - 런타임/봇 재시작 없음.

- 21:29 KST continuation checkpoint: `main.py`의 `is_casual_call_or_status_question` thin wrapper를 제거하고 `evelyn_core/runtime/evelyn_core/session_memory_state.py`의
  `is_casual_call_or_status_question`을 DI 경로로 직접 주입하도록 정리.
  - 변경 파일: `main.py`.
  - 적용 대상: `build_voice_response_runtime_deps()`에서 `is_casual_call_or_status_question` 주입 대상 대체.
  - 검증: `py_compile(main.py)`, `pytest tests/core/test_session_memory_state.py -k casual`.
  - 런타임/봇 재시작 없음.

- 21:39 KST continuation checkpoint: `response_output_policy` 출력 조립/마스킹 계열 함수를 thin wrapper DI 경로로 정리.
  - `main.py`는 `should_label_question_response`, `fallback_for_unrequested_minecraft_leak`,
    `sanitize_unrequested_minecraft_leak`, `format_display_text` 위임을 런타임 deps로 전환.
  - 런타임 모듈 `evelyn_core/runtime/evelyn_core/response_output_policy.py`에
    `ResponseOutputPolicyRuntimeDeps`, `should_label_question_response_from_runtime`,
    `fallback_for_unrequested_minecraft_leak_from_runtime`,
    `sanitize_unrequested_minecraft_leak_from_runtime`, `format_display_text_from_runtime` 추가.
  - 변경 파일: `main.py`, `evelyn_core/runtime/evelyn_core/response_output_policy.py`.
  - 검증 예정: `py_compile(main.py, evelyn_core/runtime/evelyn_core/response_output_policy.py)`,
    `pytest tests/core/test_response_output_policy.py`.
  - 런타임/봇 재시작 없음.

- 21:19 KST continuation checkpoint: `main.py`의 음성 짧은 발화 판정 헬퍼(`should_ignore_short_transcription`, `is_short_followup_candidate`)를 `evelyn_core/runtime/evelyn_core/discord_session_policy_runtime.py`로 위임.
  - 새 `DiscordSessionPolicyRuntimeDeps` 주입 항목 추가: `normalize_voice_text`, `normalized_wake_words`, `min_audio_sec`, `min_transcribed_len`, `wake_short_text_keep_len`.
  - `main.py`에서 thin wrapper만 유지 (`build_discord_session_policy_runtime_deps()` 통해 주입 전달).
  - 변경 파일: `main.py`, `evelyn_core/runtime/evelyn_core/discord_session_policy_runtime.py`, `tests/discord_io/test_discord_session_policy_runtime.py`.
  - 검증: `py_compile(main.py, evelyn_core/runtime/evelyn_core/discord_session_policy_runtime.py)`, `pytest tests/discord_io/test_discord_session_policy_runtime.py`.
  - 런타임/봇 재시작 없음.

- 20:32 KST continuation checkpoint: vision 헬퍼를 `main.py`에서 `evelyn_core/runtime/evelyn_core/vision_runtime.py`로 분리.
  - 이동 항목: `build_vision_observation_prompt`, `build_vision_watch_prompt`, `format_vision_observation`, `vision_watch_scene_looks_bad`.
  - `main.py`는 `build_vision_watch_runtime_deps()` + thin wrapper만 유지.
  - 새 런타임 API: `build_vision_observation_prompt_from_runtime`, `build_vision_watch_prompt_from_runtime`,
    `format_vision_observation_from_runtime`, `vision_watch_scene_looks_bad_from_runtime`.
  - 변경 파일: `main.py`, `evelyn_core/runtime/evelyn_core/vision_runtime.py`, `tests/vision/test_vision_watch_runtime.py`.
  - 검증: `py_compile main.py`, `py_compile tests/vision/test_vision_watch_runtime.py`, `pytest tests/vision/test_vision_watch_runtime.py`.
  - 런타임/봇 재시작 없음.

- 20:42 KST continuation checkpoint: `main.py`에서 Evelyn 페이지 URL 런타임 빌더를 `evelyn_core/runtime/evelyn_core/page_urls.py`로 이전.
  - 이동 항목: `main.py`의 `build_evelyn_page_url_runtime_deps`(빌더)와 `EvelynPageUrlRuntimeDeps` 인스턴스화 책임.
  - `main.py`의 `resolve_evelyn_page_url`는 런타임 빌더(`build_evelyn_page_url_runtime_deps`)를 호출해 DI 기반으로 위임.
  - 변경 파일: `main.py`, `evelyn_core/runtime/evelyn_core/page_urls.py`, `tests/ui/test_page_urls.py`.
  - 검증: `py_compile main.py evelyn_core/runtime/evelyn_core/page_urls.py`, `pytest tests/ui/test_page_urls.py`.
  - 런타임/봇 재시작 없음.
  - 남은 범위: 음성 파이프라인 관련 위임 후보( `should_skip_full_stt_after_wake_probe`, `stt` 경로 유틸 보완 등) 지속 검토.

- 20:50 KST continuation checkpoint: `main.py`의 `should_skip_full_stt_after_wake_probe`를 `evelyn_core/runtime/evelyn_core/discord_session_policy_runtime.py`로 위임.
  - 새 런타임 API: `should_skip_full_stt_after_wake_probe_from_runtime`.
  - 변경 파일: `main.py`, `evelyn_core/runtime/evelyn_core/discord_session_policy_runtime.py`, `tests/discord_io/test_discord_session_policy_runtime.py`, `docs/MAIN_PY_DECOMPOSITION_TARGET_KR.md`.
  - `DiscordSessionPolicyRuntimeDeps`에 `clean_text`, `looks_like_brief_filler_text`, `looks_like_repetitive_noise_text`, `no_wake_max_continue_sec` 주입 포인트 추가.
  - `build_discord_session_policy_runtime_deps()`에서 새 의존성 주입 후 wrapper 동작으로 재연결.
  - 검증: `py_compile(main.py, evelyn_core/runtime/evelyn_core/discord_session_policy_runtime.py, tests/discord_io/test_discord_session_policy_runtime.py)`, `pytest tests/discord_io/test_discord_session_policy_runtime.py`.
  - 런타임/봇 재시작 없음.
- 20:59 KST continuation checkpoint: `resolve_command_prefix` 위임 로직을 `evelyn_core/runtime/evelyn_core/discord_settings_runtime.py`로 이전.
  - `main.py`는 `commands.Bot` 생성 시 `resolve_command_prefix_from_runtime(_bot, message, get_guild_command_prefix=...)`를 직접 위임.
  - 변경 파일: `main.py`, `evelyn_core/runtime/evelyn_core/discord_settings_runtime.py`, `docs/MAIN_PY_DECOMPOSITION_TARGET_KR.md`.
  - 검증 대상: `py_compile(main.py, discord_settings_runtime.py)`, `pytest tests/discord_io/test_discord_settings_runtime.py`.
  - 런타임/봇 재시작 없음.

- 21:09 KST continuation checkpoint: `build_search_query` 분해 후보를 `evelyn_core/runtime/evelyn_core/search_followup_runtime.py`로 이전.
  - 이동 대상: `build_search_query` 본문(`get_conversation_history`/`compact_working_summary`/`memory_summary` 사용 구간)과 의존성 연결.
  - `SearchFollowupRuntimeDeps`에 `get_conversation_history`, `memory_summary_path`, `read_text_file`, `compact_working_summary` 필드 추가.
  - `main.py`는 `build_search_query_from_runtime`을 호출하는 thin wrapper만 유지.
  - 새 테스트 파일 추가: `tests/core/test_search_followup_runtime.py`.
  - 검증: `py_compile(main.py, evelyn_core/runtime/evelyn_core/search_followup_runtime.py, tests/core/test_search_followup_runtime.py)`, `pytest tests/core/test_search_followup_runtime.py`.
  - 런타임/봇 재시작 없음.

  - 이동 방식: `main.py`의 빌더 본문을 runtime 모듈로 이전하고, `get_stt_model`에서 DI 빌더 + `get_stt_model_from_runtime`만 호출.
  - 변경 파일: `main.py`, `evelyn_core/runtime/evelyn_core/stt_model_runtime.py`, `tests/voice/test_stt_model_runtime.py`, `docs/MAIN_PY_DECOMPOSITION_TARGET_KR.md`.
  - 검증: `py_compile main.py evelyn_core/runtime/evelyn_core/stt_model_runtime.py tests/voice/test_stt_model_runtime.py`, `pytest tests/voice/test_stt_model_runtime.py`, `git diff --check`.
  - 런타임/봇 재시작 없음.

- 20:09 KST continuation checkpoint: `main.py`의 인스턴스 락 래퍼(`build_instance_lock_runtime_deps`, `acquire_instance_lock`, `release_instance_lock`)를 `evelyn_core/runtime/evelyn_core/instance_lock_runtime.py`로 이동해 thin wrapper 형태로 정리.
  - 새 런타임 API: `build_instance_lock_runtime_deps`, `acquire_instance_lock_from_main`, `release_instance_lock_from_main`.
  - `main.py`는 `acquire_instance_lock`, `release_instance_lock`만 유지하고 실제 의존성 구성/락 획득/해제 위임을 runtime 모듈로 이전.
  - 변경 파일: `main.py`, `evelyn_core/runtime/evelyn_core/instance_lock_runtime.py`, `docs/MAIN_PY_DECOMPOSITION_TARGET_KR.md`.
  - 검증: `py_compile main.py evelyn_core/runtime/evelyn_core/instance_lock_runtime.py`, `pytest tests/core/test_instance_lock_runtime.py`, `git diff --check`.
  - 런타임/봇 재시작 없음.
  - 남은 범위: 음성 파이프라인 래퍼 정리, 필요 시 control page 라우팅 미세 정리.

- `main.py`의 `should_require_confirm_exact_for_wake`, `is_transport_corrupted_audio`, `is_tail_fragment_candidate`를 `evelyn_core/runtime/evelyn_core/discord_session_policy_runtime.py`로 분리.
  - 새 런타임 객체: `DiscordSessionPolicyRuntimeDeps`.
  - 새 위임 API: `should_require_confirm_exact_for_wake_from_runtime`, `is_transport_corrupted_audio_from_runtime`, `is_tail_fragment_candidate_from_runtime`.
  - `main.py`는 `build_discord_session_policy_runtime_deps()` 추가 후 thin wrapper 3개만 유지.
  - 변경 파일: `main.py`, `evelyn_core/runtime/evelyn_core/discord_session_policy_runtime.py`, `tests/discord_io/test_discord_session_policy_runtime.py`.
  - 검증: `py_compile main.py evelyn_core/runtime/evelyn_core/discord_session_policy_runtime.py tests/discord_io/test_discord_session_policy_runtime.py`, `pytest tests/discord_io/test_discord_session_policy_runtime.py`.
  - 런타임/봇 재시작 없음.

- 19:59 KST continuation checkpoint: `main.py`에서 control page 런타임 서비스 프로브 로직 1개(`_probe_control_page_runtime_services_once`)를 `evelyn_core/runtime/evelyn_core/control_page_runtime_services_runtime.py`로 이동.
  - 새 런타임 객체: `ControlPageRuntimeServicesProbeDeps`.
  - 새 위임 API: `build_control_page_runtime_services_probe_runtime_deps`, `probe_control_page_runtime_services_once_from_runtime`.
  - `main.py`는 `build_control_page_runtime_services_runtime_deps()`에서 프로브 래퍼 제거 후 DI 빌더에 thin 래퍼 함수만 주입.
  - 변경 파일: `main.py`, `evelyn_core/runtime/evelyn_core/control_page_runtime_services_runtime.py`, `tests/ui/test_control_page_runtime_services_runtime.py`, `docs/MAIN_PY_DECOMPOSITION_TARGET_KR.md`.
  - 검증: `py_compile main.py evelyn_core/runtime/evelyn_core/control_page_runtime_services_runtime.py tests/ui/test_control_page_runtime_services_runtime.py`, `pytest tests/ui/test_control_page_runtime_services_runtime.py`.
  - 런타임/봇 재시작 없음.

## 2026-06-29 진행 노트

- 19:32 KST continuation checkpoint: `main.py`의 빠른 경로/라우팅 헬퍼군(`is_control_page_source`, `deep_route_marker_count`, `has_negated_search_marker`, `needs_search_or_deep_routing`, `is_simple_directive`, `is_obvious_continue`, `fast_path_policy`, `context_policy_for_fast_path_policy`)을 `evelyn_core/runtime/evelyn_core/fast_path_policy.py`로 분리.
  - 새 runtime 객체: `FastPathPolicyRuntimeDeps`.
  - 새 위임 API: `is_control_page_source_from_runtime`, `deep_route_marker_count_from_runtime`, `has_negated_search_marker_from_runtime`, `needs_search_or_deep_routing_from_runtime`, `is_simple_directive_from_runtime`, `is_obvious_continue_from_runtime`, `fast_path_policy_from_runtime`, `context_policy_for_fast_path_policy_from_runtime`.
  - `main.py`는 `build_fast_path_policy_runtime_deps()` + thin wrapper로 유지.
  - 변경 파일: `main.py`, `evelyn_core/runtime/evelyn_core/fast_path_policy.py`, `tests/core/test_fast_path_policy_runtime.py`.
  - 검증: `py_compile main.py evelyn_core/runtime/evelyn_core/fast_path_policy.py tests/core/test_fast_path_policy_runtime.py`, `pytest tests/core/test_fast_path_policy_runtime.py`, `git diff --check`.
  - `main.py` 라인 수: 9052.
  - 런타임/봇 재시작 안 함.

- 19:10 KST continuation checkpoint: `main.py`의 세션 키/세션 식별자 유틸(`runtime_session_key`, `make_text_session_key`, `make_text_reply_slot_key`, `make_voice_room_session_key`, `make_voice_session_key`, `make_room_memory_key`, `make_person_memory_key`, `make_session_memory_key`)을 `evelyn_core/runtime/evelyn_core/session_key_runtime.py`로 분리.
  - 새 런타임 객체: `SessionKeyRuntimeDeps`.
  - 새 위임 함수: `runtime_session_key_from_runtime`, `make_text_session_key_from_runtime`, `make_text_reply_slot_key_from_runtime`, `make_voice_room_session_key_from_runtime`, `make_voice_session_key_from_runtime`, `make_room_memory_key_from_runtime`, `make_person_memory_key_from_runtime`, `make_session_memory_key_from_runtime`.
  - `main.py`는 `build_session_key_runtime_deps()` + thin wrapper 7개로 유지, 실질 로직은 런타임 모듈로 이동.
  - 변경 파일: `main.py`, `evelyn_core/runtime/evelyn_core/session_key_runtime.py`, `tests/core/test_session_key_runtime.py`.
  - 검증: `py_compile main.py evelyn_core/runtime/evelyn_core/session_key_runtime.py tests/core/test_session_key_runtime.py`, `pytest tests/core/test_session_key_runtime.py`.
  - 런타임/봇 재시작 없음.

- 19:20 KST continuation checkpoint: `main.py`에서 `execute_voice_delivery_plan`을 `evelyn_core/runtime/evelyn_core/voice_delivery_runtime.py`로 옮겨 thin wrapper만 남김.
  - 새 런타임 함수: `execute_voice_delivery_plan_from_runtime(vc, delivery_plan, ..., deps=...)`.
  - `main.py`의 `execute_voice_delivery_plan`은 `build_voice_delivery_runtime_deps()`를 통해 위임.
  - 변경 파일: `main.py`, `evelyn_core/runtime/evelyn_core/voice_delivery_runtime.py`.
  - 검증: `py_compile main.py evelyn_core/runtime/evelyn_core/voice_delivery_runtime.py`, `py_compile tests/voice/test_voice_delivery_runtime.py` (간접 경로), `pytest tests/voice/test_voice_delivery_runtime.py`.
  - 런타임/봇 재시작 없음.

- 19:00 KST continuation checkpoint: `main.py`의 Control Page Minecraft 스냅샷 안전 조회(`safe_get_control_page_minecraft_snapshot`)를 `evelyn_core/runtime/evelyn_core/control_page_minecraft_snapshot_runtime.py`로 이동.
  - 새 런타임 위임 함수: `safe_get_control_page_minecraft_snapshot_from_runtime`.
  - `main.py`는 thin wrapper만 유지해 `build_control_page_minecraft_snapshot_runtime_deps()`로 전달한 디펜던시로 위임.
  - 추가 테스트: `tests/ui/test_control_page_minecraft_snapshot_runtime.py`(`safe_get` 실패/성공 케이스 추가).
  - 검증: `py_compile main.py evelyn_core/runtime/evelyn_core/control_page_minecraft_snapshot_runtime.py tests/ui/test_control_page_minecraft_snapshot_runtime.py`, `pytest tests/ui/test_control_page_minecraft_snapshot_runtime.py`.
  - 런타임/봇 재시작 없음.

- 18:50 KST continuation checkpoint: `main.py`의 Control Page 상태/인벤토리/자율 행동 reply 래퍼 3개(`build_control_page_inventory_reply`, `build_control_page_minecraft_reply`, `build_control_page_autonomy_reply`)를 `evelyn_core/runtime/evelyn_core/control_page_status_runtime.py`로 이동.
  - 이동 대상 `from_runtime` 시그니처: `build_control_page_inventory_reply_from_runtime`, `build_control_page_minecraft_reply_from_runtime`, `build_control_page_autonomy_reply_from_runtime`.
  - `main.py`는 `ControlPageStatusRuntimeDeps`에 런타임 `safe_get_control_page_minecraft_snapshot`, 자율 엔진/라우터 조회 함수, 페이로드 생성기를 주입해 thin wrapper만 유지.
  - 변경/추가 테스트: `tests/ui/test_control_page_status_runtime.py`.
  - 검증: `py_compile main.py evelyn_core/runtime/evelyn_core/control_page_status_runtime.py tests/ui/test_control_page_status_runtime.py`, `pytest tests/ui/test_control_page_status_runtime.py`.
  - 런타임/봇 재시작 없음.

- 18:40 KST continuation checkpoint: `main.py`의 질문 정책 위임 구간(`question_cooldown_hit`, `apply_fast_path_question_policy`, `record_question_trace`, `summarize_question_metrics`, `proactive_question_scope_candidates`, `record_session_question_asked`, `resolve_pending_proactive_question_for_turn`, `select_and_mark_proactive_question`, `maybe_append_proactive_question`)을 `evelyn_core/runtime/evelyn_core/question_policy_runtime.py`로 분리.
  - 새 런타임 객체: `QuestionPolicyStateRuntimeDeps`.
  - 새 위임 함수: 각 대상 함수의 `*_from_runtime` 시리즈.
  - `main.py`는 `build_question_policy_state_runtime_deps()` + thin wrapper만 유지.
  - 변경/추가 테스트: `tests/core/test_question_policy_runtime.py`.
  - 검증: `py_compile main.py`, `pytest tests/core/test_question_policy_runtime.py`.
  - 런타임/봇 재시작 없음.

- 18:29 KST continuation checkpoint: `main.py`의 STT 모델 초기화 유틸(`resolve_stt_torch_dtype`, `normalize_stt_language`, `get_stt_model`)를 `evelyn_core/runtime/evelyn_core/stt_model_runtime.py`로 분리해 thin-wrapper 패턴으로 전환.
  - 새 런타임 객체: `SttModelRuntimeDeps`.
  - 새 위임 함수: `resolve_stt_torch_dtype_from_runtime`, `normalize_stt_language_from_runtime`, `get_stt_model_from_runtime`.
  - `main.py`는 `build_stt_model_runtime_deps()` + thin wrapper만 보유.
  - 추가 테스트: `tests/voice/test_stt_model_runtime.py`.
  - 검증: `py_compile main.py evelyn_core/runtime/evelyn_core/stt_model_runtime.py tests/voice/test_stt_model_runtime.py`, `pytest tests/voice/test_stt_model_runtime.py`.
  - `main.py` 라인 수: 7991.
  - 런타임/봇 재시작 없음.

- 18:12 KST continuation checkpoint: `main.py`의 로컬 제어 TTS 런타임 디펜던시 빌더(`LocalControlTtsRuntimeDeps` 생성 로직)를 `evelyn_core/runtime/evelyn_core/local_control_tts_runtime.py`로 분리.
  - 새 이동 항목: `build_local_control_tts_runtime_deps`.
  - `main.py`에서 직접 빌더 함수(`build_local_control_tts_runtime_deps`)를 제거하고, `schedule_local_control_tts`는 런타임 빌더만 사용하도록 정리.
  - `main.py` 라인 수: 9057
  - 변경 파일: `main.py`, `evelyn_core/runtime/evelyn_core/local_control_tts_runtime.py`.
  - 검증: `py_compile main.py evelyn_core/runtime/evelyn_core/local_control_tts_runtime.py` 및 `pytest tests/voice/test_local_control_tts_runtime.py`.
  - 런타임/봇 재시작 없음.

- 17:59 KST continuation checkpoint: `main.py`의 STT 텍스트 처리 헬퍼(`build_partial_stt_window`, `longest_common_prefix_text`, `commit_stable_transcript`, `get_partial_transcript`, `score_stt_candidate`, `choose_full_stt_candidate`)를 `evelyn_core/runtime/evelyn_core/stt_text_runtime.py`로 분리.
  - 새 런타임 객체: `SttTextRuntimeDeps`
  - 새 빌더/래퍼: `build_stt_text_runtime_deps`, `build_partial_stt_window_from_runtime`, `longest_common_prefix_text_from_runtime`, `commit_stable_transcript_from_runtime`, `get_partial_transcript_from_runtime`, `score_stt_candidate_from_runtime`, `choose_full_stt_candidate_from_runtime`
  - `main.py`는 `_build_stt_text_runtime_deps` + thin wrapper만 보유.
  - 추가 테스트: `tests/voice/test_stt_text_runtime.py`.
  - 검증: `py_compile main.py, tests/voice/test_stt_text_runtime.py, evelyn_core/runtime/evelyn_core/stt_text_runtime.py`, `pytest tests/voice/test_stt_text_runtime.py`
  - 주의: 런타임/봇 재시작 없음.

- 17:49 KST continuation checkpoint: `main.py`의 `should_force_search_followup` 판정 로직을 `evelyn_core/runtime/evelyn_core/cognitive_followup_policy.py`로 분리하고 DI deps 패턴으로 위임 처리.
  - 새 런타임 객체: `ShouldForceSearchFollowupRuntimeDeps`
  - 새 위임 함수: `should_force_search_followup_from_runtime`
  - `main.py`는 `build_cognitive_followup_runtime_deps()` + thin wrapper만 보유.
  - 추가 테스트: `tests/core/test_cognitive_followup_runtime.py`.
  - `main.py` 라인 수: 9144
  - 검증: `py_compile main.py evelyn_core/runtime/evelyn_core/cognitive_followup_policy.py tests/core/test_cognitive_followup_runtime.py`, `pytest tests/core/test_cognitive_followup_runtime.py`(3 passed), `git diff --check`(CRLF 경고만).
  - 주의: 런타임/봇 재시작 없음.

- 17:39 KST continuation checkpoint: `main.py`의 `mark_startup_component`와 `startup_component_done` 래퍼를 `evelyn_core/runtime/evelyn_core/startup_component_state.py`로 이동. `StartupComponentRuntimeDeps`를 통해 상태 매핑/시간 함수를 주입받아 thin wrapper 패턴으로 정리했고, `STARTUP_BOOT_STEPS`도 런타임 모듈 상수로 이동. `main.py`는 `build_startup_component_runtime` 객체를 생성해 위임만 수행.
  - 검증: `py_compile` (`main.py`, `evelyn_core/runtime/evelyn_core/startup_component_state.py`), `pytest tests/core/test_startup_component_state.py` 4 passed, `git diff --check` only CRLF warnings. 런타임/봇 재시작 없음. `main.py` 라인 수 갱신 필요.

- 17:10 KST continuation checkpoint: `main.py` 세션/턴 상태 유틸의 일부를 `evelyn_core/runtime/evelyn_core/session_turn_runtime.py`로 이동(`new_conversation_history`, `build_topic_id`, `new_turn_id`, `current_turn_id`, `next_segment_id`, `start_new_turn`, `begin_user_text_turn`, `finish_assistant_text_turn`, `session_state_snapshot`, `increment/reset_bad_audio`, `update_session_state`, `mark_session_active`, `is_session_active_for_user`, `get_conversation_history`, `trim_history`, `append_history`, `recent_assistant_reply_summary`, `persona_state_hint_for_turn`) 및 `main.py`는 deps 빌더+thin wrapper로 축소.
  - 검증: `py_compile` (`main.py`, `evelyn_core/runtime/evelyn_core/session_turn_runtime.py`) 통과, `pytest tests/core/test_session_memory_state.py` 12 passed, `git diff --check`에서 CRLF 경고만.

- 16:59 KST continuation checkpoint: `should_label_question_response` 로직을 `main.py`에서 `evelyn_core/runtime/evelyn_core/response_output_policy.py`로 분리.
  - 변경 파일: `main.py`, `evelyn_core/runtime/evelyn_core/response_output_policy.py`, `tests/core/test_response_output_policy.py`.
  - `main.py`는 `should_label_question_response_payload` 래퍼로 thin wrapper만 유지.
  - 검증: `py_compile` (main.py + response_output_policy + test 파일), `pytest tests/core/test_response_output_policy.py`.
- 16:39 KST: `main.py`에서 Discord 명령어 prefix/채널 설정 래퍼( `normalize_command_prefix`, `get_guild_command_prefix`, `save_guild_command_prefix`, `get_guild_observe_channel_ids`, `get_guild_command_only_channel_ids`, `save_guild_channel_list`, `add_guild_channel_setting`, `remove_guild_channel_setting` )를 새 runtime 모듈 `evelyn_core/runtime/evelyn_core/discord_settings_runtime.py`로 이동. `main.py`는 `build_discord_settings_runtime_deps`와 thin wrapper 1:1 위임으로 전환.
- 16:52 KST continuation checkpoint: `main.py`에서 바리인 연속성 관련 6개 헬퍼를 `evelyn_core/runtime/evelyn_core/voice_barge_in_continuity.py`로 위임해 thin wrapper 패턴으로 정리.
  - 이동 항목: `_parse_barge_in_reason_label`, `_format_voice_barge_in_continuity_summary`, `_format_voice_barge_in_continuity_detail_lines`, `start_voice_barge_in_continuity_probe`, `_build_voice_barge_in_continuity_snapshot`, `reset_voice_barge_in_continuity_probe`, `_mark_voice_barge_in_continuity_probe`.
  - 변경 파일: `main.py`, `evelyn_core/runtime/evelyn_core/voice_barge_in_continuity.py`, `tests/voice/test_voice_barge_in_continuity.py`(기존 회귀 확인 목적).
  - 검증 대상 변경 범위: 바리인 continuity wrapper 위임 + runtime 모듈.
- 17:09 KST continuation checkpoint: `main.py`의 `open_control_page_path_with_system`/`open_control_page_url_with_system` 중복 OS 오픈 헬퍼 바인딩을 `evelyn_core/control_page_server.py`의 `open_path_with_system`/`open_url_with_system`으로 대체. `main.py`에는 alias만 남기고 동작 본문은 runtime 모듈로 이동. `py_compile main.py`, `pytest tests/ui/test_control_page_runtime_services_runtime.py`(`3 passed`), `git diff --check` 성공. `main.py` 라인 수 8057. runtime/bot 재시작 없음.

## 목표

`main.py`는 이블린의 전체 구현 파일이 아니라 런타임 엔트리포인트와 배선 파일이어야 한다.
최종 목표는 `main.py`를 대략 1,500~2,500줄 이하로 줄이고, 기능별 규칙과 상태를 모듈로 이동하는 것이다.

## 최종 책임 경계

- `main.py`
  - 환경 경로/설정 초기화
  - Discord bot 생성과 이벤트/명령 decorator 등록
  - 주요 모듈 의존성 wiring
  - local-only 또는 Discord runtime 진입

- `runtime_lifecycle.py`
  - 재시작, 종료, 런처 선택, PowerShell stop helper 호출

- `control_page_runtime_health.py`
  - 8799 Control Page와 8798 Bot API 계약 해석
  - `runtime.serviceHealth` 요약과 Control Page용 진단 payload

- `evelyn_core.runtime.evelyn_core.control_page_http`
  - Control Page JSON 응답, no-store 파일/바이너리 응답, CORS, asset path guard, health payload.

- `evelyn_core.runtime.evelyn_core.control_page.*`
  - aiohttp route, Control Page state payload, command catalog, tool registry, UI contracts

- `evelyn_core.runtime.evelyn_core.voice.*`
  - 음성 파이프라인 상태, 바리인 연속성, session/room ownership, ingress, reply flow, debug audio

- `evelyn_core.runtime.evelyn_core.observability.*`
  - turn trace, metric 집계, p95, log event formatting

- `evelyn_core.runtime.evelyn_core.memory.*`
  - session key/history/follow-up target, memory vault bridge

- `evelyn_core.runtime.evelyn_core.minecraft.*`
  - Control Page용 Minecraft/Voyager snapshot cache, autonomy command handling

## 분리 원칙

- `main.py`에는 오래 사는 상태 dict를 두지 않는다.
- `main.py`에는 비즈니스 규칙과 판정 로직을 두지 않는다.
- Discord decorator/event handler는 남겨도 내부는 모듈 함수 호출로 얇게 유지한다.
- Control Page, Voice, Runtime, Minecraft/Voyager는 명확한 payload/contract로 연결한다.
- 기존 문자열 기반 `main.py` 테스트는 점진적으로 모듈 단위 테스트로 옮긴다.
- 한 번에 대규모 이동하지 않고, 작고 검증 가능한 단위로 이동한다.

## 구현 순서

1. `voice_barge_in_continuity.py`
   - 5회 연속 바리인 상태, reason 분류, summary/detail formatting, reset, snapshot, event logging.
2. `control_page.commands` / `control_page.tools`
   - Control Page command catalog, cheap command routing, tool registry, high-risk command guard.
3. `control_page.state`
   - `/api/control-page/state` payload 조립과 boot/status summary.
4. `voice.pipeline_state` / `voice.debug_audio`
   - `voicePipeline` snapshot, counters, debug audio write/trim worker.
5. `memory.session_memory`
   - session key, conversation history, follow-up target, room/person memory key.
6. Discord command handlers thinning
   - command decorator는 유지하되 내부 구현은 모듈 API로 이동.

## 1차 구현 범위

현재 1차 범위는 바리인 연속성 분리다.

- 새 모듈: `evelyn_core/runtime/evelyn_core/voice_barge_in_continuity.py`
- `main.py`에 남는 것:
  - tracker 생성
  - 기존 함수명 호환 wrapper
  - 실제 voice/Control Page 호출부
- 검증:
  - 새 단위 테스트
  - `py_compile`
  - `tests/voice`
  - 관련 `tests/runtime`, `tests/ui`, `tests/core`

## 2026-06-19 진행 상태

완료된 분리:

- `voice_barge_in_continuity.py`
  - 바리인 연속성 tracker, reason/label, snapshot, Control Page 표시 payload.
- `control_page_tools.py`
  - Control Page command catalog, cheap/router tool decision, restart/status 질문 guard, UI action mapping, tool risk policy.
- `control_page_state.py`
  - Control Page local/guild state payload/view builder, runtime/voice/minecraft/boot payload builder, local/guild status text formatter, route query/body parser, chat refresh plan, memory vault open payload/reply helper, Control Page command/tool execution reply formatter, chat log/UI command store, Minecraft snapshot cache store, runtime services cache/payload store, welcome text sanitizer.
  - Control Page chat/shutdown/memory-note route orchestration helpers, memory vault open fallback helpers, Discord-required reply, and shutdown tool fallback policy with live dependencies injected from `main.py`.
- `control_page_state_handler.py`
  - Control Page `/api/control-page/state` live dependency collection and local/guild state orchestration, with live runtime callbacks/config injected from `main.py`.
- `voice_pipeline_state.py`
  - voice pipeline counters, last channel state file, last-channel save failure wrapper, failure state/event logging, Control Page voice pipeline snapshot.
- `voice_debug_audio.py`
  - debug wav 저장, stem 관리, 오래된 파일 정리, debug write worker/queue enqueue orchestration.
- `local_mic_state.py`
  - local mic runtime state, input mode normalization, 상태 문자열.
- `observability_metrics.py`
  - 평균/p95/rate helper, turn ingress metric creation, turn-stage metric mutation, turn drop/rejected-turn metadata recording, turn-path metric summary, voice p95 summary, model-call trace payload/metric recording, context pipeline benchmark JSONL writing, model-call metric replay/summary, question metric summary.
- `question_policy_state.py`
  - 질문 정책 정규화, fast-path 질문 제한, session question cooldown/state, proactive question selection wrapper.
- `route_fallback_policy.py`
  - LLM route fallback 이름 정규화, voice context 강제 조건, main/sub_hint/sub_wait fallback route 판정.
- `tool_awareness_policy.py`
  - Main response guidance용 tool awareness context, marker 기반 search/runtime/minecraft tool shortlist, route availability callback contract.
- `local_tool_diagnostic_context.py`
  - local tool diagnostic 요청 감지, 후보 파일 경로, 진단 snippet line matching/rendering.
- `memory_context_state.py`
  - layered memory context row merge, memory row line formatting, memory-vault recall을 포함한 최종 memory context 조립/rendering.
- `memory_layers.py`
  - guild/room/person/session memory layer I/O 수집.
- `memory_llm_context.py`
  - cognitive/long-term memory LLM prompt 조립, compact retry prompt, layered summary, scope target 계산.
- `memory_update_policy.py`
  - memory summary/writebehind 실행 여부 정책, vision context memory redaction, raw turn row/scope label 조립, raw transcript/vault mirror 기록, memory writer decision payload 조립, writebehind scheduling plan.
- `memory_writeback_state.py`
  - long-term memory LLM writebehind orchestration, context overflow compact retry, summary/facts/questions writeback, vault mirror, proactive question promotion.
- `cognitive_policy_state.py`
  - fast-path cognitive state 생성, ask confidence gating, cognitive action short-circuit response, layered/cached cognitive state lookup, fallback/final state 보정.
- `autonomy_observation_state.py`
  - 자율 엔진 기본 observation payload 계산, 최근 user text 선택, cognitive refresh 필요성/vision/local mic 활동 판정, autonomy executor summary/status/recent-context payload.
- `response_output_policy.py`
  - response action tag parsing, friend-style output normalization, model output sanitize, reasoning answer extraction/meta-line filtering, display cleanup, Minecraft leak suppression, simple local chat fast-path.
- `search_followup_policy.py`
  - search promise 감지, search answer source stripping, 짧은 search/weather follow-up query classifier.
- `search_query_context.py`
  - 최근 user search context 후보 수집, generic search follow-up 해소, 날씨 위치 문맥 보강, 짧은 query memory fallback.
- `session_memory_state.py`
  - session key, follow-up target, turn/segment id, user text turn start, assistant/command/tool text turn finish, active session snapshot, conversation history, router history rendering, persona status hint.
- `room_speaker_activity.py`
  - voice room 최근 발화자 pruning, activity decay, active speaker 선택.
- `room_session_state.py`
  - voice room owner/reply-in-progress state mutation and snapshot helpers, split out of heavy voice orchestration so session policy tests stay lightweight.
- `turn_trace.py`
  - turn summary payload에 더해 JSONL trace writer와 console fallback까지 포함.
- `turn_lifecycle.py`
  - `TurnScope`에 더해 room turn scope registry, stale turn cancel count, scoped task attach/detach/create/clear helper.
- `discord_ingress.py`
  - text/voice ingress key builder, Discord message/thread context builder, reply-target check, attachment context builder, text message precheck/turn decision.
- `discord_text_turn.py`
  - Discord `on_message` text-turn orchestration, command-only/prefix precheck handoff, wake/reply/active-session gate, reply slot locking, text reply streaming, optional voice delivery, memory/search follow-up scheduling, assistant turn finalization, and text-turn summary/error logging with live dependencies injected from `main.py`.
- `discord_text_reply_runtime.py`
  - Discord text reply LLM streaming call, proactive question append/update, display text fallback, delivery plan creation, Discord send orchestration, and buffered edit streamer/sink helpers.
- `discord_command_handlers.py`
  - Discord command decorator bodies for voice join/rejoin/leave, restart/shutdown/status/page/prefix, autonomy, Minecraft, channel settings, and guild reset. `main.py` keeps decorators and injects live callbacks/config.
- `discord_command_session_runtime.py`
  - Discord command-origin text session record wiring, including thread/session key resolution and command assistant turn metadata.
- `discord_commands.py`
  - Discord command 권한/상태/접두사/채널 목록/도움말/자율상태/마인크래프트 명령/길드 초기화 응답 formatter.
- `discord_settings.py`
  - Discord command prefix와 observe/command-only channel 설정 I/O, channel id normalization, prefix validation/cache write-through.
- `search_tools.py`
  - DuckDuckGo/API/HTML search, weather query normalization, wttr weather result, search result rendering.
- `runtime_status_context.py`
  - 런타임 상태 문맥용 URL port 추출, TCP probe, 로그 tail/오류 compact, 최근 런타임 오류 수집, GPU VRAM/OOM 상태 답변.
- `runtime_mode_policy.py`
  - realtime/congested/normal runtime mode selection and mode option application from queue/backlog/inflight pressure.
- `control_page_runtime_probe.py`
  - Control Page runtime services TCP/HTTP probe orchestration, Bot API state probing, Codex gateway health 판정.
- `control_page_runtime_services_runtime.py`
  - Control Page runtime services cache freshness/staleness decision, background refresh scheduling, inline refresh fallback, and probe error payload storage.
- `control_page_minecraft_snapshot_runtime.py`
  - Control Page Minecraft snapshot cache copy/refresh/ensure orchestration and background poll task start/stop handling.
- `control_page_status_runtime.py`
  - Control Page guild/local/voice status reply live-data assembly with payload builders injected from `main.py`.
- `control_page_search_runtime.py`
  - Control Page forced-search answer path, search action execution, main synthesis, session state update, and local TTS scheduling with live callbacks injected from `main.py`.
- `control_page_tool_runtime.py`
  - Control Page memory-panel action, restart command scheduling, router-history retrieval, tool-turn recording, UI-tool router LLM request assembly, command execution dispatch to memory/runtime/voice/Minecraft tool handlers, and Control Page input routing between cheap tools/router/search/main text.
- `control_page_http.py`
  - Control Page JSON 응답, no-store cache header, CORS middleware, asset path guard, Bot API health payload.
- `minecraft_runtime_snapshot.py`
  - Minecraft runtime snapshot freshness/status fields, Voyager status/observation merge, inventory/position normalization, Control Page recent activity extraction, Main LLM용 Minecraft 상태 요약.
- `minecraft_assets.py`
  - Minecraft jar asset bytes/json reader, item/model texture path normalization, model parent/alias texture resolution, Control Page item icon jar discovery/cache loader.
- `voice_route_execution.py`
  - voice route action 실행, skill dispatch/follow-up execution, Minecraft/vision/runtime/local-tool routing callbacks.
- `llm_context_assembly.py`
  - Main LLM messages/context assembly, runtime/memory/cognitive/local-tool/Minecraft/tool-awareness context 조립.
- `main_llm_runtime.py`
  - Main LLM one-shot call, tool result synthesis, promised-search escalation and synthesis answer drift guard.
- `voice_response_runtime.py`
  - first/follow-up response split, low-latency first response LLM call, follow-up response LLM call, duplicate follow-up suppression.
- `voice_stream_chunks.py`
  - streaming speech chunker construction, streamed delta/flush question filtering, delivery-plan TTS chunk emission.
- `voice_ingress_runtime.py`
  - voice ingress entrypoint item assembly, queue dequeue/enqueue runtime, stale/full drop accounting, utterance assembly buffer scheduling/flush/merge orchestration.
- `voice_stt_flow.py`
  - wake/final/partial STT flow helpers plus speculative STT policy creation, storage, match, and expiry helpers.
- `voice_reply_side_effects.py`
  - voice reply 후 history append, memory update scheduling, search follow-up scheduling, active session/room owner 갱신 orchestration.
- `voice_reply_gate_runtime.py`
  - live session/room state, TTS input suppression, reply cooldown, and voice reply gate policy orchestration.
- `voice_delivery_runtime.py`
  - streaming voice/local-speaker answer finalization, delivery fanout, local full-answer fallback, voice-turn cancellation/error summary, with delivery/runtime callbacks injected from `main.py`.
- `local_control_tts_runtime.py`
  - Control Page local-only TTS scheduling, metrics construction, and local TTS summary logging.
- `local_control_voice_runtime.py`
  - local-only voice guild/client/member adapters and local speaker voice-client marker detection.
- `local_mic_segment_runtime.py`
  - local mic service startup/stop wiring, segment state update, Discord/local-control target routing, Discord audio suppression state, and routed debug metadata assembly.
- `local_runtime_context.py`
  - Main LLM runtime dependency topology/self-state context rendering from live local mic/TTS/config payloads.
- `tts_warmup_runtime.py`
  - OmniVoice health check/warmup generation orchestration, startup component state marking, and warmup payload construction.
- `cached_tts_runtime.py`
  - canned/cached TTS audio path resolution, cached source construction, playback request creation, and cached playback trace logging with dependencies injected from `main.py`.
- `tts_interrupt_runtime.py`
  - active TTS cancellation event logging and speaker-verification based TTS interrupt gating.
- `voice_timing_runtime.py`
  - voice latency/stage/bottleneck timing marks, alias metric recording, p95 summary logging, and turn summary event emission.
- `llm_warmup_runtime.py`
  - Main LLM startup warmup payload construction, SSE warmup response handling, and startup component state marking.
- `omnivoice_request_runtime.py`
  - OmniVoice TTS request id, voice profile, request metadata, HTTP payload construction, `TtsSynthResult` construction, and clone-to-auto fallback handling with factory injection to keep tests lightweight.
- `startup_audio_runtime.py`
  - Opus startup load checks and STT silence warmup orchestration with runtime dependencies injected from `main.py`.
- `stt_task_runtime.py`
  - blocking STT execution lock/cooldown/timeout policy and timeout failure recording with runtime callbacks injected from `main.py`.
- `startup_component_state.py`
  - startup component status/detail timestamp mutation and done-state lookup.
- `http_session_runtime.py`
  - shared aiohttp session reuse/recreate decision with timeout and session factories injected from `main.py`.
- `instance_lock_runtime.py`
  - single-instance lock acquire/release retry and cleanup logic with OS lock modules injected from `main.py`.
- `guild_runtime_reset.py`
  - guild-scoped runtime/session/task reset, task cancellation, and TTS playback tracking cleanup.
- `page_urls.py`
  - Public Evelyn page URL resolution, GitHub Pages remote derivation, and git remote lookup orchestration with subprocess runner injected from `main.py`.
- `cognitive_state_runtime.py`
  - cognitive state refresh runtime orchestration, layered scope writeback, background task cleanup.
- `memory_update_runtime.py`
  - memory writer decision 기록과 writebehind scheduling runtime orchestration.
- `search_followup_runtime.py`
  - promised/proactive search follow-up scheduling, singleflight cancellation, cognitive completion state writeback, Discord/TTS delivery, memory update scheduling.
- `observability_metrics.py`
  - turn stage mark/event logging runtime helper까지 포함. `main.py`는 `time.monotonic`, `record_turn_stage`, `merge_log_event_payload`, `log_turn_event` 주입 wrapper만 유지.

현재 `main.py`에 남은 주요 다음 후보:

- voice answer payload assembly와 delivery planning side-effect wiring
  - streaming delivery orchestration은 `voice_delivery_runtime.py`로 분리됨. answer payload assembly와 일부 delivery entrypoint wiring은 아직 `main.py`에 있음.
  - Discord text reply의 display text fallback과 delivery plan 생성은 `discord_text_reply_runtime.py`로 분리됨.
- memory vault bridge와 cognitive update wiring
  - voice reply 후 memory/search/session 갱신 wiring은 `voice_reply_side_effects.py`로 분리됨.
- autonomy executor의 남은 side-effect wiring
- remaining voice pipeline side-effect wiring
  - voice ingress entrypoint와 queue/utterance assembly runtime은 `voice_ingress_runtime.py`로 분리됨. Opus/STT/LLM/TTS warmup, OmniVoice request payload/result/fallback, TTS cache/interrupt control, voice timing logging은 runtime 모듈로 분리됨. STT/route/LLM/TTS 연결부와 speaker/session state mutation 일부가 아직 `main.py`에 있음.

## 2026-06-29 추가 진행

- `evelyn_core/runtime/evelyn_core/control_page_ui_runtime.py`:
  - Control Page ui utility(명령 enqueue/panel 상태, 로컬 URL, guild 키/ID/이름 보조, 채팅 로그 append/get, 환영문구 정제) 추출.
- `main.py`는 해당 위임부를 thin wrapper로 축소:
  - `build_control_page_ui_runtime_deps()` 추가.
  - `enqueue_control_page_ui_command`, `build_control_page_panel_state`, `control_page_local_url`, `control_page_session_key`, `control_page_effective_guild_id`, `control_page_effective_guild_name`, `append_control_page_chat_log`, `get_control_page_chat_log`, `sanitize_control_page_welcome_text`를 runtime 함수 위임형으로 변경.
- 테스트: `tests/ui/test_control_page_ui_runtime.py` 추가(기본 동작/저장소 위임/문구 정제).
- `evelyn_core/runtime/evelyn_core/control_page_runtime_services_runtime.py`:
  - Control Page runtime services cache fresh/stale/expired decision과 background refresh scheduling을 `main.py` 밖으로 이동.
  - `main.py`는 runtime services cache/lock/task/probe/error-payload 의존성만 주입.
- 테스트: `tests/ui/test_control_page_runtime_services_runtime.py` 추가(fresh cache, stale background refresh, expired/error inline refresh).
- `evelyn_core/runtime/evelyn_core/control_page_minecraft_snapshot_runtime.py`:
  - Control Page Minecraft snapshot cache copy/refresh/ensure와 background poll task start/stop을 `main.py` 밖으로 이동.
  - `main.py`는 snapshot cache/lock/task/getter와 poll task 의존성만 주입.
- 테스트: `tests/ui/test_control_page_minecraft_snapshot_runtime.py` 추가(cache hit, refresh success/error, background start/stop).
- `evelyn_core/runtime/evelyn_core/control_page_status_runtime.py`:
  - Control Page guild/local/voice status reply 조립을 `main.py` 밖으로 이동.
  - `main.py`는 model names, live status getters, payload builders만 주입.
- 테스트: `tests/ui/test_control_page_status_runtime.py` 추가(guild/local/voice status 조립).
- `evelyn_core/runtime/evelyn_core/control_page_search_runtime.py`:
  - Control Page 강제 검색 답변 경로의 search action 실행, main synthesis, session state 기록, local TTS scheduling을 `main.py` 밖으로 이동.
  - `main.py`는 route builder, search/synthesis callbacks, session lock/state callbacks, display fallback만 주입.
- 테스트: `tests/ui/test_control_page_search_runtime.py` 추가(search+synthesis 실행, session 기록, action-result fallback).
- `evelyn_core/runtime/evelyn_core/control_page_tool_runtime.py`:
  - Control Page memory panel action, restart command scheduling, recent router history, tool turn recording, UI-tool router LLM request assembly, command execution dispatch, input routing 흐름을 `main.py` 밖으로 이동.
  - `main.py`는 session store, UI command enqueue, router LLM, current turn id, search/main answer callbacks 등 live dependency만 주입.
- 테스트: `tests/ui/test_control_page_tool_runtime.py` 추가/확장(memory panel, history, tool-turn record, router request, command execution, input routing).
- `evelyn_core/runtime/evelyn_core/discord_command_handlers.py`:
  - 제어 명령어 권한 체크 헬퍼와 체크 실패 에러 처리 핸들러를 `main.py` 외부로 이동.
  - `main.py`는 `make_control_command_authorized_checker(allowed_user_ids=ALLOWED_RESTART_USER_IDS)`와 `handle_control_command_error`를 위임받는 방식으로 사용.
- 테스트: `tests/discord_io/test_discord_command_handlers.py` 추가/확장(권한 체크 허용 규칙, 체크 실패 메시지 처리 동작 검증).
- 17:29 KST continuation checkpoint: `select_control_page_guild`, `resolve_guild_member_name`, `current_tts_target_name`를 `evelyn_core/runtime/evelyn_core/control_page_guild_runtime.py`로 분리해 `main.py`에서 thin wrapper로 위임. 새 런타임 디펜스: `ControlPageGuildSelectionRuntimeDeps` + `select_control_page_guild_from_runtime`, `resolve_guild_member_name_from_runtime`, `current_tts_target_name_from_runtime`를 추가하고 `build_control_page_guild_selection_runtime_deps`로 주입.
  - 변경 파일: `main.py`, `evelyn_core/runtime/evelyn_core/control_page_guild_runtime.py`, `tests/ui/test_control_page_guild_runtime.py`.
  - 검증: `py_compile` (`main.py`, `evelyn_core/runtime/evelyn_core/control_page_guild_runtime.py`, `tests/ui/test_control_page_guild_runtime.py`), `pytest tests/ui/test_control_page_guild_runtime.py`.
  - `git diff --check` 경고 없음.
  - 현재 `main.py` 라인 수 및 런타임/봇 재시작 상태는 다음 checkpoint에서 재확인 기록.
- 17:30 KST: lock 기반 크론 구간 실행 상태 정리 완료 후 `main.py` 분해 진행 중. 크론 실행 중 런타임 재시작 없음.
- 18:17 KST continuation checkpoint: 질문 정책 유틸 5개를 `evelyn_core/runtime/evelyn_core/question_policy_runtime.py`로 이동.
  - 이동 대상(현재 `main.py` 래퍼): `normalize_question_policy_mapping`, `extract_question_policy_from_route_meta`, `user_wants_direct_answer`, `user_frustration_with_questions`, `is_continuable_technical_topic`.
  - 변경 파일: `main.py`, `evelyn_core/runtime/evelyn_core/question_policy_runtime.py`, `tests/core/test_question_policy_runtime.py`.
  - 빌더/주입: `main.py`에 `build_question_policy_runtime_deps()` 추가, 위 함수는 모두 `*_from_runtime` 래퍼로 위임.
  - 검증: `py -3 -m py_compile main.py evelyn_core/runtime/evelyn_core/question_policy_runtime.py tests/core/test_question_policy_runtime.py`, `py -3 -m pytest tests/core/test_question_policy_runtime.py`.
  - `main.py` 라인: 9079.
  - 런타임/봇 재시작: 실행 안 함.
- 2026-06-29 19:50 KST continuation checkpoint: 로컬 마이크 의존성 위임 정리.
  - `main.py`에서 `should_drop_discord_audio_for_local_mic`, `build_evelyn_runtime_dependency_context`, `local_mic_effective_max_silence_ms` 래퍼를 제거.
  - `build_local_mic_service_runtime_deps()`는 `max_silence_ms_provider`를 `local_mic_effective_max_silence_ms_from_runtime(local_tts_playback_snapshot=local_tts_playback_manager.snapshot, ...)` inline 위임 형태로 대체.
  - `build_llm_context_assembly_deps()`는 `build_evelyn_runtime_dependency_context` 대신 `build_evelyn_runtime_dependency_context_from_payload(...)`를 inline 주입.
  - 기존 `serialize_local_mic_runtime_state()`, `voice_input_mode_status_line()`, `local_mic_status_line()`는 유지해 현재 의존성 경로와 테스트 호환성을 보존.
  - 검증: `py_compile(main.py)`, `git diff --check`.
  - `main.py` 라인: 9045.
  - 런타임/봇 재시작 없음.
- 2026-06-29 23:40 KST cron checkpoint: `resolve_route_executor` 위임 정리(의존성 주입 패턴).
  - 이동 대상: `main.py`의 `resolve_route_executor` 본문을 `evelyn_core/runtime/evelyn_core/autonomy_router.py`의 `resolve_route_executor_from_runtime`로 이전.
  - 추가: `ResolveRouteExecutorRuntimeDeps`, `build_route_executor_runtime_deps`.
  - 변경 파일: `main.py`, `evelyn_core/runtime/evelyn_core/autonomy_router.py`, `tests/core/test_autonomy_router_runtime.py`.
  - 검증: `py_compile main.py`, `py_compile evelyn_core/runtime/evelyn_core/autonomy_router.py`, `py_compile tests/core/test_autonomy_router_runtime.py`, `pytest tests/core/test_autonomy_router_runtime.py`.
  - 런타임/봇 재시작 없음.
