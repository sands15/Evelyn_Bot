# Conversation Skill Structure

## Role
- Handles general direct-answer conversation flow.
- Owns routes: `main_direct`, `policy_short_circuit`.
- Sources: `text`, `voice`.

## Current Files
- `__init__.py`
  - Defines the skill contract fields.
  - Registers the skill through `skill_registry.register_module(...)`.
  - Implements `execute(context)`.

## Core Functions
### `execute(context)`
Current behavior:
1. inspect route/preface from `SkillContext`
2. immediately return a policy short-circuit result when appropriate
3. otherwise build the non-stream main-LLM once payload through injected callbacks
4. execute the main LLM once path
5. return normalized `SkillResult`

## Notes
- This skill intentionally handles the non-stream direct-answer path.
- The real STT -> router subLLM -> main LLM -> TTS backbone still stays centered in `main.py`.
