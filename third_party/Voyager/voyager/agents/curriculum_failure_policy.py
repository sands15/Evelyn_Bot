from __future__ import annotations

from typing import Callable


class CurriculumFailurePolicy:
    def __init__(
        self,
        *,
        normalize_task: Callable[[str], str],
        task_keywords: Callable[[str], set[str]],
        current_position_key: Callable[[object], tuple | None],
        search_failure_reasons: set[str],
        repeat_block_failure_reasons: set[str],
    ):
        self._normalize_task = normalize_task
        self._task_keywords = task_keywords
        self._current_position_key = current_position_key
        self._search_failure_reasons = set(search_failure_reasons or set())
        self._repeat_block_failure_reasons = set(repeat_block_failure_reasons or set())

    def recent_local_search_failure(self, task, failed_tasks):
        target_keywords = self._task_keywords(task)
        if not target_keywords:
            return None
        best_record = None
        best_overlap = 0
        for record in failed_tasks or []:
            if record.get("reason") not in self._search_failure_reasons:
                continue
            overlap = len(target_keywords.intersection(self._task_keywords(record.get("task"))))
            if overlap > best_overlap:
                best_overlap = overlap
                best_record = record
        return best_record if best_overlap > 0 else None

    def recent_blocking_failure(self, task, events, failed_tasks):
        task_name = self._normalize_task(task)
        if not task_name:
            return None
        current_position = self._current_position_key(events)
        target_keywords = self._task_keywords(task_name)
        best_record = None
        best_score = 0
        for record in failed_tasks or []:
            reason = str(record.get("reason") or "")
            repeat_count = int(record.get("repeat_count") or 1)
            same_position_count = int(record.get("same_position_repeat_count") or 1)
            if reason not in self._repeat_block_failure_reasons:
                continue
            if repeat_count < 2 and same_position_count < 2:
                continue
            record_task = self._normalize_task(record.get("task"))
            if record_task == task_name:
                score = 100
            else:
                overlap = len(target_keywords.intersection(self._task_keywords(record_task)))
                score = overlap
            if current_position is not None and record.get("last_position_key") == list(current_position):
                score += 10
            if score > best_score:
                best_score = score
                best_record = record
        return best_record if best_score > 0 else None
