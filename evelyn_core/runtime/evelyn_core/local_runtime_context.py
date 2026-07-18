from __future__ import annotations


def build_evelyn_runtime_dependency_context_from_payload(
    *,
    local_tts: dict[str, object],
    local_mic: dict[str, object],
    local_only_mode: bool,
    discord_enabled: bool,
    model_name: str,
    llm_server_url: str,
    router_model_name: str,
    summary_model_name: str,
    stt_model_name: str,
    stt_backend: str,
    omnivoice_server_url: str,
    omnivoice_voice: str | None,
    omnivoice_speed: float | str,
    voice_input_mode_status_line: str,
) -> str:
    output_mode = "local_speaker" if local_only_mode and local_tts.get("enabled") else (
        "discord_voice" if discord_enabled else "none"
    )
    lines = [
        "Evelyn dependency topology:",
        f"- self_runtime: main.py control/runtime process; local_only={local_only_mode}; discord_enabled={discord_enabled}.",
        f"- main_llm: {model_name}; endpoint={llm_server_url}; role=primary answer text generation.",
        f"- router_llm: {router_model_name}; role=route/cognitive policy before the main answer.",
        f"- summary_llm: {summary_model_name}; role=memory summaries/background consolidation.",
        f"- stt: {stt_model_name}; backend={stt_backend}; role=voice input -> transcript before main_llm.",
        f"- tts: OmniVoice endpoint={omnivoice_server_url}; voice={omnivoice_voice or 'auto'}; speed={omnivoice_speed}; role=text -> spoken audio after main_llm.",
        f"- voice_io: input_mode={voice_input_mode_status_line}; output_mode={output_mode}.",
        (
            "- local_mic: "
            f"enabled={local_mic.get('enabled')} capture_ready={local_mic.get('captureReady')} "
            f"device={local_mic.get('device')} segments={local_mic.get('segmentCount')} "
            f"last_error={local_mic.get('lastError') or 'none'}."
        ),
        (
            "- local_tts_output: "
            f"enabled={local_tts.get('enabled')} active={local_tts.get('active')} "
            f"device={local_tts.get('device') or 'default'} play_count={local_tts.get('playCount')} "
            f"last_error={local_tts.get('lastError') or 'none'}."
        ),
        "- minecraft_voyager: optional downstream game/autonomy service; use live runtime status before claiming current game state.",
        "- codex_gateway: optional external coding/control helper; if status says standby/not ready, say so instead of assuming it is available.",
        "Rule: when the user asks about your own state, dependencies, voice path, or local/Discord mode, answer from this runtime topology and status, not from persona guesses.",
    ]
    return "\n".join(lines)
