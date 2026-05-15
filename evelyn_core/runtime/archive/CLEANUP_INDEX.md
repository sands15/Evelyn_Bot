# evelyn_core Cleanup Index

Last updated: 2026-05-15

This archive note records the structural cleanup applied to `C:\Evelyn\evelyn_core`.

## Current root rule

`C:\Evelyn\evelyn_core` now keeps:

- root `.bat` launcher files only
- one runtime folder: `runtime\`

## Runtime layout

- `runtime\evelyn_core\`
  - Python / JS source files
  - `skills\`
  - `tools\`
- `runtime\launchers\`
  - non-`.bat` launcher helpers (`.ps1`, `.sh`)
- `runtime\archive\`
  - caches, retired helpers, cleanup notes, relocation docs

## Root .bat launchers kept in place

- `start.bat`
- `start_bot.bat`
- `start_codex_gateway.bat`
- `start_env.bat`
- `start_main_llm.bat`
- `start_router_llm.bat`
- `start_sub_llm.bat`
- `start_tts.bat`
- `start_voyager.bat`
- `start_voyager_service.bat`

## Active non-.bat launcher helpers moved to runtime\launchers

- `run_main_llm.sh`
- `run_router_llm.sh`
- `run_sub_llm.sh`
- `start_bot.ps1`
- `start_codex_gateway.ps1`
- `start_tts.ps1`
- `start_voyager_service.ps1`
- `start_voyager_task.ps1`
- `supervise_service.ps1`

## Source moved to runtime\evelyn_core

This includes former root files such as:

- `__init__.py`
- `audio.py`
- `autonomy.py`
- `config.py`
- `memory.py`
- `minecraft_autonomy_client.py`
- `paths.py`
- `upstream_voyager_runner.py`
- `voyager_service.py`
- other core `.py` / `.js` files

Plus package directories:

- `skills\`
- `tools\`

## Archived material

- cache files moved under `runtime\archive\cache\`
- older helpers retained under `runtime\archive\legacy_launchers\`
- path relocation note stored in `runtime\archive\PATH_RELOCATION_MAP.md`

## Intent

This structure keeps the visible launcher surface simple while preserving:

- a single runtime subtree for source and helper scripts
- reversible archive history
- explicit path documentation for future maintenance
