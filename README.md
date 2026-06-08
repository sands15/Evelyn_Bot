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

The main design note is:

- `docs/EVELYN_MEMORY_VAULT_ARCHITECTURE.md`

## Control Page

The control page is a local dashboard for checking the bot, voice state, runtime
status, and memory graph. It is meant for local operation and debugging, not as a
public web app.

## Running

The usual launcher is:

```bat
start.bat
```

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
node --check docs\assets\evelyn-page.js
git diff --check
```

## Notes

This repository contains active experimental work. Some runtime components are
specific to the local machine setup, especially model launchers, voice profiles,
and Minecraft automation services.
