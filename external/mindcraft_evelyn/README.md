# Evelyn Mindcraft integration

This directory overlays the pinned `external/mindcraft` submodule without modifying upstream history.

- Upstream: `mindcraft-bots/mindcraft` `v0.1.4`
- Pinned commit: `b36eaf7e61b3f6bd031fdb531812b2e3c42b6c73`
- Runtime: Minecraft Java 1.21.11, Microsoft authentication, non-operator survival
- Policy: arbitrary code generation disabled; slash commands blocked at the Mineflayer boundary
- Planner: local Qwen by default; the Codex adapter is disabled before token or network access

The Docker build copies upstream, applies `evelyn.patch`, and then copies the new Evelyn model,
profile, and telemetry plugin. Do not patch the submodule directly.

The separate `codex-gateway` Compose profile remains fail-closed until the
pinned image's effective tool registry is verified; normal Minecraft startup
does not start or depend on it.
