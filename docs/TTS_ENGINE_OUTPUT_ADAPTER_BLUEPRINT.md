# Evelyn TTS Engine / Output Adapter Blueprint

Date: 2026-06-09
Status: temporary architecture note, no implementation yet

## Why This Exists

The local speaker TTS path can feel slower and can stutter near the end of long
answers, while the Discord path is more stable. The observed difference is not
just "Discord versus local speaker"; it is mostly the shape of the pipeline.

Current local speaker behavior is closer to:

```text
LLM full answer
-> one full-answer TTS request
-> local sounddevice playback
```

Current Discord behavior is closer to:

```text
LLM streaming
-> sentence / phrase chunks
-> TTS prefetch queue
-> queued audio source
-> Discord voice playback
```

The next design should not hard-code this as two unrelated TTS systems. It
should split the voice path into a shared synthesis pipeline plus small output
adapters.

## Target Shape

```text
LLM stream
-> spoken text sanitizer
-> speech chunker
-> TTS engine adapter
   - OmniVoice
   - Higgs Audio v3
   - future engines
-> internal audio queue
-> output adapter
   - Discord voice
   - local speaker
   - future outputs
```

The important boundary is:

```text
TTS engine creates audio.
Output adapter plays audio.
```

The application should not care whether the engine is OmniVoice, Higgs Audio v3,
or another backend, and the TTS engine should not care whether the output is
Discord or the local speaker.

## Internal Audio Contract

Use one internal playback format even if backend APIs differ.

Recommended first contract:

```text
sample_rate: 24000
channels: 1
sample_width: 16-bit
encoding: signed little-endian PCM
read_size: output-adapter controlled
```

Every engine adapter should normalize its output to this contract before putting
audio into the shared queue.

If an engine returns WAV, MP3, Opus, or codec tokens, the adapter owns decoding
or conversion. Discord/local playback code should only see the internal queue.

## TTS Engine Adapter Contract

The engine interface should be small and streaming-oriented.

Example shape:

```python
class TtsEngineAdapter:
    name: str

    async def health(self) -> TtsHealth:
        ...

    async def synthesize_stream(
        self,
        chunks: AsyncIterator[SpeechChunk],
        options: TtsOptions,
        cancellation: TurnCancellation,
    ) -> AsyncIterator[AudioChunk]:
        ...
```

The adapter may internally use:

- one HTTP request per chunk
- HTTP streaming per chunk
- a persistent WebSocket
- a hosted API
- local model inference

The rest of Evelyn should not need to know which one it is.

## Output Adapter Contract

The output adapter owns device-specific playback and cancellation.

Example shape:

```python
class AudioOutputAdapter:
    name: str

    async def play(
        self,
        audio: AsyncIterator[AudioChunk],
        context: PlaybackContext,
    ) -> PlaybackResult:
        ...
```

Discord-specific work belongs in the Discord adapter:

- Discord `AudioSource`
- guild / channel state
- voice client reconnect handling
- Discord-specific silence padding if needed

Local-specific work belongs in the local adapter:

- `sounddevice.RawOutputStream`
- local device selection
- local underrun handling
- local first-sound / drain metrics

Both adapters should consume the same queue contract.

## OmniVoice Phase

The immediate fix should keep OmniVoice and change the local speaker route to use
the same streaming / chunk / prefetch structure as Discord.

Target:

```text
LLM streaming
-> speech chunker
-> OmniVoice chunk source prefetch
-> queued audio source
-> local speaker output adapter
```

Keep the existing full-answer local TTS function as a fallback for short
deterministic answers, startup messages, or failure recovery.

Acceptance criteria:

- Local first sound starts before the full LLM answer is complete.
- Local speaker and Discord share the same chunking and prefetch rules.
- Local playback does not starve when later OmniVoice chunks arrive slowly.
- Discord behavior is not regressed.
- Turn trace records first LLM token, first TTS PCM, first playback write, queue
  gaps, underrun count, and cancellation.

## Higgs Audio v3 Future Phase

Higgs Audio v3 should be introduced as another engine adapter, not as a rewrite
of Discord/local playback.

Public references checked on 2026-06-09:

- Boson AI describes Higgs Audio v3 TTS as a voice-chat-oriented TTS model with
  blocking and streaming generation via API, inline controls, zero-shot voice
  cloning, and local weights.
- The Hugging Face model card describes the model as a 24 kHz TTS model using an
  autoregressive decoder and audio tokens at 25 fps.
- SGLang-Omni and vLLM-Omni both expose Higgs Audio v3 serving paths.

Adapter implication:

```text
HiggsAudioV3EngineAdapter
-> accepts SpeechChunk stream
-> maps Evelyn options to Higgs controls
-> returns normalized 24 kHz mono PCM chunks
-> preserves the same output adapters
```

Do not let Higgs-specific controls leak into the whole app. Map them through
generic fields first:

```text
voice
reference_audio
reference_text
language
speed
pitch
emotion
style
pause
format
```

Engine-specific extras can live in an `engine_options` dict.

## Configuration

Add a single engine selector later:

```text
TTS_ENGINE=omnivoice
TTS_ENGINE=higgs_audio_v3
```

Optional fallback:

```text
TTS_FALLBACK_ENGINE=omnivoice
```

Output selection remains separate:

```text
TTS_OUTPUT=discord
TTS_OUTPUT=local_speaker
```

Do not combine these into values such as `discord_omnivoice` or
`local_higgs`. That would recreate the coupling this blueprint is trying to
remove.

## Metrics To Preserve

Every engine and output pair should report comparable metrics:

- `llm_first_token_ms`
- `speech_first_chunk_ms`
- `tts_first_pcm_ms`
- `playback_first_write_ms`
- `first_sound_estimate_ms`
- `audio_queue_depth_max`
- `audio_queue_gap_ms_p95`
- `underrun_count`
- `cancelled`
- `fallback_engine_used`
- `engine_name`
- `output_name`

The goal is to compare OmniVoice and Higgs Audio v3 with the same ruler.

## Failure Rules

- If the selected engine fails health check, use the fallback engine when
  configured.
- If streaming synthesis fails mid-turn, cancel the active output cleanly and
  optionally play a short fallback phrase through the fallback engine.
- If local playback underruns, record it and continue if more audio is expected.
- If the user interrupts, cancellation must stop LLM chunk delivery, TTS
  synthesis tasks, queued audio, and output playback.
- Late chunks from a cancelled turn must be ignored by turn id.

## Implementation Order

1. Document and preserve the current Discord streaming behavior.
2. Extract the shared speech chunk / TTS prefetch path behind a small facade.
3. Add a local speaker output adapter that consumes the same queued audio source.
4. Route local speaker turns through the shared streaming path.
5. Keep full-answer local TTS as fallback.
6. Add metrics for first sound and underruns.
7. Only after the OmniVoice path is stable, add a `TtsEngineAdapter` interface.
8. Add `OmniVoiceEngineAdapter` behind that interface.
9. Later, add `HiggsAudioV3EngineAdapter` without touching output adapters.

## Non-Goals

- Do not replace OmniVoice during the local stutter fix.
- Do not introduce Higgs Audio v3 until VRAM, serving stack, and license status
  are explicitly accepted.
- Do not create separate duplicated pipelines named "Discord TTS" and
  "Local TTS".
- Do not restart Evelyn as part of this document-only step.

## Licensing Note

Higgs Audio v3 public model-card text currently indicates a research and
non-commercial license. Any production, hosted, revenue-generating, or public
service usage needs a separate license check before integration.

