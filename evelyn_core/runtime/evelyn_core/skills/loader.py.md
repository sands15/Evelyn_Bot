# `loader.py` Structure

## Role
Loads external Evelyn skills from Python modules, files, or directories.

## Core Functions
### `load_skill_module(module_name, registry=...)`
- Imports a Python module by import path.
- Registers it into the chosen skill registry.

### `load_skill_file(path, registry=...)`
- Loads one `.py` file as an external skill module.
- Creates a synthetic module name so file-based skills can coexist.

### `load_skills_from_directory(directory, registry=..., recursive=False)`
- Scans a directory for `.py` skill files.
- Skips hidden/underscore-prefixed files and `__init__.py`.
- Registers every discovered skill file.

## Why It Matters
This file is the runtime bridge that makes Evelyn's external skill contract actually usable.
