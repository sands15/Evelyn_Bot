# Evelyn Control Page Mode Target

## Goal

Keep one stable three-column control page frame, but make the page read as an operator dashboard by default and only turn into a Minecraft mission dashboard when a live Minecraft session is actually active.

## Required Shape

- keep the existing three-slot desktop frame:
  - left = state/context slot
  - center = current focus slot
  - right = actions/tools slot
- keep shared chrome stable across modes:
  - avatar
  - chat thread
  - composer
  - outer frame
- swap slot content and slot language by mode instead of swapping the full page

## Mode Rules

### Default Mode

- page should read as Evelyn operations, not Minecraft standby
- surface:
  - current voice/runtime state
  - focus summary
  - recommended next action
  - latest response / latest user context
  - current issue or wait reason when relevant

### Minecraft Mode

- page should read as live mission control
- surface:
  - goal
  - task and stage
  - position / progress
  - inventory and survival state
  - recent activity

### Warmup / Offline / Issue States

- warmup stays in default mode until a real Minecraft session is live
- offline, stale, and issue states should be visible as operating states, not hidden inside generic copy
- mode labels, pills, and panel headings should reflect these states directly

## Frontend Implementation Rules

- backend remains the source of truth for `ui.mode`, `ui.submode`, and `ui.reason`
- frontend should use class/data-attribute mode toggles instead of ad-hoc inline visibility logic
- headings, pills, and summary copy should switch with the mode so default mode does not look Minecraft-branded
