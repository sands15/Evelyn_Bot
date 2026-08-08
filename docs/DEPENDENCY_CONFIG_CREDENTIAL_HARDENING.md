# Dependency, Configuration, and Credential Hardening

Document status: **Current**
Last reviewed: 2026-08-03 KST

## Dependency compatibility result

### Python

| Runtime | Torch family | Transformers | Decision |
| --- | --- | --- | --- |
| Root/Windows and CI | `torch==2.13.0` | `4.57.6` | Torch finding removed from the root audit exception list |
| Discord image | `torch==2.13.0` | not installed directly | CPU runtime pinned to the patched Torch release |
| STT CUDA 12.8 image | `torch/torchaudio==2.11.0+cu128` | `4.57.6` | newest matched CUDA 12.8 family currently published |
| Vision CUDA 12.8 image | `torch==2.11.0+cu128`, `torchvision==0.26.0+cu128` | `5.14.1` | matched CUDA family and current Transformers |
| OmniVoice image | `torch/torchaudio==2.8.0+cu128` | image-owned | default TTS; `omnivoice==0.1.5` and SHA-256-gated server source |
| VoxCPM compatibility image | `torch/torchaudio==2.8.0` | image-owned | opt-in diagnostics only; unchanged pending exact-latent and FlashAttention model smoke |

`qwen-asr==0.0.6` declares an exact dependency on
`transformers==4.57.6`. A dry resolver check rejects combining it with
Transformers 5.14.1. The STT service therefore keeps 4.57.6 until Qwen-ASR
publishes a compatible release. Vision is isolated and can use 5.14.1.

The official CUDA 12.8 index currently stops at Torch/Torchaudio 2.11 and
Torchvision 0.26. Root/CPU runtimes can use Torch 2.13, but CUDA services
cannot claim the same remediation until compatible CUDA wheels and model
smoke evidence exist.

OmniVoice does not install mutable server `main`. The image copies only the
external checkout's `omnivoice_server/` package through a named build context,
copies only four explicit Python globs, compares the exact 20-file set and validates every
SHA-256 from `docker/omnivoice_source.sha256`. Runtime dependency pins cover the
direct dependencies only. Startup also verifies all 13 required paths in the pinned model
snapshot against `docker/omnivoice_model.sha256`; transitive wheels and the CUDA base image
digest remain unlocked, so this is not described as a fully reproducible image.

The model cache mount is limited to `hub/`, read-only, and loaded offline. It must contain
revision `c5fdb5ccb189668d56333f77ba2629f4cd7535f4`; readiness also requires that exact
`model_revision` in health. The Evelyn clone profile remains read-only. A source or required
model revision change must be reviewed together with a new manifest and image fingerprint.

The local server patch removes synthesis text, filesystem paths and session/turn identifiers
from operational logs. Profile API and request-validation error responses do not echo input
text; it remains available only inside the server for synthesis. Default local synthesis uses sentence streaming.
Experimental blockwise generation remains disabled until a client disconnect reliably cancels
the in-flight model generation and releases its concurrency slot.

Compose sets TTS `pull_policy: never`. The path-safe local image builder builds a missing image
or an explicitly requested fresh image, including from a non-ASCII project path; Host Supervisor
repair only reuses an existing image. The VoxCPM service on host 8881 is an opt-in compatibility
and diagnostic target, not an automatic/runtime fallback, and existing clients are not rerouted
from `tts:8880` when it starts.

Falcon-OCR still requires Hugging Face remote model code. Evelyn now pins the
full upstream commit `42ec56b72a23984ac059e7c8a6d397a8529423fe` and verifies
the size and SHA-256 of every executable, configuration, tokenizer, and weight
file before `trust_remote_code=True` is reached. The runtime defaults to
`VISION_OCR_LOCAL_FILES_ONLY=true`; an absent, changed, or incomplete snapshot
fails closed with a content-free health code.

Provisioning is an explicit administrator operation outside the repository:

```powershell
python tools/provision_falcon_ocr_snapshot.py download `
  --cache-dir C:/Users/you/.cache/huggingface
python tools/provision_falcon_ocr_snapshot.py verify `
  --cache-dir C:/Users/you/.cache/huggingface
```

`--cache-dir` is the Hugging Face home directory; the provisioner and runtime
both use its `hub/` child so the downloaded and mounted snapshot are identical.

The Vision container runs as UID/GID 10001 with a read-only root filesystem,
all Linux capabilities dropped, `no-new-privileges`, a PID limit, ephemeral
write paths under `/tmp`, and the model cache mounted read-only. This narrows
the executable-code boundary. The model runtime is also attached only to the
Compose `vision_isolated` network (`internal: true`), which has no external
gateway. A separate UID 65534 ingress with no credentials, model cache, or
bind mounts is the only dual-network peer. It accepts a fixed method/path
allowlist and proxies only to `vision_runtime:8891`, preserving
`http://vision:8891` for Docker callers and `127.0.0.1:8891` for the host
without giving remote model code an egress route.

This does not convert Falcon-OCR to native Transformers code or provide a
kernel syscall sandbox. Compromised model code can still consume the Vision
runtime's allocated CPU/GPU/memory and attempt denial of service inside its
own isolated network, so bounded requests, responses, PIDs, and operator
timeouts remain required.

### Node/Minecraft

The root and Mindcraft manifests both pin the current public Mineflayer
`4.37.1`. No `overrides` or forced audit fix is used. The remaining
authentication-chain findings cannot be removed safely by overriding UUID or
Microsoft/Xbox authentication dependencies.

Mindcraft no longer loads both `mineflayer-pvp` and
`@nxg-org/mineflayer-custom-pvp`. The legacy plugin and its
`mineflayer-utils` dependency branch were removed; Mindcraft's existing
`bot.pvp.attack/stop` calls are bridged to the pinned custom plugin's
`bot.swordpvp` API. A build-time smoke verifies the plugin entry point and the
two compatibility methods before an image can be produced. This reduced the
Mindcraft production audit from 14 to 12 moderate findings while retaining
zero high or critical findings. The root production audit remains 8 moderate,
zero high, and zero critical findings.

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

Control Page의 legacy runtime service probe도 같은 경계를 사용한다.
`botApiError`, `botApiErrorKind`, `voyagerError`, `codexError`는 exact
allowlist 코드만 허용한다. 개별 TCP/HTTP/Voyager/Codex probe와 전체 refresh
예외는 exception message를 사용하지 않으며, 신뢰할 수 없는 upstream
`error`·login 문자열도 공개 payload로 복사하지 않는다. Codex Gateway의
허용된 credential/backend error code만 통과하고 나머지는
`codex_gateway_not_ready`로 정규화한다.

Codex 동작 준비 상태는 `/health`의 HTTP 생존 신호 `ok`와 분리된다.
Control Page는 `backendReady is true`일 때만 `codexReady=true`로 판정한다.
필드 누락, false, 비-boolean 값은 모두 fail-closed다.

Main/Voice LLM의 runtime status context에는
`runtime.recent-error.v1` marker만 들어간다.

```json
{
  "schema": "runtime.recent-error.v1",
  "owner": "codex_gateway|voyager|voyager_service|upstream_bridge",
  "code": "exact allowlisted code",
  "ageBucket": "lt_1m|lt_1h|lt_1d|gte_1d|unknown"
}
```

- Codex artifact의 phase와 오류 필드 존재 여부만 읽고 `error`,
  `stderr_tail`, `message` 본문은 prompt에 넣지 않는다.
- Voyager는 `last_error` 존재 여부 또는 allowlisted failure completion
  reason만 사용한다. 정상 completion의 critique는 오류가 아니다.
- error log는 크기가 0보다 큰지만 확인하고 tail 내용을 읽지 않는다.
- loader 결과를 신뢰하지 않고 최종 context builder가 schema와 세 enum을
  다시 검사하고, unknown/legacy 문자열 및 additive private 필드를 버린다.
- 렌더링은 `owner`, `code`, coarse age bucket만 포함하며 최대 3개다.

Autonomy의 실패 문맥은 별도 exact 계약만 사용한다.

```json
{
  "schema": "autonomy.failure.v1",
  "code": "autonomy_executor_observe_failed|autonomy_executor_execute_failed|autonomy_cycle_failed",
  "phase": "observe|execute|cycle",
  "domain": "assistant|minecraft|unknown",
  "action": "optional exact supported action",
  "verified": false
}
```

- exception message, exception repr, URL, 경로와 token-like 원문은 관찰,
  영속 상태, action audit, 알림과 사용자 status에 복사하지 않는다.
- action 실행 예외는 `failed/verified=false`이며 plan cursor를 전진시키지
  않는다.
- legacy state의 raw error는 load와 writer에서 정규화하고, Discord와
  Control Page가 마지막 출력 직전에 exact code allowlist를 다시 검사한다.

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

2026-07-30 source-mounted HTTP smoke:

- Codex Gateway started with the new source on the existing image and safely
  reported `backendReady=false`, `credentialMode=unconfigured`, and one fixed
  credential error when no dedicated credential copy was supplied.
- STT started with model loading disabled and reported owner configuration
  plus zero runtime errors.
- Vision started with both models disabled and reported owner configuration
  plus zero runtime errors.
- Control Page returned manifest `1.1` and
  `runtime_errors.summary.v1` with all seven owner sources. Its public privacy
  contract kept exception messages and filesystem paths disabled.

2026-07-31 runtime probe hardening:

- private token-like text, internal URL, Windows path를 넣은 개별 probe,
  전체 refresh와 untrusted Codex health 합성 테스트에서 공개 결과는 고정
  코드만 유지했다.
- 공식 Control Page/Discord 환경의 집중 41개, runtime 388개, UI 154개와
  core 468개를 실행했고 기능 assertion 실패는 0개였다. 이미지에 없는
  `git`으로 난 기존 core 검사 2개는 Windows에서 해당 모듈 13개를 통과했다.
- Control Page image `sha256:2a20b778b966e18930de96120146a08f1758b2f9b2c86fd74e8513b1181aaf0c`
  를 배포했고 healthy, restart count 0이다. Discord image
  `sha256:570dd9be4de3c89dc39c1bc0060fe3b89fc4c3dd5cda3d7e7141d652b83793f5`
  는 빌드·검증만 하고 시작하지 않았다.

2026-07-31 runtime context hardening:

- artifact/log와 dependency 주입값에 token-like text, 내부 URL, Windows
  path를 넣어도 LLM context에는 exact marker만 남는 집중 테스트 22개를
  통과했다.
- runtime 전체 393개는 통과(skip 2)했고 core 468개는 기능 assertion 실패
  0개였다. 이미지에 없는 `git` 오류 2개는 Windows의 해당 모듈 13개로
  보완했다.
- Discord/Main image
  `sha256:1ad4935410afec659a1862e11d3950c3657d379618bb29d3190cde7f58cc69b9`
  는 내부 `compileall`, `pip check`와 집중 테스트를 통과했다. 실제
  Discord/Main 서비스는 시작하지 않았다.

2026-07-31 autonomy failure hardening:

- token-like 문자열, 내부 URL과 Windows path를 관찰·실행·cycle 예외와
  legacy state에 주입한 집중 테스트 78개에서 고정 marker만 남았다.
- Discord I/O 99개, runtime 393개(skip 2), UI 154개(skip 7)를 통과했다.
  core 476개는 기능 assertion 실패 0개였고 이미지에 없는 `git` 오류 2개와
  Windows 전용 OCR은 Windows 모듈 19개로 보완했다.
- Discord/Main image
  `sha256:f0d82b867babaeb5ad4731116fa90c4ae91e30630dfc6ca6e64bca36506c83b9`
  는 내부 `compileall`, `pip check`, 계약 import와 집중 테스트 78개를
  통과했다. 실제 Discord/Main 서비스는 시작하지 않았다.

Still required before deployment:

- rebuild STT, Vision, and Codex Gateway images from the changed
  dependency files; this requires explicit approval for the repository's
  service dependency manifests to be queried against public package
  registries;
- GPU model-load smoke for Qwen3-ASR, SmolVLM2, and Falcon-OCR;
- one real Codex Gateway action using the dedicated credential copy;
- Minecraft Microsoft/Xbox login and in-game behavior smoke.
