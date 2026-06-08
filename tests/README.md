# Evelyn Tests

Tests are grouped by runtime area so the folder stays readable.

## Layout

- `core/` - routing, context policy, turn lifecycle, and general text policies
- `discord_io/` - Discord ingress, delivery, and session gating
- `memory/` - memory vault, writebehind, identity review, and proactive questions
- `voice/` - STT, local mic, TTS playback, and voice turn orchestration
- `vision/` - vision context and OCR lifecycle tests
- `minecraft/` - Minecraft runtime snapshot tests
- `voyager/` - Voyager status and boundary tests
- `runtime/` - shutdown and runtime artifact retention tests
- `ui/` - control page and URL helpers
- `hygiene/` - source hygiene checks
- `fixtures/` - shared test data

## Commands

Run everything:

```powershell
python -m unittest discover -s tests
```

Run one area:

```powershell
python -m unittest discover -s tests\memory
python -m unittest discover -s tests\voice
```

Run one file:

```powershell
python tests\memory\test_proactive_questions.py
```
