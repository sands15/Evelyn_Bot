# `registry.py` Structure

## Role
Central registry for loading, storing, looking up, and executing Evelyn skills.

## Collision Policy
### Skill name collisions
- **Default policy:** reject duplicate skill names.
- `register(...)` and `register_module(...)` now raise an error if the same `name` is already registered.
- To intentionally replace an existing skill, call with `replace=True`.

### Route collisions
- Route duplication is allowed.
- Multiple skills may advertise the same route.
- Current selection stays simple: callers receive matching skills in registry order and choose one.
- Priority-based route arbitration is not implemented yet.
- Current direction is to keep priority as a documented future policy only, not active code.
- We will revisit implementation only after real route-collision cases justify a concrete arbitration rule.

## Core Functions
### `register(spec, execute, origin=..., replace=False)`
Registers a skill from explicit metadata + execute callable.
Rejects duplicate names unless `replace=True`.

### `register_module(module, origin=..., replace=False)`
Reads the minimum skill contract from a Python module and registers it.
Rejects duplicate names unless `replace=True`.

### `get(name)`
Fetches one registered skill by name.

### `list()`
Returns all registered skills.

### `find_by_route(route, source=...)`
Finds skills matching the requested route and optional source.

### `extend(skills)`
Bulk insert helper.

### `execute(name, context)`
Executes a skill by name.
Supports both sync and async `execute(context)` functions.

## Runtime Object
### `skill_registry`
Global registry instance used by built-in and external skills.
