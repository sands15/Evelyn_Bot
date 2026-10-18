# Evelyn Tests

## Automatic verification evidence

Existing unittest module/discovery commands, the direct shutdown-script test and
the six direct Minecraft entrypoints automatically retain a new receipt for each
run, including nested runs. `EVELYN_TEST_ATTEMPT_ROOT` relocates the default
`runtime_artifacts/validation/test_attempts/` output; it does not enable capture.
Failure events are flushed immediately. Retries never overwrite earlier files.
Only error types, source-relative frames, OS error numbers and selected source
hashes are kept, not messages, stdout, locals, parameters or environment contents.
Hashes cover selected Python test modules and the recorder, not every dependency.
Storage failures and stopped selections cannot pass. Native skip/expected-failure
exit behavior stays unchanged, with an `INCOMPLETE` receipt instead of `PASS`.

The existing Verify Evelyn workflow preserves only redacted result/event files,
using run ID plus attempt number and `overwrite: false`. The normal CI includes
the independent Minecraft oracle checks without an opt-in. The optional manual
`prove_safeguards` input runs the bounded safeguard checks, then deliberately
injects a wrong Java product value and the same wrong old expected value. This
run **must fail**, including on a rerun; normal values are checked first. This
explicit negative proof skips the unrelated full-product regression job, not any
of the safeguard checks. It is not a full-product acceptance run.

The pinned menu fixture is `minecraft_direct/fixtures/menu_properties_26_3.json`.
The real Java `MenuPropertiesTest` and existing `test_java_transport.py` gate
compare against independent official facts; missing/duplicate reports fail closed.
Use the existing transport command with `--java-home` and `--gson` arguments.
Hard kills, runner loss before upload, arbitrary custom runners and unrelated
standalone scripts are not intercepted. GitHub artifact retention still applies;
this is test wiring, not a runtime service or a globally installed Python hook.

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
