# Search Skill Structure

## Role
- Handles search-driven answer generation.
- Owns route: `search_executor`.
- Sources: `text`, `voice`.

## Current Files
- `__init__.py`
  - Declares and registers the search skill.
  - Implements `execute(context)`.

## Core Functions
### `execute(context)`
Current behavior:
1. receive search route context
2. call the injected search action callback
3. normalize the answer into `SkillResult`
4. request a first-class delivery follow-up via `followup_route="delivery"`

## Notes
- Search now has real execution logic.
- The actual low-level search execution callback still comes from `main.py`.
