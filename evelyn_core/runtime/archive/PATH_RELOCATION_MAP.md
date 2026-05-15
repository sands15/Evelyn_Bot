# Path Relocation Map for `evelyn_core`

Last updated: 2026-05-15

This file documents the structural move that changed `C:\Evelyn\evelyn_core` from a mixed source/launcher root into:

- root `.bat` launchers
- `runtime\evelyn_core\` for source
- `runtime\launchers\` for non-`.bat` helper launchers
- `runtime\archive\` for cache and retired helpers

## Core source relocations

| Old path | New path |
|---|---|
| `evelyn_core\audio.py` | `evelyn_core\runtime\evelyn_core\audio.py` |
| `evelyn_core\autonomy.py` | `evelyn_core\runtime\evelyn_core\autonomy.py` |
| `evelyn_core\autonomy_router.py` | `evelyn_core\runtime\evelyn_core\autonomy_router.py` |
| `evelyn_core\codex_gateway_client.py` | `evelyn_core\runtime\evelyn_core\codex_gateway_client.py` |
| `evelyn_core\codex_gateway_server.py` | `evelyn_core\runtime\evelyn_core\codex_gateway_server.py` |
| `evelyn_core\config.py` | `evelyn_core\runtime\evelyn_core\config.py` |
| `evelyn_core\local_runtime.py` | `evelyn_core\runtime\evelyn_core\local_runtime.py` |
| `evelyn_core\memory.py` | `evelyn_core\runtime\evelyn_core\memory.py` |
| `evelyn_core\minecraft_autonomy_client.py` | `evelyn_core\runtime\evelyn_core\minecraft_autonomy_client.py` |
| `evelyn_core\minecraft_hostile_threat.js` | `evelyn_core\runtime\evelyn_core\minecraft_hostile_threat.js` |
| `evelyn_core\minecraft_threat.py` | `evelyn_core\runtime\evelyn_core\minecraft_threat.py` |
| `evelyn_core\paths.py` | `evelyn_core\runtime\evelyn_core\paths.py` |
| `evelyn_core\text.py` | `evelyn_core\runtime\evelyn_core\text.py` |
| `evelyn_core\upstream_voyager_runner.py` | `evelyn_core\runtime\evelyn_core\upstream_voyager_runner.py` |
| `evelyn_core\voice_llm_orchestration.py` | `evelyn_core\runtime\evelyn_core\voice_llm_orchestration.py` |
| `evelyn_core\voice_orchestration.py` | `evelyn_core\runtime\evelyn_core\voice_orchestration.py` |
| `evelyn_core\voice_pipeline.py` | `evelyn_core\runtime\evelyn_core\voice_pipeline.py` |
| `evelyn_core\voyager_service.py` | `evelyn_core\runtime\evelyn_core\voyager_service.py` |

## Package directory relocations

| Old path | New path |
|---|---|
| `evelyn_core\skills\` | `evelyn_core\runtime\evelyn_core\skills\` |
| `evelyn_core\tools\` | `evelyn_core\runtime\evelyn_core\tools\` |

## Active launcher helper relocations

| Old path | New path |
|---|---|
| `evelyn_core\start_codex_gateway.ps1` | `evelyn_core\runtime\launchers\start_codex_gateway.ps1` |
| `evelyn_core\start_voyager_service.ps1` | `evelyn_core\runtime\launchers\start_voyager_service.ps1` |
| `evelyn_core\start_voyager_task.ps1` | `evelyn_core\runtime\launchers\start_voyager_task.ps1` |
| `evelyn_core\start_bot.ps1` | `evelyn_core\runtime\launchers\start_bot.ps1` |
| `evelyn_core\start_tts.ps1` | `evelyn_core\runtime\launchers\start_tts.ps1` |
| `evelyn_core\supervise_service.ps1` | `evelyn_core\runtime\launchers\supervise_service.ps1` |
| `evelyn_core\run_main_llm.sh` | `evelyn_core\runtime\launchers\run_main_llm.sh` |
| `evelyn_core\run_router_llm.sh` | `evelyn_core\runtime\launchers\run_router_llm.sh` |
| `evelyn_core\run_sub_llm.sh` | `evelyn_core\runtime\launchers\run_sub_llm.sh` |

## What stayed at the root

These remain directly under `evelyn_core\`:

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
- `runtime\`

## Path handling changes applied

To support the new structure:

- `main.py` now injects `C:\Evelyn\evelyn_core\runtime` into `sys.path`
- launcher environment now exports `EVELYN_PROJECT_ROOT`, `EVELYN_CORE_ROOT`, and `EVELYN_CORE_RUNTIME`
- Python modules that used `Path(__file__)` for repo-root assumptions now resolve through `evelyn_core.paths.get_repo_root()`
- launcher `.bat` files that call `.ps1`/`.sh` helpers now point into `runtime\launchers\`

## Important implication

Any future code or docs that refer to old source paths like `evelyn_core\voyager_service.py` should be updated to the new runtime path:

- `evelyn_core\runtime\evelyn_core\voyager_service.py`
