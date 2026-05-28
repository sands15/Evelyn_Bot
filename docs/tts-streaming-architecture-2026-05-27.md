# TTS Streaming Architecture - 2026-05-27

## Target shape

Evelyn should keep voice response latency low without letting partially generated audio starve Discord playback.

The live strategy is:

- Use `blockwise_capped_first` for TTS chunks so the OmniVoice backend emits a short first cap and then commits audio with one-block lookahead.
- Do not start Discord playback on the first tiny PCM slice alone. Hold a small start jitter buffer before handing the source to Discord playback.
- Keep TTS prefetch enabled so follow-up synthesis runs while the current chunk is playing.
- Do not treat "first PCM arrived" as enough evidence that the audio source can sustain Discord's fixed 20 ms frame pull.

## Why

Discord audio sources are pulled in fixed 20 ms frames. If a blockwise OmniVoice stream has delivered only the first cap but stalls while generating the next committed block, playback can underrun. That sounds like the middle of the sentence cutting out.

The correct shape is an explicit committed-block contract between the TTS backend and playback layer:

- The backend emits only stable committed audio after the first cap.
- The bot starts playback only after a minimum start buffer is available.
- Later backend stalls should be absorbed by the buffer instead of leaking into Discord playback.
