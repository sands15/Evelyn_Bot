from __future__ import annotations


def build_action_prompt(voyager_prompt: str) -> str:
    return (
        "You are generating Mineflayer JavaScript code for Voyager.\n\n"
        "Rules:\n"
        "- Return only one JavaScript code block.\n"
        "- Do not edit files.\n"
        "- Do not run commands.\n"
        "- Do not explain.\n"
        "- Do not output markdown except the single JavaScript code block.\n"
        "- The code must be executable by Voyager's Mineflayer environment.\n\n"
        f"Voyager prompt:\n{voyager_prompt.strip()}"
    ).strip()
