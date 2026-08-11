# Evelyn Bot

Evelyn is a local-first Discord voice bot focused on natural conversation,
voice interaction, memory, and optional Minecraft automation.

It is built as a personal runtime rather than a hosted service. Most heavy
components run locally: speech recognition, LLMs, TTS, memory indexing, and the
control page.

## What It Does

- Listens and responds in Discord voice channels.
- Routes simple turns through lightweight paths and complex turns through a
  local main LLM.
- Uses local TTS for spoken replies.
- Keeps long-term project memory in a human-readable Markdown vault.
- Provides a local control page for runtime state and memory graph inspection.
- Grounds local screen questions in ephemeral Windows evidence and refuses
  exact screen claims when that evidence is not actionable.
- Can connect to a Minecraft/Voyager automation stack when needed.

## Main Parts

- `main.py` - Discord bot entrypoint and voice/conversation orchestration.
- `evelyn_voice/` - custom Discord voice receive client.
- `evelyn_core/runtime/evelyn_core/` - runtime modules for config, memory,
  voice routing, control page, and integrations.
- `docs/` - architecture notes and design documents.
- `tests/` - grouped regression tests for routing, memory, voice, vision, and runtime behavior.

## Memory

Evelyn's long-term memory is being moved into an Obsidian-style Markdown vault.
The Markdown files are the durable source of truth, while SQLite, search indexes,
graph links, and hot-context caches are rebuildable runtime helpers.
The local Control Page exposes evidence-only provenance audits and explicit
preview/apply flows for source backfill, relink, unlink, and latest-change undo.
These flows preserve note content, reject stale graph state, and keep
content-free recovery journals.

The current architecture and mutation contract are:

- `docs/EVELYN_MEMORY_VAULT_ARCHITECTURE.md`
- `docs/MEMORY_PROVENANCE_DELETION_CONTRACT.md`

## Control Page

The control page is a local dashboard for checking the bot, voice state, runtime
status, and memory graph. It is meant for local operation and debugging, not as a
public web app.

## Local Screen Understanding

The Windows Host Supervisor owns a narrow Host Vision Bridge. It captures one
ephemeral screen observation on demand, combines bounded foreground-window
metadata with the local Vision service and Windows OCR, and returns structured
evidence to the Docker Bot API. Screenshots and OCR tiles are deleted after the
request; status files keep only counters and evidence metadata.

Exact text requests fail closed when OCR is unscored or unreliable. See:

- `docs/HOST_VISION_BRIDGE_CONTRACT.md`
- `docs/VISION_EVIDENCE_CONTRACT.md`

## Running

On a first Windows setup, create the isolated Host Supervisor runtime:

```powershell
powershell -ExecutionPolicy Bypass -File .\evelyn_core\runtime\launchers\bootstrap_host_runtime.ps1
```

This installs the small, locked host-only dependency set into `.venv-host`.
GPU model dependencies remain inside the Docker services.

The local TTS service also requires the private, untracked voice profile:

```text
omnivoice_profiles/evelyn/ref_audio.wav
omnivoice_profiles/evelyn/meta.json
```

`meta.json` must contain a non-empty `ref_text`. The launcher validates this
profile before starting Docker and does not add either file to source control.

The usual launcher is:

```bat
start.bat
```

### Startup error codes

If startup fails, `start.bat` keeps the error visible and prints a stable
`EVL-START-NNNN` code. The Korean cause/action table is in
[`docs/EVELYN_DOCKER_RUNTIME_QUICKSTART.md`](docs/EVELYN_DOCKER_RUNTIME_QUICKSTART.md#시작-실패-오류코드).
The latest content-free failure record is written to
`runtime_artifacts/logs/background_start/startup-error.log`.
The user-owned `docs/99_PROJECT_INBOX.md` capture file is excluded from the
source-cleanliness check; other tracked or untracked changes still produce
`EVL-START-2001`.

For non-interactive use, set `EVELYN_KEEP_CONSOLE_ON_EXIT=false` to skip the
failure pause. This does not change the process exit code.

To rebuild the local app images before starting:

```powershell
$env:EVELYN_DOCKER_BUILD = "true"
powershell -ExecutionPolicy Bypass `
  -File .\evelyn_core\runtime\launchers\start_local_background.ps1
```

The build helper uses a temporary unused drive letter when the project path
contains non-ASCII characters, avoiding the Docker Buildx session-key failure
seen with direct builds from such paths. It builds only the Bot API, Control
Page, and Vision images and removes only the mapping it created.

Runtime defaults are configured through:

```text
evelyn_core/start_env.bat
```

Create local environment files from `.env.example` when needed. Do not commit
private tokens, generated logs, local memory, or runtime state.

## Checks

Useful development checks:

```powershell
python -m unittest discover -s tests
python tests\memory\test_memory_vault.py
python tests\core\test_dialogue_turn_classifier.py
python -m py_compile main.py evelyn_core\runtime\evelyn_core\config.py
node --check docs\assets\evelyn-live2d.js
node --check docs\assets\evelyn-boot-progress.js
git diff --check
```

## Notes

This repository contains active experimental work. Some runtime components are
specific to the local machine setup, especially model launchers, voice profiles,
and Minecraft automation services.
