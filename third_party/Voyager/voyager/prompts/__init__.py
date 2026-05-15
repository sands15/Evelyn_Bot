from pathlib import Path

import voyager.utils as U


def load_prompt(prompt):
    package_path = Path(__file__).resolve().parents[1]
    return U.load_text(str(package_path / "prompts" / f"{prompt}.txt"))
