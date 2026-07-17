# main.py 분리 목표

Last reviewed: 2026-07-17

## 2026-07-17 Control Page turn + voice delivery dependency composition 연속 배치

- Control Page forced-search와 일반 text-turn dependency root를
  `control_page_search_text_dependency_composition.py`로 이동했다.
  - 공통 session lock과 turn scope 계약을 composition이 소유한다.
  - 뒤에서 생성되는 route/search/LLM adapter는 lazy callback으로 연결했다.
  - 커밋: `96fcdc2 refactor: extract control page search text composition`.
- Control Page cheap-tool/router/search/text input routing dependency root를
  `control_page_input_dependency_composition.py`로 이동했다.
  - pure tool decision/query policy는 composition이 직접 소유하고 Control Page 실행 adapter만 late-bound한다.
  - 커밋: `9e72497 refactor: extract control page input composition`.
- voice turn-entry, voice delivery, Discord text-reply dependency root를
  `voice_delivery_dependency_composition.py`로 이동했다.
  - answer payload/delivery plan/TTS sentence split 정책은 composition이 직접 소유한다.
  - 뒤에서 생성되는 LLM/voice I/O adapter는 lazy callback으로 연결했다.
  - 커밋: `4eb883b refactor: extract voice delivery dependency composition`,
    `39599ca test: follow control page input composition boundary`.
- 누적 결과:
  - `main.py`: 3,930줄 → 3,948줄
  - 최상위 함수: 98개 → 92개
  - 줄 수 증가는 voice delivery의 late-bound adapter를 전역 조회 없이 명시적으로 나열한 typed wiring 비용이다.
  - 새 composition 경계 테스트 9개 추가
  - 각 배치마다 실제 `main.py` Control Page process smoke 통과
  - 실제 `main.py` smoke와 `PYTHONWARNINGS=error::ResourceWarning`를 포함한 전체 unittest 1,229개 통과
  - Python `compileall`, `git diff --check`, replacement character/중복 최상위 정의 검사 통과
- 런타임/컨테이너 재시작과 외부 push는 하지 않았다.
- 다음 후보는 voice TTS interrupt/cached/single-stream dependency root 또는 남은 Control Page server-start root다.

## 2026-07-17 Control Page dependency composition 연속 배치

- UI/guild selection/welcome dependency root를 `control_page_ui_dependency_composition.py`로 이동했다.
  - UI store와 guild lookup, welcome LLM 계약을 한 composition으로 묶었다.
  - 뒤에서 생성되는 Control Page와 LLM route adapter는 lazy callback으로 연결했다.
  - 커밋: `05e510d refactor: extract control page ui dependency composition`.
- runtime-services cache/probe dependency root를
  `control_page_runtime_services_dependency_composition.py`로 이동했다.
  - cache refresh/lock 계약과 service/Bot API/Codex/Voyager probe 계약을 함께 소유한다.
  - main 소유 task/lock setter는 lazy callback으로 유지했다.
  - 커밋: `8883203 refactor: extract control page runtime services composition`.
- Minecraft live/cache/background snapshot dependency root를
  `control_page_snapshot_dependency_composition.py`로 이동했다.
  - live observation normalization, cached snapshot singleflight, background poll stage를 함께 묶었다.
  - 뒤에서 생성되는 Control Page와 main 소유 task/lock setter는 lazy callback으로 연결했다.
  - 커밋: `a83b9f0 refactor: extract control page snapshot composition`,
    `a3b6682 test: follow control page snapshot composition boundary`.
- 누적 결과:
  - `main.py`: 3,939줄 → 3,930줄
  - 최상위 함수: 106개 → 98개
  - 새 composition 경계 테스트 9개 추가
  - 각 배치마다 실제 `main.py` Control Page process smoke 통과
  - 실제 `main.py` smoke와 `PYTHONWARNINGS=error::ResourceWarning`를 포함한 전체 unittest 1,220개 통과
  - Python `compileall`, `git diff --check`, replacement character/중복 최상위 정의 검사 통과
- 런타임/컨테이너 재시작과 외부 push는 하지 않았다.
- 다음 후보는 Control Page search/text/input dependency root 또는 voice TTS interrupt/reply-delivery root다.

## 2026-07-17 voice dependency composition 연속 배치

- audio ingress와 wake probe dependency root를 `voice_ingress_dependency_composition.py`로 이동했다.
  - audio/VAD/waveform과 wake 판정의 순수 정책은 composition이 직접 소유한다.
  - live session/metrics/debug adapter는 명시적 lazy callback으로 연결했다.
  - 커밋: `f4954ba refactor: extract voice ingress dependency composition`.
- partial/full STT 실행과 transcript 확정 dependency root를
  `voice_transcription_dependency_composition.py`로 이동했다.
  - STT flow, transcript correction, barge-in merge 순수 정책과 live transcript state 경계를 분리했다.
  - 커밋: `7492326 refactor: extract voice transcription dependency composition`.
- session gate, reply context, member-audio pipeline dependency root를
  `voice_member_pipeline_dependency_composition.py`로 이동했다.
  - ingress → wake → TTS interrupt → STT → transcript → session gate → reply dispatch 단계 binding을
    composition이 소유하고, `main.py`에는 live callback/state 조립과 공개 alias만 남겼다.
  - 기존 단계별 source-contract 테스트 7개는 새 composition 경계를 직접 검사하도록 갱신했다.
  - 커밋: `e601575 refactor: extract voice member pipeline dependency composition`,
    `0a6aa38 test: follow voice member pipeline composition boundary`.
- 누적 결과:
  - `main.py`: 3,955줄 → 3,939줄
  - 최상위 함수: 114개 → 106개
  - 새 composition 경계 테스트 9개 추가
  - 각 배치마다 실제 `main.py` Control Page process smoke 통과
  - 실제 `main.py` smoke와 `PYTHONWARNINGS=error::ResourceWarning`를 포함한 전체 unittest 1,211개 통과
  - Python `compileall`, `git diff --check`, replacement character/중복 최상위 정의 검사 통과
- 런타임/컨테이너 재시작과 외부 push는 하지 않았다.
- 다음 후보는 voice TTS interrupt/reply-delivery dependency builder 또는 Control Page의 남은
  UI/welcome/runtime-services/snapshot dependency root다.

## 2026-07-17 composition root 연속 배치

- 자율행동 engine factory dependency root를 `autonomy_runtime_composition.py`로 이동했다.
  - `build_autonomy_runtime_factory_deps`와 `get_or_create_autonomy_engine` 공개 계약은 composition binding으로 유지했다.
  - 뒤에서 만들어지는 cognitive/memory/local-mic adapter는 lazy callback으로 연결했다.
  - 커밋: `b817155 refactor: extract autonomy runtime composition`.
- guild runtime reset dependency root를 `guild_runtime_reset_composition.py`로 이동했다.
  - session/room/task/lock/TTS/cognitive mutable state 경계를 명시적 typed deps로 묶었다.
  - `build_guild_runtime_reset_deps`와 `reset_guild_runtime_state` 공개 계약은 composition binding으로 유지했다.
  - 커밋: `5fc3956 refactor: extract guild runtime reset composition`.
- Control Page status/tool dependency root를 `control_page_status_tool_composition.py`로 이동했다.
  - 뒤에서 생성되는 `ControlPageComposition`은 lazy getter로 연결해 순환 초기화 경계를 보존했다.
  - 기존 tool builder가 존재하지 않는 `MAX_HISTORY`를 지연 참조하던 문제를 `MAX_HISTORY_ITEMS`로 바로잡았다.
  - local-mic/voice tool 정적 계약 테스트도 새 composition 경계를 따르도록 갱신했다.
  - 커밋: `b08eb5b refactor: extract control page status tool composition`,
    `21223c2 test: follow control page status tool boundary`.
- 누적 결과:
  - `main.py`: 3,971줄 → 3,955줄
  - 최상위 함수: 120개 → 114개
  - 새 composition 경계 테스트 9개 추가
  - 각 배치마다 실제 `main.py` Control Page process smoke 통과
  - 실제 `main.py` smoke와 `PYTHONWARNINGS=error::ResourceWarning`를 포함한 전체 unittest 1,202개 통과
  - Python `compileall`, `git diff --check`, replacement character/중복 최상위 정의 검사 통과
- 런타임/컨테이너 재시작과 외부 push는 하지 않았다.
- 다음 후보는 voice audio ingress/wake/STT/transcript dependency builder 군이다.

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
- 두 번째 절개에서 wake probe와 본문 STT 이전 조기 차단을 분리했다.
  - 새 모듈: `evelyn_core/runtime/evelyn_core/voice_wake_probe_runtime.py`
  - 새 계약: `VoiceWakeProbeDeps`, `VoiceWakeProbeResult`
  - 이동 책임: owner follow-up probe 생략, wake STT 실행/해석, strict confirm, fuzzy near-miss,
    hard reject, 환경음·짧은 필러·반복 소음·저신호 조기 차단 및 관련 debug/drop 기록.
  - `main.py`는 live 콜백을 조립하는 `build_voice_wake_probe_runtime_deps()`와
    `run_voice_wake_probe_from_runtime(...)` 호출 및 결과 전달만 유지한다.
- 두 번째 절개 뒤 정확한 누적 크기 변화:
  - `main.py`: 8,793줄 → 8,560줄
  - `_process_member_audio_impl`: 729줄 → 421줄
- 두 번째 절개 검증:
  - 새 경계 단위 테스트 12개 통과
  - `tests/voice` 249개 통과
  - `tests/discord_io` 78개 통과
  - CI와 같은 discovery 명령으로 전체 unittest 970개 통과
  - 실제 `main.py` 프로세스 smoke와 `PYTHONWARNINGS=error::ResourceWarning`를 함께 적용한 전체 unittest 970개 통과
  - `py_compile`, `git diff --check` 통과(`git diff --check`는 기존 LF/CRLF 안내만 출력)
- 런타임/컨테이너 재시작과 외부 push는 하지 않았다.
- 세 번째 절개에서 TTS interrupt와 입력 억제 gate를 기존 `tts_interrupt_runtime.py`로 이동했다.
  - 새 계약: `VoiceTtsInterruptGateDeps`, `VoiceTtsInterruptGateResult`
  - 이동 책임: interrupt 자격 판정, local/Discord TTS 상태 조회, 화자 검증, local playback 중단,
    Discord playback debounce/중단, post-TTS 입력 억제, barge-in 연속성 기록.
  - `main.py`는 `build_voice_tts_interrupt_gate_deps()`와
    `run_voice_tts_interrupt_gate_from_runtime(...)` 호출만 유지한다.
- 세 번째 절개 뒤 정확한 누적 크기 변화:
  - `main.py`: 8,793줄 → 8,515줄
  - `_process_member_audio_impl`: 729줄 → 353줄
- 세 번째 절개 검증:
  - TTS interrupt runtime 테스트 12개 통과(새 gate 테스트 8개 포함)
  - `tests/voice` 257개 통과
  - `tests/discord_io` 78개 통과
  - CI와 같은 discovery 명령으로 전체 unittest 978개 통과
  - 실제 `main.py` 프로세스 smoke와 `PYTHONWARNINGS=error::ResourceWarning`를 함께 적용한 전체 unittest 978개 통과
- 런타임/컨테이너 재시작과 외부 push는 하지 않았다.
- 네 번째 절개에서 partial/full STT 실행을 분리했다.
  - 새 모듈: `evelyn_core/runtime/evelyn_core/voice_stt_execution_runtime.py`
  - 새 계약: `VoiceSttExecutionDeps`, `VoiceSttExecutionResult`
  - 이동 책임: partial STT와 committed/speculative 상태 기록, full STT/rescore 실행,
    후보 선택 wake context 전달, full STT 실패·빈 결과 처리와 debug 저장.
  - partial STT 실패는 기존처럼 full STT 진행을 막지 않는다.
- 네 번째 절개 뒤 정확한 누적 크기 변화:
  - `main.py`: 8,793줄 → 8,498줄
  - `_process_member_audio_impl`: 729줄 → 304줄
- 네 번째 절개 검증:
  - 새 STT 실행 경계 테스트 7개 통과
  - `tests/voice` 264개 통과
  - `tests/discord_io` 78개 통과
  - CI와 같은 discovery 명령으로 전체 unittest 985개 통과
  - 실제 `main.py` 프로세스 smoke와 `PYTHONWARNINGS=error::ResourceWarning`를 함께 적용한 전체 unittest 985개 통과
- 런타임/컨테이너 재시작과 외부 push는 하지 않았다.
- 다섯 번째 절개에서 transcript 확정과 TTS barge-in 문장 병합을 분리했다.
  - 새 모듈: `evelyn_core/runtime/evelyn_core/voice_transcript_finalize_runtime.py`
  - 새 계약: `VoiceTranscriptFinalizeDeps`, `VoiceTranscriptFinalizeResult`
  - 이동 책임: 최종 transcript 보정/commit/speculative 기록, interrupt 시각 정규화,
    barge-in 문장 병합과 transcript 교체, 병합 metadata 기록.
- 다섯 번째 절개 뒤 정확한 누적 크기 변화:
  - `main.py`: 8,793줄 → 8,476줄
  - `_process_member_audio_impl`: 729줄 → 255줄
- 다섯 번째 절개 검증:
  - 새 transcript 확정 경계 테스트 7개 통과
  - `tests/voice` 271개 통과
  - `tests/discord_io` 78개 통과
  - CI와 같은 discovery 명령으로 전체 unittest 992개 통과
  - 실제 `main.py` 프로세스 smoke와 `PYTHONWARNINGS=error::ResourceWarning`를 함께 적용한 전체 unittest 992개 통과
- 런타임/컨테이너 재시작과 외부 push는 하지 않았다.
- 여섯 번째 절개에서 short transcript와 final wake session gate를 분리했다.
  - 새 모듈: `evelyn_core/runtime/evelyn_core/voice_session_gate_runtime.py`
  - 새 계약: `VoiceSessionGateDeps`, `VoiceSessionGateResult`
  - 이동 책임: short follow-up 후보 보존, short noise 차단, final-text wake veto,
    accepted transcript debug 저장과 최종 STT trace.
- 여섯 번째 절개 뒤 정확한 누적 크기 변화:
  - `main.py`: 8,793줄 → 8,470줄
  - `_process_member_audio_impl`: 729줄 → 231줄
- 여섯 번째 절개 검증:
  - 새 session gate 테스트 7개 통과
  - `tests/voice` 278개 통과
  - `tests/discord_io` 78개 통과
  - CI와 같은 discovery 명령으로 전체 unittest 999개 통과
  - 실제 `main.py` 프로세스 smoke와 `PYTHONWARNINGS=error::ResourceWarning`를 함께 적용한 전체 unittest 999개 통과
- 런타임/컨테이너 재시작과 외부 push는 하지 않았다.
- 일곱 번째 절개에서 reply context 생성과 dispatch를 분리했다.
  - 새 모듈: `evelyn_core/runtime/evelyn_core/voice_reply_dispatch_runtime.py`
  - 새 계약: `VoiceReplyDispatchDeps`
  - 이동 책임: room reply 상태, topic seed, ingress/queue metadata, memory key를 포함한
    `VoiceTranscriptReplyContext` 생성과 기존 reply orchestrator 호출.
  - `VoiceTranscriptReplyDeps` 조립은 전역을 숨기는 방식 없이 명시적 builder로 `main.py`에 유지했다.
- 일곱 번째 절개 뒤 정확한 누적 크기 변화:
  - `main.py`: 8,793줄 → 8,478줄(직전 8,470줄보다 builder/import로 8줄 증가)
  - `_process_member_audio_impl`: 729줄 → 185줄
- 일곱 번째 절개 검증:
  - 새 reply dispatch 테스트 6개 통과
  - `tests/voice` 284개 통과
  - `tests/discord_io` 78개 통과
  - CI와 같은 discovery 명령으로 전체 unittest 1,005개 통과
  - 실제 `main.py` 프로세스 smoke와 `PYTHONWARNINGS=error::ResourceWarning`를 함께 적용한 전체 unittest 1,005개 통과
- 런타임/컨테이너 재시작과 외부 push는 하지 않았다.
- 여덟 번째 배치에서 분리된 모든 음성 단계를 상위 pipeline으로 묶었다.
  - 새 모듈: `evelyn_core/runtime/evelyn_core/voice_member_audio_pipeline_runtime.py`
  - 새 계약: `VoiceMemberAudioPipelineDeps`
  - 이동 책임: ingress → wake → TTS interrupt → STT → transcript → session gate → reply dispatch
    순서와 각 단계의 short-circuit 반환.
  - `_process_member_audio_impl`은 typed pipeline dependency를 조립해 한 번 호출하는 thin wrapper가 됐다.
  - 기존 단계별 source-contract 테스트 7개도 직접 호출 위치가 아니라
    `main builder → pipeline stage` 연결을 검사하도록 갱신했다.
- 여덟 번째 배치 뒤 정확한 누적 크기 변화:
  - `main.py`: 8,793줄 → 8,346줄
  - `_process_member_audio_impl`: 729줄 → 30줄
- 여덟 번째 배치 검증:
  - 새 상위 pipeline 테스트 8개 통과
  - `tests/voice` 292개 통과
  - `tests/discord_io` 78개 통과
  - CI와 같은 discovery 명령으로 전체 unittest 1,013개 통과
  - 실제 `main.py` 프로세스 smoke와 `PYTHONWARNINGS=error::ResourceWarning`를 함께 적용한 전체 unittest 1,013개 통과
  - Python `compileall`, `git diff --check` 통과(`git diff --check`는 기존 LF/CRLF 안내만 출력)
- 런타임/컨테이너 재시작과 외부 push는 하지 않았다.
- 음성 hot path의 실행 orchestration 분리는 완료했다. 다음 구조 작업은 `main.py` 전역의
  runtime dependency builder 군을 기능별 composition root로 묶는 별도 단계다.
- 아홉 번째 배치에서 비음성 최대 함수였던 autonomy engine factory를 분리했다.
  - 새 모듈: `evelyn_core/runtime/evelyn_core/autonomy_runtime_factory.py`
  - 새 계약: `AutonomyRuntimeFactoryDeps`
  - 이동 책임: follow-up 채널 선택/알림, autonomy observation, proactive question,
    follow-up session/memory 기록, summary/status, cognitive refresh task, engine 생성과 캐시.
  - `get_or_create_autonomy_engine`은 runtime factory에 typed deps를 주입하는 thin wrapper가 됐다.
- 아홉 번째 배치 뒤 정확한 누적 크기 변화:
  - `main.py`: 8,793줄 → 8,153줄
  - 최대 함수: `get_or_create_autonomy_engine` 242줄 제거 후 `create_omnivoice_source` 146줄
- 아홉 번째 배치 검증:
  - 새 autonomy factory 테스트 8개 통과
  - CI와 같은 discovery 명령으로 전체 unittest 1,021개 통과
  - 실제 `main.py` 프로세스 smoke와 `PYTHONWARNINGS=error::ResourceWarning`를 함께 적용한 전체 unittest 1,021개 통과
  - Python `compileall` 통과
- 런타임/컨테이너 재시작과 외부 push는 하지 않았다.
- 다음 대형 함수 위험 순위는 `create_omnivoice_source` 146줄,
  `stream_local_tts_sentences` 129줄, `classify_llm_route_async` 118줄이다.
- 열 번째 배치에서 146줄 `create_omnivoice_source`를 분리했다.
  - 새 모듈: `evelyn_core/runtime/evelyn_core/omnivoice_source_runtime.py`
  - 새 계약: `OmniVoiceSourceRuntimeDeps`
  - 이동 책임: HTTP PCM streaming, 첫 byte/PCM trace, clone voice→auto fallback 연결,
    source finish/fail, turn-scope task 생성과 취소 cleanup.
  - 새 테스트 6개: 빈 입력, 정상 PCM, trace callback, clone fallback, 최종 HTTP 실패, 취소 cleanup, main 위임.
- 열한 번째 배치에서 129줄 `stream_local_tts_sentences`를 분리했다.
  - 새 모듈: `evelyn_core/runtime/evelyn_core/local_tts_stream_runtime.py`
  - 새 계약: `LocalTtsStreamRuntimeDeps`
  - 이동 책임: sentence source prefetch, local speaker 순차 재생, 첫 재생/latency trace,
    playback/prefetch 실패 기록, leftover source cleanup, turn-scope attach/detach.
  - 새 테스트 5개: disabled gate, 정상 streaming/callback, playback 실패/leftover cleanup,
    prefetch 실패 stage, main 위임.
- 열한 번째 배치 뒤 정확한 누적 크기 변화:
  - `main.py`: 8,793줄 → 7,971줄
  - 최대 함수: 242줄 autonomy factory → 146줄 OmniVoice source → 129줄 local TTS stream →
    현재 `classify_llm_route_async` 118줄
- 열 번째·열한 번째 배치 통합 검증:
  - 관련 OmniVoice/local TTS 테스트 93개 통과
  - 일반 discovery 전체 unittest 1,032개 통과(실제 `main.py` opt-in 1개 의도적 skip)
  - 실제 `main.py` smoke와 `PYTHONWARNINGS=error::ResourceWarning`를 함께 적용한 전체 unittest 1,032개 통과
  - Python `py_compile`, `git diff --check` 통과
- 런타임/컨테이너 재시작과 외부 push는 하지 않았다.
- 다음 대형 함수 위험 순위는 `classify_llm_route_async` 118줄,
  `ask_llm_once` 111줄, `answer_control_page_text` 104줄이다.
- 열두 번째 배치에서 118줄 `classify_llm_route_async`를 분리했다.
  - 새 모듈/계약: `llm_route_runtime.py`, `LlmRouteRuntimeDeps`
  - 이동 책임: fast-path, voice/router-disabled fallback, memory/cognitive router prompt,
    router 실패/invalid JSON fallback, question/context policy 정규화.
  - 새 테스트 7개: fast-path, voice fallback, disabled router, 정상 router, 예외, invalid 결과, main 위임.
- 열세 번째 배치에서 111줄 `ask_llm_once` orchestration을 `main_llm_runtime.py`로 이동했다.
  - 새 계약: `AskLlmOnceRuntimeDeps`
  - 이동 책임: route context 준비, skill/policy short-circuit, Minecraft/runtime context,
    Main LLM payload/실행, promised-search 보정, question trace와 latency logging.
  - 새 테스트 6개: skill/policy short-circuit, 정상 실행, casual Minecraft skip,
    question trace 비활성, echo fallthrough, main 위임.
- 열네 번째 배치에서 104줄 `answer_control_page_text`를 분리했다.
  - 새 모듈/계약: `control_page_text_runtime.py`, `ControlPageTextRuntimeDeps`
  - 이동 책임: text turn/scope lifecycle, streaming answer, black-frame 오류 치환,
    proactive question, session finalize, local TTS schedule, 성공/실패 summary와 cleanup.
  - 새 테스트 5개: 정상/proactive, resolved question, black frame, 실패 cleanup, main 위임.
- 열네 번째 배치 뒤 정확한 누적 크기 변화:
  - `main.py`: 8,793줄 → 7,762줄
  - 최대 함수: 118줄 LLM route → 111줄 single LLM → 104줄 Control Page text →
    현재 `build_runtime_status_context` 83줄
- 열두 번째~열네 번째 배치 통합 검증:
  - 새 테스트 18개와 관련 테스트 32개 통과
  - 일반 discovery 전체 unittest 1,050개 통과(실제 `main.py` opt-in 1개 의도적 skip)
  - 실제 `main.py` smoke와 `PYTHONWARNINGS=error::ResourceWarning`를 함께 적용한 전체 unittest 1,050개 통과
  - Python `compileall`, `git diff --check` 통과
- 런타임/컨테이너 재시작과 외부 push는 하지 않았다.
- 다음 최대 함수는 `build_runtime_status_context` 83줄, `stream_tts_sentences` 81줄,
  `build_fast_path_policy_runtime_deps` 72줄이다.
- 열다섯 번째 배치에서 83줄 `build_runtime_status_context`와 상태 소유권을 분리했다.
  - 확장 모듈/계약: `runtime_status_context.py`, `RuntimeStatusContextDeps`, `RuntimeStatusContextState`
  - 이동 책임: TTL/force cache, lazy lock, TCP probe, Control API 상태, GPU/OOM/recent error 요약.
  - `main.py`의 cache dict와 lock 전역을 제거하고 상태 객체 하나만 composition root에 유지했다.
  - 새 테스트 6개: disabled/cache/force, service+GPU+error 조합, service failure, main 위임.
- 열여섯 번째 배치에서 81줄 `stream_tts_sentences`를 분리했다.
  - 새 모듈/계약: `discord_tts_stream_runtime.py`, `DiscordTtsStreamRuntimeDeps`
  - 이동 책임: source callback/trace, playback request, prefetch/prepared failure stage, turn-scope cleanup.
  - 새 테스트 4개: 정상 request/callback, failure stage, playback 예외 cleanup, main 위임.
- 열일곱 번째 배치에서 71줄 `generate_control_page_welcome_text`를 분리했다.
  - 확장 모듈/계약: `control_page_ui_runtime.py`, `ControlPageWelcomeRuntimeDeps`
  - 이동 책임: welcome prompt/payload, HTTP 응답, sanitize, 성공/실패 model trace와 fallback.
  - 새 테스트 4개: 정상 생성, HTTP 실패, empty choices, main 위임.
- 열일곱 번째 배치 뒤 정확한 누적 크기 변화:
  - `main.py`: 8,793줄 → 7,631줄
  - 현재 최대 함수는 `build_fast_path_policy_runtime_deps` 72줄이며,
    최대 실행 함수는 `speak_answer_local` 65줄이다.
- 열다섯 번째~열일곱 번째 배치 통합 검증:
  - 새 테스트 14개와 관련 테스트 70개 통과
  - 일반 discovery 전체 unittest 1,064개 통과(실제 `main.py` opt-in 1개 의도적 skip)
  - 실제 `main.py` smoke와 `PYTHONWARNINGS=error::ResourceWarning`를 함께 적용한 전체 unittest 1,064개 통과
  - Python `compileall`, `git diff --check` 통과
- 첫 전체 회귀에서 runtime status 문자열 위치를 `main.py`로 고정한 정적 테스트 1개가 실패했고,
  새 소유 모듈을 검사하도록 수정한 뒤 두 전체 회귀를 처음부터 재통과했다.
- 런타임/컨테이너 재시작과 외부 push는 하지 않았다.
- 다음 후보는 `speak_answer_local` 65줄, `ask_summary_llm` 64줄,
  `ask_router_llm` 64줄과 dependency builder composition root 정리다.
- 열여덟 번째 배치에서 65줄 `speak_answer_local`을 분리했다.
  - 확장 모듈/계약: `local_tts_stream_runtime.py`, `LocalTtsSingleRuntimeDeps`
  - 이동 책임: enabled/empty gate, source callback/ready wait, local playback, 취소 전파, 실패 기록과 task detach.
  - 새 테스트 6개: disabled, empty, 정상 callback/playback, 실패, 취소, main 위임.
- 열아홉 번째·스무 번째 배치에서 각 64줄 `ask_summary_llm`과 `ask_router_llm`을 공통화했다.
  - 새 모듈/계약: `json_llm_request_runtime.py`, `JsonLlmRequestRuntimeDeps`
  - 이동 책임: non-streaming JSON payload/timeout/HTTP 오류, content/reasoning JSON 추출,
    empty choices와 성공 model trace.
  - `main.py`는 summary/router별 model/endpoint/role/error label만 조립한다.
  - 새 테스트 5개: content, reasoning, empty choices, role별 HTTP 오류, 두 main wrapper 위임.
- 스무 번째 배치 뒤 정확한 누적 크기 변화:
  - `main.py`: 8,793줄 → 7,554줄
  - 현재 최대 함수는 `build_fast_path_policy_runtime_deps` 72줄,
    최대 실행 함수는 Discord 단일 `speak_answer` 60줄이다.
- 열여덟 번째~스무 번째 배치 통합 검증:
  - 새 테스트 11개와 관련 테스트 35개 통과
  - 일반 discovery 전체 unittest 1,075개 통과(실제 `main.py` opt-in 1개 의도적 skip)
  - 실제 `main.py` smoke와 `PYTHONWARNINGS=error::ResourceWarning`를 함께 적용한 전체 unittest 1,075개 통과
  - Python `compileall`, `git diff --check` 통과
- 신규 local TTS empty 테스트의 첫 실행에서 test double이 실제 `clean_tts_text`와 달리
  OmniVoice 태그를 제거하지 않아 1개가 실패했고, 실제 계약으로 수정 후 관련/전체 회귀를 재통과했다.
- 런타임/컨테이너 재시작과 외부 push는 하지 않았다.
- 다음 후보는 `speak_answer` 60줄, `transcribe_audio16k_sync` 55줄,
  `connect_evelyn_voice_client` 55줄과 dependency builder composition root 정리다.
- 스물한 번째 배치에서 60줄 Discord 단일 `speak_answer`를 분리했다.
  - 확장 모듈/계약: `discord_tts_stream_runtime.py`, `DiscordTtsSingleRuntimeDeps`
  - 이동 책임: local speaker 분기, turn state 전환, cached audio short-circuit,
    OmniVoice source/first-packet callback, single playback request.
  - 새 테스트 4개: local 위임, cached short-circuit, source/playback request, main 위임.
- 스물두 번째 배치에서 55줄 `transcribe_audio16k_sync`를 분리했다.
  - 새 모듈/계약: `stt_transcription_runtime.py`, `SttTranscriptionRuntimeDeps`
  - 이동 책임: remote STT request/fallback, language 결정, float32 변환/resample,
    local Qwen ASR 실행과 결과 정제/로그.
  - 새 테스트 5개: empty, remote 계약, fallback 차단, local resample, main 위임.
- 스물세 번째 배치에서 55줄 `connect_evelyn_voice_client`와 내부 reconnect 대기를 분리했다.
  - 새 모듈/계약: `discord_voice_connection_runtime.py`, `DiscordVoiceConnectionRuntimeDeps`
  - 이동 책임: guild별 connect lock, 내부 reconnect 재사용, listener arm,
    실패 시 stale disconnect/voice-state 정리와 bounded retry.
  - 새 테스트 4개: reconnect 재사용, 정상 연결/arm, 실패 정리/재시도, main 위임.
- 스물세 번째 배치 뒤 정확한 누적 크기 변화:
  - `main.py`: 8,793줄 → 7,463줄
  - 현재 최대 함수는 `build_fast_path_policy_runtime_deps` 72줄이며,
    최대 실행 함수는 `build_live_vision_context` 55줄이다.
- 스물한 번째~스물세 번째 배치 통합 검증:
  - 새 테스트 13개, 관련 테스트 21개 통과(선택 의존성 조건에 따른 5개 skip)
  - 일반 discovery 전체 unittest 1,088개 통과(실제 `main.py` opt-in 1개 의도적 skip)
  - 실제 `main.py` smoke와 `PYTHONWARNINGS=error::ResourceWarning`를 함께 적용한 전체 unittest 1,088개 통과
  - Python `py_compile`, `git diff --check` 통과
- 첫 전체 회귀에서 소유권 이동 전 문자열 위치를 `main.py`로 고정한 정적 테스트 2개가 실패했고,
  새 Discord TTS/STT 소유 모듈을 검사하도록 수정한 뒤 두 전체 회귀를 처음부터 재통과했다.
- 검증 명령에 `-t .`이 없거나 Voyager 가상환경 경로가 빠지면 `tests.voyager`와 외부
  `voyager` 패키지 충돌 또는 `gymnasium` 누락이 발생하므로 정식 discovery 계약을 사용했다.
- 런타임/컨테이너 재시작과 외부 push는 하지 않았다.
- 다음 후보는 `build_live_vision_context` 55줄, `answer_from_search_results` 53줄,
  `ask_llm_streaming` 52줄과 dependency builder composition root 정리다.
- 스물네 번째 배치에서 55줄 `build_live_vision_context`를 분리했다.
  - 확장 모듈/계약: `vision_runtime.py`, `LiveVisionContextRuntimeDeps`
  - 이동 책임: capture disabled/error/black-frame 처리, vision HTTP 분석, 요청 이미지 삭제,
    관찰 포맷과 quality/latency/문자 수 metrics 기록.
  - 새 테스트 5개: disabled, black frame, 분석 실패 삭제, 정상 metrics/payload, main 위임.
- 스물다섯 번째 배치에서 53줄 `answer_from_search_results`를 분리했다.
  - 새 모듈/계약: `search_answer_runtime.py`, `SearchAnswerRuntimeDeps`
  - 이동 책임: 검색 결과 prompt/payload, HTTP 오류, model answer sanitize/source 제거,
    empty choices/answer의 첫 snippet fallback.
  - 새 테스트 5개: empty results, 정상 request, HTTP 오류, fallback/source 제거, main 위임.
- 스물여섯 번째 배치에서 52줄 `ask_llm_streaming` 진입 경계를 분리했다.
  - 새 모듈/계약: `voice_turn_entry_runtime.py`, `VoiceTurnEntryRuntimeDeps`
  - 이동 책임: `VoiceTurnRequest` 생성, orchestrator dependency 조립/실행,
    pipeline failure 기록과 turn-scope task detach.
  - 새 테스트 3개: request/orchestrator 실행, 실패 기록/cleanup, main 위임.
- 스물여섯 번째 배치 뒤 정확한 누적 크기 변화:
  - `main.py`: 8,793줄 → 7,402줄
  - 현재 최대 함수는 `build_fast_path_policy_runtime_deps` 72줄이며,
    최대 실행 함수는 `start_control_page_server` 47줄이다.
- 스물네 번째~스물여섯 번째 배치 통합 검증:
  - 새 테스트 13개, 관련 테스트 32개 통과
  - 일반 discovery 전체 unittest 1,101개 통과(실제 `main.py` opt-in 1개 의도적 skip)
  - 실제 `main.py` smoke와 `PYTHONWARNINGS=error::ResourceWarning`를 함께 적용한 전체 unittest 1,101개 통과
  - Python `compileall`, `git diff --check` 통과
- 첫 관련 테스트에서 test double turn scope의 취소 계약 누락 2개와 이전 vision 소유 위치를
  고정한 정적 테스트 1개가 실패했고 실제 계약/새 모듈 기준으로 수정했다.
- 첫 전체 회귀에서 검색 정책 문구 위치를 `main.py`로 고정한 정적 테스트 1개가 실패했고,
  `search_answer_runtime.py` 기준으로 수정한 뒤 일반·엄격 전체 회귀를 처음부터 재통과했다.
- 런타임/컨테이너 재시작과 외부 push는 하지 않았다.
- 다음 후보는 `start_control_page_server` 47줄, `observe_live_minecraft_state` 45줄,
  `get_control_page_minecraft_snapshot` 44줄과 dependency builder composition root 정리다.
- 스물일곱 번째 배치에서 47줄 `start_control_page_server` 실행 경계를 분리했다.
  - 새 모듈/계약: `control_page_server_start_runtime.py`, `ControlPageServerStartRuntimeDeps`
  - 이동 책임: enabled/duplicate/docs gate, lazy start lock, route registrar,
    AppRunner/TCPSite setup과 실패 cleanup, runner/site 게시 및 startup 상태 기록.
  - 라우트 목록과 handler 조합은 `main.py` composition root에 유지했다.
  - 새 테스트 5개: disabled/existing, docs missing, 정상 route/start, setup cleanup, main 위임.
- 스물여덟 번째 배치에서 45줄 `observe_live_minecraft_state`를 분리했다.
  - 새 모듈/계약: `minecraft_live_state_runtime.py`, `MinecraftLiveObservationRuntimeDeps`
  - 이동 책임: status 우선 조회, 실제 context 판정, observe fallback, snapshot freshness metadata.
- 스물아홉 번째 배치에서 44줄 `get_control_page_minecraft_snapshot`을 같은 모듈로 분리했다.
  - 계약: `ControlPageMinecraftLiveSnapshotRuntimeDeps`
  - 이동 책임: status error/fallback, inventory/slot/activity/task/progress/position 정규화,
    Control Page live snapshot metadata.
  - Minecraft 새 테스트 6개: status context, observe fallback, 이중 실패, snapshot 정규화,
    status error 보존, 두 main wrapper 위임.
- 스물아홉 번째 배치 뒤 정확한 누적 크기 변화:
  - `main.py`: 8,793줄 → 7,380줄
  - 현재 최대 함수는 `build_fast_path_policy_runtime_deps` 72줄이며,
    최대 실행/조립 함수는 `build_control_page_state` 45줄이다.
- 스물일곱 번째~스물아홉 번째 배치 통합 검증:
  - 새 테스트 11개, 관련 테스트 19개 통과
  - 일반 discovery 전체 unittest 1,112개 통과(실제 `main.py` opt-in 1개 의도적 skip)
  - 실제 `main.py` smoke와 `PYTHONWARNINGS=error::ResourceWarning`를 함께 적용한 전체 unittest 1,112개 통과
  - Python `compileall`, `git diff --check` 통과
- 첫 전체 회귀에서 `/health`와 `/shutdown` route를 직접 `app.router` 호출 위치로 고정한
  정적 테스트 2개가 실패했고, main route tuple과 runtime registrar 계약을 검사하도록 수정한 뒤
  일반·엄격 전체 회귀를 처음부터 재통과했다.
- 런타임/컨테이너 재시작과 외부 push는 하지 않았다.
- 다음 후보는 `build_control_page_state` 45줄, Discord `on_ready` 43줄,
  `run_vision_watch_once` 40줄과 dependency builder composition root 정리다.

## 2026-07-16 대형 배치 전환: Control Page composition 일괄 분리

- 서른 번째 배치부터 작은 함수 2~3개 단위가 아니라 도메인 전체를 한 번에 이동하는 방식으로 전환했다.
- 새 모듈/계약:
  - `control_page_composition_runtime.py`
  - `ControlPageCompositionDeps`, `ControlPageComposition`
  - `ControlPageHttpCompositionDeps`, `ControlPageHttpComposition`
- 이동 책임:
  - UI command/chat/welcome/guild 선택 adapter
  - Minecraft snapshot/cache/background adapter
  - runtime service/status/tool/search/text/input adapter
  - welcome single-flight와 startup component/boot progress adapter
  - Control Page HTTP handler 13개와 서버 route tuple 조립
  - Control Page 서버 시작 위임
- `main.py`의 Control Page 구간은 명시적 dependency builder와 composition root만 남겼고,
  동적 `globals()`/namespace 주입은 사용하지 않았다.
- 정확한 크기 변화:
  - `main.py`: 7,380줄 → 6,914줄
  - 이번 배치 순감축: 466줄
- 검증:
  - 관련 회귀 29개 통과(기존 실프로세스 opt-in 1개 skip)
  - CI와 동일한 `-s tests -t .` discovery 전체 unittest 1,112개 통과
  - 실제 `main.py` smoke + `PYTHONWARNINGS=error::ResourceWarning` 전체 unittest 1,112개 통과
  - `py_compile`, `git diff --check` 통과
- 런타임/컨테이너 재시작과 외부 push는 하지 않았다.
- 다음 대형 후보는 LLM/route composition 영역이며, Control Page에 남은 dependency builder는
  해당 조립 root를 별도 모듈로 옮길 때 함께 정리한다.

## 2026-07-16 LLM/route composition 일괄 분리

- 서른한 번째 배치는 LLM·검색·라우팅·스트리밍 진입 adapter를 한 번에 이동했다.
- 새 모듈/계약:
  - `llm_route_composition_runtime.py`
  - `LlmRouteCompositionDeps`, `LlmRouteComposition`
- 이동 책임:
  - fast-path 판정 adapter 8개
  - LLM context 조립, summary/router JSON 요청, route classification adapter
  - model output 정제와 reasoning answer 추출
  - search query/answer, proactive follow-up, single-flight scheduling adapter
  - main LLM 단일 요청, tool synthesis, promised-search escalation adapter
  - route executor, short-circuit/registered route, main streaming turn, streaming entry adapter
- 기존 공개 호출 시그니처를 composition 메서드에 명시적으로 보존했고,
  `main.py`에는 dependency builder와 composition root/호환 alias만 남겼다.
- 동적 `globals()`/namespace 주입은 사용하지 않았다.
- 정확한 크기 변화:
  - `main.py`: 6,914줄 → 6,347줄
  - 이번 배치 순감축: 567줄
  - 대형 배치 2회 누적: 7,380줄 → 6,347줄, 1,033줄 순감축
- 검증:
  - 관련 LLM/route/search 회귀 62개 통과
  - 일반 discovery 전체 unittest 1,112개 통과(실제 main opt-in 1개 skip)
  - 실제 `main.py` smoke + `PYTHONWARNINGS=error::ResourceWarning` 전체 unittest 1,112개 통과
  - `py_compile`, `git diff --check` 통과
- 런타임/컨테이너 재시작과 외부 push는 하지 않았다.
- 다음 대형 후보는 voice/STT/전달 adapter 영역이다.

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
  - 런타임 상태 문맥용 TTL cache/lazy lock 상태, URL port/TCP probe, Control API/GPU VRAM/OOM/최근 오류 요약과 GPU 상태 답변.
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
- `control_page_text_runtime.py`
  - Control Page 일반 text turn/scope lifecycle, streaming answer, black-frame 오류 치환, proactive question, session finalize, local TTS와 summary cleanup.
- `control_page_ui_runtime.py`
  - Control Page URL/session/guild/chat UI helpers와 welcome LLM prompt/HTTP/trace/fallback 생성.
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
- `llm_route_runtime.py`
  - fast-path/router/fallback route 선택, memory/cognitive router prompt, question/context policy 정규화.
- `main_llm_runtime.py`
  - Main LLM one-shot HTTP call과 turn orchestration, skill/policy short-circuit, tool result synthesis, promised-search escalation and synthesis answer drift guard.
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
- `omnivoice_source_runtime.py`
  - OmniVoice HTTP PCM streaming, trace callback, source lifecycle, clone fallback 실행과 turn-scope producer cleanup.
- `local_tts_stream_runtime.py`
  - 로컬 스피커 단일/sentence source 합성, ready wait/prefetch, 순차 재생, latency trace, 실패 기록과 cleanup.
- `discord_tts_stream_runtime.py`
  - Discord sentence TTS source callback/trace, streaming playback request, prefetch/prepared failure stage와 turn-scope cleanup.
- `json_llm_request_runtime.py`
  - summary/router 공통 non-streaming JSON LLM payload/HTTP/timeout, content/reasoning 추출과 성공 trace.
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

## 2026-07-17 TTS dependency root 연속 분리

- `voice_tts_control_dependency_composition.py`
  - TTS interruption, cached TTS playback, voice interruption gate의 세 dependency builder를 `main.py` 밖으로 이동.
  - speaker verification, playback manager, cache resolver, interruption policy는 명시적 dependency로 주입.
- `discord_tts_dependency_composition.py`
  - Discord single-answer 및 streaming TTS dependency builder를 하나의 composition root로 통합.
  - cached playback과 local-speaker fallback은 late-bound callback으로 유지해 기존 `VoiceIoComposition` 초기화 순서를 보존.
- `local_tts_dependency_composition.py`
  - local-speaker single-answer 및 streaming TTS dependency builder를 하나의 composition root로 통합.
  - delivery entry가 제공하는 first-playback marker와 prepared-source cleanup은 late-bound callback으로 연결.
- 구조 결과:
  - 대상 builder 7개는 `main.py`의 top-level 함수에서 제거되고 세 composition method alias로 대체됨.
  - `main.py`: 3,942 lines, top-level functions 85개.
- 검증:
  - 각 composition 경계 테스트 3개씩 통과.
  - 각 배치 후 실제 `main.py` control-page process smoke 통과.
  - `EVELYN_RUN_REAL_MAIN_INTEGRATION=1`, `PYTHONWARNINGS=error::ResourceWarning` 전체 discovery: 1,238 tests 통과.
  - 런타임/컨테이너 재시작 및 원격 push 없음.

## 2026-07-17 voice support/response와 Control Page start 경계 연속 분리

- `voice_audio_support_dependency_composition.py`
  - TTS warmup, voice timing, OmniVoice request/source dependency builder 4개를 하나의 audio-support root로 이동.
  - OmniVoice source의 request dependency factory는 composition 내부 method로 직접 연결.
  - Control Page composition 뒤에서 제공되는 startup component callback은 late-bound wiring으로 초기화 순서를 보존.
- `voice_response_dependency_composition.py`
  - voice response, main LLM, one-shot LLM, voice stream chunk dependency builder 4개를 하나의 response root로 이동.
  - LLM route composition 뒤에서 제공되는 route/model/search callback은 late-bound wiring으로 순환 초기화 없이 연결.
- Control Page server-start boundary:
  - 한 줄짜리 `build_control_page_server_start_runtime_deps` wrapper를 제거.
  - `ControlPageCompositionDeps.server_start`가 `control_page_http_composition.build_server_start_deps()`를 직접 late-bind하도록 단순화.
- 구조 결과:
  - 대상 builder 9개가 `main.py` top-level 함수에서 제거됨.
  - `main.py`: 3,899 lines, top-level functions 76개.
- 검증:
  - 각 신규 composition 경계 테스트와 기존 server-start runtime 테스트 통과.
  - 각 배치 후 실제 `main.py` control-page process smoke 통과.
  - `EVELYN_RUN_REAL_MAIN_INTEGRATION=1`, `PYTHONWARNINGS=error::ResourceWarning` 전체 discovery: 1,244 tests 통과.
  - 런타임/컨테이너 재시작 및 원격 push 없음.

## 2026-07-17 voice input/conversation policy/Discord app dependency 연속 분리

- `voice_input_support_dependency_composition.py`
  - STT text, STT transcription, Discord voice connection dependency builder 3개를 하나의 input-support root로 이동.
  - `process_member_audio`는 `VoiceIoComposition` 뒤에서 생성되므로 late-bound callback으로 연결.
- `conversation_policy_dependency_composition.py`
  - question policy, question-policy state, session turn, Discord session policy, response-output policy builder 5개를 통합.
  - conversation session 뒤에서 생성되는 snapshot callback은 late-bound wiring으로 초기화 순서를 보존.
  - 실제 main smoke가 기존 lazy builder 안에 숨어 있던 누락 symbol 2개를 검출해 공식 policy/output helper import를 추가.
- `discord_app_dependency_composition.py`
  - Discord text message handler와 command-session dependency builder를 통합.
  - `bot.user`는 시작 시점의 `None`을 고정하지 않고 builder 호출 시점에 resolve하도록 보존.
  - 기존 command-session builder의 미정의 `MAX_HISTORY`를 실제 설정 `MAX_HISTORY_ITEMS`로 교정.
- 구조 결과:
  - 대상 top-level builder 10개가 `main.py`에서 제거됨.
  - 명시적 typed wiring 증가로 `main.py`는 3,899→3,902 lines, top-level functions는 76→66개.
- 검증:
  - 세 신규 composition 경계 테스트 통과 및 builder dataclass 10종 materialization 확인.
  - 수정 완료 후 각 배치 실제 `main.py` control-page process smoke 통과.
  - `EVELYN_RUN_REAL_MAIN_INTEGRATION=1`, `PYTHONWARNINGS=error::ResourceWarning` 전체 discovery: 1,253 tests 통과.
  - 런타임/컨테이너 재시작 및 원격 push 없음.

## 2026-07-17 voice turn/LLM cognitive/search memory dependency 연속 분리

- `voice_turn_dependency_composition.py`
  - barge-in continuity, reply side effect/gate, ingress runtime/entrypoint builder 5개를 통합.
  - 뒤에서 생성되는 memory/search/voice worker callback은 late-bound wiring으로 연결.
- `llm_cognitive_dependency_composition.py`
  - cognitive follow-up, summary/router JSON LLM, LLM route, cognitive-state builder 5개를 통합.
  - summary/router의 중복 JSON request dependency 조립은 내부 공통 factory로 합침.
  - 뒤에서 생성되는 `fast_path_policy`, `ask_router_llm`, voice timing callback은 late-bound wiring으로 보존.
- `search_memory_dependency_composition.py`
  - memory update, search answer, search follow-up builder 3개를 통합.
  - 실제 main smoke에서 eager binding된 late route helper 3개를 검출해 호출 시점 wiring으로 수정.
- 구조 결과:
  - 대상 top-level builder 13개가 `main.py`에서 제거됨.
  - `main.py`: 3,902→3,884 lines, top-level functions 66→53개.
- 검증:
  - 세 신규 composition 경계 테스트 통과 및 builder dataclass 13종 materialization 확인.
  - 수정 완료 후 각 배치 실제 `main.py` control-page process smoke 통과.
  - `EVELYN_RUN_REAL_MAIN_INTEGRATION=1`, `PYTHONWARNINGS=error::ResourceWarning` 전체 discovery: 1,262 tests 통과.
  - 런타임/컨테이너 재시작 및 원격 push 없음.

## 2026-07-17 마지막 dependency builder function 제거

- Discord settings:
  - `build_discord_settings_runtime_deps` 한 줄 wrapper를 `functools.partial` binding으로 교체.
- Route executor:
  - `build_route_executor_runtime_deps` dataclass wrapper를 `ResolveRouteExecutorRuntimeDeps` partial binding으로 교체.
- Minecraft live observation:
  - `build_minecraft_live_observation_runtime_deps` dataclass wrapper를 `MinecraftLiveObservationRuntimeDeps` partial binding으로 교체.
- 구조 결과:
  - `main.py`의 `def build_*_deps(...)` 및 `def _build_*_deps(...)` 함수가 0개가 됨.
  - `main.py`: 3,884 lines 유지, top-level functions 53→50개.
- 검증:
  - 세 기존 runtime 테스트에 main partial binding 경계 검사를 추가해 통과.
  - 각 배치 실제 `main.py` control-page process smoke 통과.
  - `EVELYN_RUN_REAL_MAIN_INTEGRATION=1`, `PYTHONWARNINGS=error::ResourceWarning` 전체 discovery: 1,265 tests 통과.
  - Python compile, 중복 top-level 함수, 잔존 dependency builder 함수, replacement character를 재감사.
  - 런타임/컨테이너 재시작 및 원격 push 없음.
