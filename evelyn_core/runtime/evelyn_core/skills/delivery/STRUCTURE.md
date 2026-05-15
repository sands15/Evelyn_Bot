# Delivery Skill Structure

## Role
- Handles normalized text delivery planning and TTS preparation.
- Owns route: `delivery`.
- Sources: `text`, `voice`.

## Current Files
- `__init__.py`
  - Declares and registers the delivery skill.
  - Implements `execute(context)`.

## Core Functions
### `execute(context)`
Current behavior:
1. receive answer text or follow-up payload
2. build `AnswerPayload` through injected callback
3. build `DeliveryPlan` through injected callback
4. return normalized `SkillResult` with delivery metadata and payload

## Notes
- Delivery now has real planning logic.
- Actual emission still stays on the core path in `main.py`.
