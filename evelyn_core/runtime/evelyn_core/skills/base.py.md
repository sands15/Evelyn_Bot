# `base.py` Structure

## Role
Defines the foundational data contracts for Evelyn skills.

## Core Types
### `SkillContext`
Carries normalized execution context into a skill.
Important fields:
- `source`
- `guild_id`
- `session_key`
- `room_key`
- `person_key`
- `session_memory_key`
- `debug_text`
- `metrics`
- `extras`

### `SkillSpec`
Metadata contract for skill registration.
Key fields:
- `name`
- `routes`
- `sources`
- `description`

### `SkillResult`
Normalized result schema for skill output.
Key fields:
- `skill`
- `route`
- `handled`
- `status`
- `display_text`
- `answer_text`
- `should_emit`
- `dedupe_key`
- `executor_used`
- `metadata`
- `payload`

### `SkillExecute`
Protocol describing the callable `execute(context)` shape.

### `RegisteredSkill`
Registered runtime wrapper around `SkillSpec` + execute function.
Includes `supports(route=..., source=...)` helper.

## Why It Matters
This file is the schema anchor for the new skill system.
