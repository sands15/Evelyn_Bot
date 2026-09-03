# Evelyn - Local-First Assistant Runtime

Evelyn is a Windows-first, local-first personal assistant runtime. It connects
Discord text and voice, a consent-gated local microphone and speaker, locally
hosted language and speech services, human-readable long-term memory, a local
Control Page, and optional Minecraft automation.

This is an actively developed personal runtime, not a hosted service or a
turnkey package. Source implementation, automated verification, controlled-live
evidence, and production readiness are tracked separately.

Last reviewed: 2026-09-03.

## What It Does

- Accepts Discord text, Discord voice, and Windows local-microphone turns.
- Uses admission, consent, speaker, session, and current-owner checks before
  audio can enter the conversation or trigger an effect.
- Routes simple turns through lightweight policy paths and complex turns to the
  local main LLM; router and summary models are conditional.
- Uses Qwen3-ASR for local speech recognition and OmniVoice for spoken replies.
- Stores assistant long-term memory in a Markdown vault with provenance,
  principal isolation, correction, and deletion controls.
- Restores bounded recent conversation state after a process restart without
  automatically replaying ambiguous deliveries or external actions.
- Provides a local Control Page for chat, runtime health, voice controls, memory
  inspection, evidence review, and explicit preview/apply operations.
- Grounds screen questions in short-lived Windows evidence and refuses exact
  claims or UI actions when the evidence is stale, ambiguous, or incomplete.
- Supports bounded, approval-gated task work and optional Minecraft automation.

## Runtime Flow

```text
Discord text / Discord voice / local microphone
-> ingress, consent, owner, and replay gates
-> STT for audio
-> route and context policy
-> short-circuit / registered skill / bounded task / main LLM
-> Discord text and/or TTS delivery
-> memory write-behind and durable completion evidence
```

Routing happens before the main LLM call. The main LLM does not decide whether
the router, memory, runtime context, a tool, or TTS is needed for the turn.

## Repository Layout

- `main.py` - application wiring, Discord registration, and runtime entrypoint.
- `evelyn_core/runtime/evelyn_core/` - conversation, memory, task, voice,
  Control Page, recovery, authorization, and integration owners.
- `evelyn_voice/` - Discord voice receive, DAVE, RTP, Opus, and queue handling.
- `docker-compose.fast-control.yml` and `docker/` - local services and pinned
  image recipes for the Control Page, Bot API, models, voice, and integrations.
- `tools/` - source checks, bounded validation harnesses, and operational tools.
- `tests/` - grouped regression tests for core, runtime, voice, memory, UI,
  Discord, vision, tools, and Minecraft behavior.
- `docs/` - developer-facing Obsidian project knowledge and evidence. It is not
  Evelyn's runtime memory store.
- `external/` and `third_party/` - pinned or adapted optional integration code.

## Current Verification Status

The labels below are deliberate:

- **Source/offline** means current code and automated checks were exercised
  without starting the real external service or device path.
- **Controlled live** means a bounded real service, GPU, Discord registry, or
  isolated Minecraft scenario was exercised.
- **Production** means the normal end-to-end operating path has been validated;
  most rows below are not at that level yet.

| Area | Verified | Still pending or intentionally off |
| --- | --- | --- |
| Current source | On 2026-09-03 the current code completed 5,109 tests with 18 skips and no failures or errors using the CI discovery command. | This run started no Docker, Discord, microphone, speaker, model, or Minecraft services. |
| Conversation and recovery | Typed turn ownership, durable ingress/completion receipts, bounded restart continuity, cancellation, and privacy-safe runtime errors are covered by source/offline regressions. | A fresh full Discord text/voice plus local-microphone hardware E2E has not been run for the latest source. |
| Task work and feedback | `TaskWorkContract`, evidence-bound grounded drafts, the fixed 24-row evaluator, and the human correction -> independent guidance -> evaluation -> approval -> 10-case canary -> activation/rollback/revoke path pass source/offline checks. | Real Qwen baseline/candidate evaluation, Discord feedback interactions, a real 10-case canary, and production guidance activation remain live work. |
| Markdown memory | An isolated explicit user-confirmed memory passed save -> new-process scoped recall -> delete -> new-process non-recall. | Automatic daily/semantic memory recall remains fail-closed because its durable source does not yet preserve sufficient principal ownership and exact-turn deletion lineage. |
| Voice and STT | The revised STT image passed a bounded GPU1 old/new 2+20 overlap with error and cleanup gates; guided Discord capture passed transport/shape 10/10. | The private 50-item corpus, content/order gate, cancel/successor and cold-restart gates, real device/Gateway E2E, and promotion remain incomplete. P0-5 Qwen3.8 has not started. |
| Screen and UI action | The Host Vision Bridge, OCR/scene evidence contract, one-use preview/apply tokens, target re-observation, and fail-closed postconditions are source-tested. | A representative live accessibility corpus and real UI-action acceptance run remain pending. |
| Optional private archive | The 30-day archive, scoped access, retention, replica, deletion, and purge coordination exist and pass source/offline checks. | This is a separate opt-in feature and is default OFF. The latest host encryption preflight stopped before creating roots, keys, TLS material, or services; production archive E2E is not complete. |
| Discord command registry | A controlled-live run temporarily published five archive/feedback commands to one exact guild and restored only those exact IDs, leaving global and other-guild commands unchanged. | This does not prove Gateway interaction, ephemeral response, archive writes, or feedback workflow E2E. |
| Minecraft | Lease, readiness, authorization, recovery, and world-effect contracts are implemented; an isolated fresh-world shelter/restart scenario passed. | The operating bot is OFF. Normal Discord/lease/world-effect and voice E2E remain pending. |

The compact current snapshot is [`docs/01_NOW.md`](docs/01_NOW.md). Detailed
evidence and unresolved risks live in
[`docs/CURRENT_STATE.md`](docs/CURRENT_STATE.md) and
[`docs/ACTIVE_RISKS.md`](docs/ACTIVE_RISKS.md).

## Memory

The configured runtime Markdown vault is the durable source for Evelyn's
long-term memory. SQLite databases, search indexes, graph links, and hot-context
caches are rebuildable helpers. Runtime memory is separate from this
repository's developer-facing `docs/` vault.

Current memory behavior includes:

- explicit confirmation before durable personal facts are stored;
- room, person, and session ownership boundaries;
- source and evidence provenance;
- preview/apply correction and relationship changes;
- tombstone-first deletion, derived-memory revocation, and negative recall;
- fail-closed reads when ownership, integrity, or deletion currentness cannot be
  established.

The current mutation and deletion contract is
[`docs/MEMORY_PROVENANCE_DELETION_CONTRACT.md`](docs/MEMORY_PROVENANCE_DELETION_CONTRACT.md).
[`docs/EVELYN_MEMORY_VAULT_ARCHITECTURE.md`](docs/EVELYN_MEMORY_VAULT_ARCHITECTURE.md)
is target architecture and must not be read as proof of current behavior.

## Task Execution and External Effects

Longer work is bounded by an exact task contract, allowed tools, output limits,
deadlines, and a staged approval boundary. A generated draft is not marked
semantically verified by the model itself and remains subject to human review.

External effects use current owner/grant checks and typed receipts. Ambiguous
Discord sends, interrupted work, and crash-recovered actions are not
automatically replayed. High-impact actions use explicit preview/apply flows and
fail closed when currentness or the postcondition cannot be proven.

See:

- [`docs/AUTONOMY_AUTHORIZATION_CONTRACT.md`](docs/AUTONOMY_AUTHORIZATION_CONTRACT.md)
- [`docs/CONVERSATION_CONTINUITY_CONTRACT.md`](docs/CONVERSATION_CONTINUITY_CONTRACT.md)
- [`docs/CONVERSATION_INGRESS_RECOVERY_CONTRACT.md`](docs/CONVERSATION_INGRESS_RECOVERY_CONTRACT.md)
- [`docs/UI_ACTION_TARGET_CONTRACT.md`](docs/UI_ACTION_TARGET_CONTRACT.md)

## Voice and STT

Discord voice and the Windows local microphone share one fail-closed input-owner
lease. Local capture also requires explicit time-bounded consent, and high-impact
intents require a fresh wake even during a follow-up window. Qualified speech can
cancel active playback without allowing an older channel, listener generation,
or turn to resume work.

Speech recognition is provided by the local Qwen3-ASR service. Spoken delivery
uses OmniVoice with bounded fallback behavior inside the same playback owner.
The latest source and bounded GPU checks do not replace real microphone,
speaker, Discord Gateway, or end-to-end latency validation.

See:

- [`docs/LOCAL_VOICE_ADMISSION_CONTRACT.md`](docs/LOCAL_VOICE_ADMISSION_CONTRACT.md)
- [`docs/VOICE_CAPTURE_CONSENT.md`](docs/VOICE_CAPTURE_CONSENT.md)
- [`docs/KOREAN_ASR_TARGET_ARCHITECTURE.md`](docs/KOREAN_ASR_TARGET_ARCHITECTURE.md)
- [`docs/GPU1_CONCURRENCY_BENCHMARK.md`](docs/GPU1_CONCURRENCY_BENCHMARK.md)

## Local Screen Understanding

The Windows Host Supervisor owns the Host Vision Bridge. It captures one
ephemeral screen observation on demand, combines bounded foreground-window
metadata with the isolated Vision service and Windows OCR, and returns structured
evidence to the Docker Bot API. Screenshots and OCR tiles are deleted after the
request; durable status keeps only counters and evidence metadata.

Exact text requests fail closed when OCR is unscored or unreliable. See:

- [`docs/HOST_VISION_BRIDGE_CONTRACT.md`](docs/HOST_VISION_BRIDGE_CONTRACT.md)
- [`docs/VISION_EVIDENCE_CONTRACT.md`](docs/VISION_EVIDENCE_CONTRACT.md)

## Optional Minecraft Automation

Minecraft support is an optional branch, not a core readiness requirement. It
uses a single world-action lease, explicit grants, fresh readiness evidence,
bounded task contracts, durable audit status, and postcondition checks before an
action can be reported as successful.

The normal operating bot is kept off unless a live run is explicitly requested.
Current references are:

- [`CURRENT_EVELYN_ARCHITECTURE.md`](CURRENT_EVELYN_ARCHITECTURE.md)
- [`docs/MINECRAFT_AUTONOMY_READINESS_CONTRACT.md`](docs/MINECRAFT_AUTONOMY_READINESS_CONTRACT.md)
- [`docs/MINECRAFT_WORLD_ACTION_LEASE.md`](docs/MINECRAFT_WORLD_ACTION_LEASE.md)
- [`docs/MINDCRAFT_MIGRATION.md`](docs/MINDCRAFT_MIGRATION.md)

## Running

The maintained local launcher targets Windows with Git, PowerShell, Docker
Desktop/Compose, Python 3.11 for host and development checks, and configured
local model/GPU assets. Node 24 is used by the verification workflow. Review
[`docs/EVELYN_DOCKER_RUNTIME_QUICKSTART.md`](docs/EVELYN_DOCKER_RUNTIME_QUICKSTART.md)
before changing service profiles or model images.

On first Windows setup, create the isolated Host Supervisor runtime:

```powershell
powershell -ExecutionPolicy Bypass -File .\evelyn_core\runtime\launchers\bootstrap_host_runtime.ps1
```

This installs the small, locked host-only dependency set into `.venv-host`.
GPU model dependencies remain inside Docker services.

Create local environment files from `.env.example` as needed. Do not place
tokens in commands, tracked files, logs, or documentation. The local TTS service
also requires this private, untracked voice profile:

```text
omnivoice_profiles/evelyn/ref_audio.wav
omnivoice_profiles/evelyn/meta.json
```

`meta.json` must contain a non-empty `ref_text`. The launcher validates the
profile before starting Docker and never adds either file to source control.

The usual launcher is:

```bat
start.bat
```

The default profile starts the local Docker core and Windows I/O bridge with the
Discord Gateway and microphone capture disabled. Use `start.bat --lightweight`
to defer Vision OCR loading, and `stop_local.bat` to stop Evelyn-owned local
resources.

The launcher checks the Git source revision, required profiles, service health,
and Windows bridges. Except for the user-owned `docs/99_PROJECT_INBOX.md`, a
dirty or untracked source tree fails closed with `EVL-START-2001`.

If startup fails, `start.bat` keeps the error visible and prints a stable
`EVL-START-NNNN` code. The Korean cause/action table is in the
[`runtime quickstart`](docs/EVELYN_DOCKER_RUNTIME_QUICKSTART.md#시작-실패-오류코드).
The latest content-free failure record is written to
`runtime_artifacts/logs/background_start/startup-error.log`.

For non-interactive use, set `EVELYN_KEEP_CONSOLE_ON_EXIT=false` to skip the
failure pause without changing the exit code.

To rebuild the local application images before starting:

```powershell
$env:EVELYN_DOCKER_BUILD = "true"
powershell -ExecutionPolicy Bypass `
  -File .\evelyn_core\runtime\launchers\start_local_background.ps1
```

The build helper maps non-ASCII project paths through a temporary unused drive
letter and builds only the requested application image groups. It removes only
the mapping it created. Runtime defaults are in `evelyn_core/start_env.bat`.

## Checks

The full source/offline regression command is the same one used by CI:

```powershell
python -m unittest discover -s tests -t . -p "test_*.py"
```

The `-t .` is required: without it, `tests/tools` can shadow the repository's
top-level `tools` package and create false import errors.

Useful static checks:

```powershell
python -m pip check
python -m compileall -q main.py evelyn_core\runtime\evelyn_core
node --check docs\assets\evelyn-live2d.js
node --check docs\assets\evelyn-boot-progress.js
node --check docs\assets\evelyn-task-approval.js
node --check docs\assets\evelyn-conversation-archive-admin.js
git diff --check
```

After intentionally starting the local runtime, its separate live health check
is:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\check_docker_runtime.ps1
```

Add `-IncludeDiscordBot`, `-IncludeLocalBridge`, or `-IncludeMinecraftStack` only
for services that were explicitly started and are in the approved validation
scope.

## Documentation

Use the evidence layer that matches the question:

- [`docs/00_EVELYN_HOME.md`](docs/00_EVELYN_HOME.md) - project navigation.
- [`docs/01_NOW.md`](docs/01_NOW.md) - compact current focus and recent checks.
- [`docs/CURRENT_STATE.md`](docs/CURRENT_STATE.md) - source, deployment, and
  runtime evidence.
- [`docs/ACTIVE_RISKS.md`](docs/ACTIVE_RISKS.md) - unresolved gaps and next
  validation actions.
- [`CURRENT_EVELYN_PIPELINE.md`](CURRENT_EVELYN_PIPELINE.md) - current assistant
  pipeline ownership map; verify dated details against source.
- [`docs/DOCUMENTATION_INDEX.md`](docs/DOCUMENTATION_INDEX.md) - current,
  target, historical, and narrow-reference routing.
- [`docs/02_DECISIONS.md`](docs/02_DECISIONS.md) and `docs/worklog/` - durable
  decisions and dated verification records.
- [`plan.md`](plan.md) - approved priorities and completion gates.

Do not treat a target architecture, passing source test, built image, or service
health response as proof of a later live or production layer.

## Data and Safety Boundaries

- Do not commit credentials, `.env` files, private voice profiles, runtime
  memory, generated logs, private audio, screenshots, or runtime artifacts.
- Do not use the developer-facing `docs/` vault as Evelyn's runtime memory.
- Keep the private conversation archive OFF unless its separate purpose,
  encrypted host prerequisites, and live validation scope are explicitly
  approved.
- Do not start Discord, microphone, Minecraft, Docker, or model services merely
  to prove a source change.
- Do not claim production readiness from tests, image builds, bounded GPU runs,
  or isolated scenarios alone.
