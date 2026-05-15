from pathlib import Path

import voyager.utils as U


def load_control_primitives_context(skills=None):
    package_path = Path(__file__).resolve().parents[1]
    context_dir = package_path / "control_primitives_context"
    if skills is None:
        seen = set()
        skills = []
        for pattern in ("*.txt", "*.js"):
            for path in context_dir.glob(pattern):
                if path.stem not in seen:
                    seen.add(path.stem)
                    skills.append(path.stem)
    primitives = []
    for primitive_name in skills:
        candidate_paths = [
            context_dir / f"{primitive_name}.txt",
            context_dir / f"{primitive_name}.js",
        ]
        for candidate in candidate_paths:
            if candidate.exists():
                primitives.append(U.load_text(str(candidate)))
                break
        else:
            raise FileNotFoundError(
                f"Missing control primitive context for {primitive_name} in {context_dir}"
            )
    return primitives
