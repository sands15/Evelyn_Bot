# Dependency, Configuration, and Credential Hardening

Document status: **Current**
Last reviewed: 2026-07-29 KST

## Dependency compatibility result

### Python

| Runtime | Torch family | Transformers | Decision |
| --- | --- | --- | --- |
| Root/Windows and CI | `torch==2.13.0` | `4.57.6` | Torch finding removed from the root audit exception list |
| Discord image | `torch==2.13.0` | not installed directly | CPU runtime pinned to the patched Torch release |
| STT CUDA 12.8 image | `torch/torchaudio==2.11.0+cu128` | `4.57.6` | newest matched CUDA 12.8 family currently published |
| Vision CUDA 12.8 image | `torch==2.11.0+cu128`, `torchvision==0.26.0+cu128` | `5.14.1` | matched CUDA family and current Transformers |
| VoxCPM image | `torch/torchaudio==2.8.0` | image-owned | unchanged pending exact-latent and FlashAttention model smoke |

`qwen-asr==0.0.6` declares an exact dependency on
`transformers==4.57.6`. A dry resolver check rejects combining it with
Transformers 5.14.1. The STT service therefore keeps 4.57.6 until Qwen-ASR
publishes a compatible release. Vision is isolated and can use 5.14.1.

The official CUDA 12.8 index currently stops at Torch/Torchaudio 2.11 and
Torchvision 0.26. Root/CPU runtimes can use Torch 2.13, but CUDA services
cannot claim the same remediation until compatible CUDA wheels and model
smoke evidence exist.

Falcon-OCR still requires Hugging Face remote model code. The new
`VISION_TRUST_REMOTE_CODE=false` default applies to SmolVLM only; it is not a
claim that Falcon-OCR has been sandboxed or converted to native Transformers
code.

### Node/Minecraft

The root and Mindcraft manifests both pin the current public Mineflayer
`4.37.1`. No `overrides` or forced audit fix is used. The remaining
authentication-chain findings cannot be removed safely by overriding UUID or
Microsoft/Xbox authentication dependencies.

## Typed owner configuration

`runtime_config_schema.py` owns typed parsing and safe diagnostics for:

- STT
- Vision
- Codex Gateway
- Mindcraft

Invalid booleans, ports, numeric bounds, choices, paths, and URLs fall back to
the declared default and emit `invalid_value_defaulted` without including the
raw value. Public summaries expose field names and whether a secret was
configured, never the secret value.

This is an owner-by-owner migration. The large legacy `config.py` and
`main_runtime_config.py` surfaces remain compatible and should be migrated
only when their owning modules change.

## Exception observability

STT, Vision, Codex Gateway, and Mindcraft now expose the same process-lifetime
counter fields as the host voice owners. Runtime Health merges their HTTP
health payloads with the Host Supervisor, Local Bridge, and Discord heartbeat
sources.

Public summaries keep only:

- fixed error code
- exception class name
- timestamp
- per-code and total counts

Codex Gateway health also strips backend stdout, stderr, working directories,
and executable paths.

## Codex credential boundary

Compose no longer mounts the user's live `~/.codex/auth.json` or
`~/.codex/config.toml`.

The gateway now:

- accepts one dedicated read-only credential directory;
- copies only `auth.json` and optional `config.toml` into an ephemeral tmpfs;
- rejects `CODEX_HOME` alone as an authentication source;
- rejects unmarked non-empty target homes;
- runs with a read-only root filesystem, all Linux capabilities dropped, and
  `no-new-privileges`;
- disables the custom shell-command backend by default;
- confines request working directories to the configured gateway work root.

Provision the dedicated credential copy explicitly:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\evelyn_core\runtime\launchers\provision_codex_credentials.ps1
```

`config.toml` is not copied by default. Add `-IncludeConfig` only when the
gateway genuinely requires it.

The native Windows launcher creates a process-specific home under the system
temporary directory and removes it only when the Evelyn marker is present.
Compose uses an in-memory tmpfs home.

The dedicated `auth.json` is still a long-lived credential. The next stronger
boundary is a purpose-issued, short-lived token that can be revoked
independently from the user's interactive Codex session.

## Validation boundary

Completed locally:

- public package version and resolver checks for the changed Torch,
  Transformers, Qwen-ASR, and SpeechBrain compatibility boundary;
- Compose rendering and security-option validation;
- owner configuration, credential staging, privacy, observability, and
  existing runtime regression tests in the current official Evelyn images.

Still required before deployment:

- rebuild STT, Vision, Discord, and Codex Gateway images from the changed
  dependency files;
- GPU model-load smoke for Qwen3-ASR, SmolVLM2, and Falcon-OCR;
- one real Codex Gateway action using the dedicated credential copy;
- Minecraft Microsoft/Xbox login and in-game behavior smoke.
