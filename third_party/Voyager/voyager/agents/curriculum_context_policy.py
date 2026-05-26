from __future__ import annotations

import re


class CurriculumContextPolicy:
    def search_policy_context(self, task, *, local_search_exhausted_reason):
        verb = str(task or "").strip().split(" ", 1)[0].lower()
        if verb not in {"obtain", "mine", "kill", "cook", "eat"}:
            return ""
        return (
            "\nOperational policy: prefer intent-level search helpers instead of ad-hoc wandering. "
            "For wood or food in the wrong domain, recover to the surface first. "
            "For nearby-search tasks, first check within 32 blocks, then use short bounded search only if needed. "
            f"If search stalls or exhausts candidates, stop with a concise reason such as {local_search_exhausted_reason}, wood_scout_exhausted, food_scout_exhausted, or surface_recovery_exhausted so the next curriculum step can change direction, biome, or prerequisites."
        )

    def task_question(self, task):
        normalized_task = (
            str(task or "")
            .replace("_", " ")
            .replace(" ore", "")
            .replace(" ores", "")
            .replace(".", "")
            .strip()
            .lower()
        )
        return f"How to {normalized_task} in Minecraft?"

    def biome_label(self, biome):
        normalized = str(biome or "").replace("_", " ").strip().lower()
        return normalized or "current area"

    def seed_questions_for_biome(self, biome):
        label = self.biome_label(biome)
        return [
            f"What are the blocks that I can find in the {label} in Minecraft?",
            f"What are the items that I can find in the {label} in Minecraft?",
            f"What are the mobs that I can find in the {label} in Minecraft?",
        ]

    def normalize_qa_question(self, question):
        normalized = re.sub(r"\s+", " ", str(question or "")).strip()
        if not normalized:
            return ""
        normalized = re.sub(
            r"\bin the in Minecraft\?$",
            "in the current area in Minecraft?",
            normalized,
            flags=re.IGNORECASE,
        )
        return normalized
