from pathlib import Path

import voyager.utils as U


_SEARCH_ORDER = {
    "search/profiles.js": 0,
    "search/perception.js": 1,
    "search/planner.js": 2,
    "search/progress.js": 3,
    "search/executor.js": 4,
    "search.js": 5,
}


def _primitive_sort_key(base_dir: Path, path: Path):
    rel = path.relative_to(base_dir).as_posix()
    if rel in _SEARCH_ORDER:
        return (0, _SEARCH_ORDER[rel], rel)
    return (1, rel)


def load_control_primitives(primitive_names=None):
    package_path = Path(__file__).resolve().parents[1]
    base_dir = package_path / "control_primitives"
    if primitive_names is None:
        primitive_paths = sorted(base_dir.rglob("*.js"), key=lambda p: _primitive_sort_key(base_dir, p))
    else:
        primitive_paths = []
        for primitive_name in primitive_names:
            candidate_paths = [
                base_dir / f"{primitive_name}.js",
                base_dir / primitive_name / "index.js",
            ]
            for candidate in candidate_paths:
                if candidate.exists():
                    primitive_paths.append(candidate)
                    break
            else:
                raise FileNotFoundError(f"Missing control primitive: {primitive_name}")
    primitives = []
    for primitive_path in primitive_paths:
        primitives.append(U.load_text(str(primitive_path)))
    return primitives
