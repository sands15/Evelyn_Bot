# Korean STT Scoreboard Target

Target shape for the Korean recognition test harness:

- One visible CMD session controls the full loop.
- Each sample uses one microphone recording as the shared audio input.
- The user types the gold Korean sentence in the same CMD session.
- The harness runs both recognizers on the same saved WAV:
  - current Evelyn STT: `Qwen/Qwen3-ASR-1.7B`
  - ciocan Gemma 4 E4B GPTQ W4A16: `ciocan/gemma-4-E4B-it-W4A16`
- In one scoreboard session, each recognizer should load once and then reuse the resident model for subsequent samples.
- Korean scoring reports spacing-tolerant character accuracy as the primary score, with spacing-sensitive word accuracy as a secondary score.
- Every trial is saved as WAV plus JSONL so later aggregate scoring can reuse the same inputs.

Operational constraints:

- Keep GPU visibility stable before importing Torch. Default to `CUDA_VISIBLE_DEVICES=0`, matching the already validated 3090 path.
- Run the ciocan Gemma 4 E4B GPTQ W4A16 recognizer through the validated WSL overlay path by default, because the Windows probe environment cannot load this GPTQ build without `gptqmodel`.
- Keep the ciocan model behind a persistent stdin/stdout worker so repeated trials do not pay the model-load cost.
- Do not require the Evelyn bot or Discord voice pipeline to be running.
- Do not restart any Evelyn service.
