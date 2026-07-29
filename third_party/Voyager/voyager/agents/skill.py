import json
import os
import re
import time
from pathlib import Path

import voyager.utils as U
from langchain.chat_models import ChatOpenAI
from langchain.schema import HumanMessage, SystemMessage

from voyager.prompts import load_prompt
from voyager.control_primitives import load_control_primitives
from voyager.agents.local_text_index import LocalTextIndex
from voyager.utils.console import safe_print as print


class SkillManager:
    POLICY_STATE_FILENAME = "policy_state.json"
    POLICY_REJECT_SCORE = 6
    POLICY_DELETE_FAILURES = 2
    POLICY_MIN_PROVED_SUCCESSES = 2
    POLICY_MAX_EVENTS = 200
    POLICY_MAX_EXPLORE_CALLS = 2
    POLICY_MAX_HELPERS = 5
    POLICY_MAX_AWAITS = 17
    POLICY_MAX_LINES = 140
    CHEAT_COMMAND_PATTERN = re.compile(
        r"/(?:give|gamerule|time|difficulty|spreadplayers|item|setblock)\b",
        re.IGNORECASE,
    )
    DIRECT_LOW_LEVEL_PATTERN = re.compile(
        r"\b(?:bot\.craft|bot\.recipesFor|bot\.placeBlock|bot\.dig|bot\.openFurnace|bot\.attack)\b"
    )

    def __init__(
        self,
        model_name="gpt-3.5-turbo",
        temperature=0,
        retrieval_top_k=5,
        request_timout=120,
        ckpt_dir="ckpt",
        resume=False,
        llm_url=None,
    ):
        llm_kwargs = {
            "model_name": model_name,
            "temperature": temperature,
            "request_timeout": request_timout,
        }
        if llm_url:
            llm_kwargs["openai_api_base"] = llm_url.removesuffix("/chat/completions")
        self.llm = ChatOpenAI(**llm_kwargs)
        U.f_mkdir(f"{ckpt_dir}/skill/code")
        U.f_mkdir(f"{ckpt_dir}/skill/description")
        U.f_mkdir(f"{ckpt_dir}/skill/vectordb")
        # programs for env execution
        self.control_primitives = load_control_primitives()
        if resume:
            print(f"\033[33mLoading Skill Manager from {ckpt_dir}/skill\033[0m")
            skills_path = f"{ckpt_dir}/skill/skills.json"
            if os.path.exists(skills_path):
                self.skills = U.load_json(skills_path)
            else:
                self.skills = {}
        else:
            self.skills = {}
        self.retrieval_top_k = retrieval_top_k
        self.ckpt_dir = ckpt_dir
        self.skill_dir = Path(ckpt_dir) / "skill"
        self.code_dir = self.skill_dir / "code"
        self.description_dir = self.skill_dir / "description"
        self.policy_state_path = self.skill_dir / self.POLICY_STATE_FILENAME
        self.policy_state = self._load_policy_state()
        self.vectordb = LocalTextIndex(
            collection_name="skill_vectordb",
            persist_directory=f"{ckpt_dir}/skill/vectordb",
        )
        self._ensure_vectordb_sync(repair=True)

    def _load_policy_state(self):
        if self.policy_state_path.exists():
            try:
                payload = json.loads(self.policy_state_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    payload.setdefault("skills", {})
                    payload.setdefault("events", [])
                    payload.setdefault("search", {})
                    return payload
            except Exception:
                pass
        return {"skills": {}, "events": [], "search": {}}

    def _save_policy_state(self):
        self.policy_state_path.write_text(
            json.dumps(self.policy_state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _append_policy_event(self, program_name, event_type, reasons=None, extra=None):
        events = self.policy_state.setdefault("events", [])
        payload = {
            "ts": time.time(),
            "program_name": program_name,
            "event": event_type,
            "reasons": list(reasons or []),
        }
        if isinstance(extra, dict):
            payload.update(extra)
        events.append(payload)
        if len(events) > self.POLICY_MAX_EVENTS:
            del events[:-self.POLICY_MAX_EVENTS]

    def _skill_state(self, program_name):
        skills_state = self.policy_state.setdefault("skills", {})
        return skills_state.setdefault(
            program_name,
            {
                "successes": 0,
                "failures": 0,
                "rejected_saves": 0,
                "delete_candidate": False,
                "candidate_reasons": [],
                "auto_deleted": False,
                "last_score": 0,
                "last_task": None,
                "last_updated_at": None,
            },
        )

    @staticmethod
    def _skill_family_name(program_name, task_text=""):
        base = re.sub(r"V\d+$", "", str(program_name or "").strip())
        if base:
            return base
        lowered = str(task_text or "").strip().lower()
        lowered = re.sub(r"\d+", "N", lowered)
        lowered = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
        return lowered or "unknown_family"

    def _existing_family_skill(self, family_name, *, exclude=None):
        family = str(family_name or "").strip()
        if not family:
            return None
        for skill_name, state in self.policy_state.get("skills", {}).items():
            if skill_name == exclude:
                continue
            if str(state.get("family") or "") != family:
                continue
            if state.get("auto_deleted") or state.get("delete_candidate"):
                continue
            if skill_name in self.skills:
                return skill_name
        return None

    @staticmethod
    def _is_proved_success(info):
        if not isinstance(info, dict) or not info.get("success"):
            return False
        completion_reason = str(info.get("completion_reason") or "").strip().lower()
        if completion_reason == "world_effect_verified":
            return True
        effect = info.get("world_effect_verification") if isinstance(info.get("world_effect_verification"), dict) else {}
        return str(effect.get("outcome") or "").strip().lower() == "success"

    def _search_state(self, goal_type):
        search_state = self.policy_state.setdefault("search", {})
        return search_state.setdefault(
            goal_type,
            {
                "successes": 0,
                "failures": 0,
                "consecutive_failures": 0,
                "last_task": None,
                "last_failure_reason": None,
                "last_failure_category": None,
                "last_updated_at": None,
                "adjustments": {
                    "radius_bonus": 0,
                    "time_budget_scale": 1.0,
                    "progress_timeout_scale": 1.0,
                    "force_domain": None,
                },
                "failure_reasons": {},
                "failure_categories": {},
            },
        )

    def _analyze_skill(self, program_code):
        code = str(program_code or "")
        reasons = []
        score = 0
        if self.CHEAT_COMMAND_PATTERN.search(code):
            reasons.append("cheat_dependency")
            score += 4
        explore_calls = code.count("await exploreUntil(")
        if explore_calls > self.POLICY_MAX_EXPLORE_CALLS:
            reasons.append("over_exploration")
            score += 3
        elif explore_calls == self.POLICY_MAX_EXPLORE_CALLS:
            reasons.append("exploration_heavy")
            score += 1
        if re.search(r"while\s*\(", code):
            reasons.append("loop_risk")
            score += 2
        if self.DIRECT_LOW_LEVEL_PATTERN.search(code):
            reasons.append("low_level_primitive_bypass")
            score += 2
        line_count = len(code.splitlines())
        if line_count >= self.POLICY_MAX_LINES:
            reasons.append("oversized_skill")
            score += 2
        await_count = code.count("await ")
        if await_count >= self.POLICY_MAX_AWAITS:
            reasons.append("high_async_complexity")
            score += 2
        helper_count = len(re.findall(r"async function |function ", code))
        if helper_count > self.POLICY_MAX_HELPERS:
            reasons.append("too_many_helpers")
            score += 2
        prereq_hits = len(
            re.findall(
                r"(?:ensure[A-Z]\w+|craft(?:WithInventory|Item)?\(|mineBlock\(bot, \"(?:oak_log|jungle_log|stone)\"|placeItem\(bot, \"crafting_table\")",
                code,
            )
        )
        if prereq_hits >= 5:
            reasons.append("oversized_prereq_chain")
            score += 2
        if re.search(r"countItem\(bot,\s*\"\w+\"\)\s*-\s*starting\w+\s*<", code):
            reasons.append("delta_inventory_tracking")
        return {
            "score": score,
            "reasons": reasons,
            "reject_save": score >= self.POLICY_REJECT_SCORE,
        }

    def _vectordb_ids(self):
        try:
            payload = self.vectordb.get()
        except Exception:
            payload = None
        ids = []
        if isinstance(payload, dict):
            ids = payload.get("ids") or []
        return [doc_id for doc_id in ids if doc_id]

    def _rebuild_vectordb_from_skills(self):
        existing_ids = self._vectordb_ids()
        if existing_ids:
            try:
                self.vectordb._collection.delete(ids=existing_ids)
            except Exception:
                for doc_id in existing_ids:
                    try:
                        self.vectordb._collection.delete(ids=[doc_id])
                    except Exception:
                        pass
        if self.skills:
            texts = []
            ids = []
            metadatas = []
            for program_name, entry in self.skills.items():
                description = str((entry or {}).get("description") or "").strip()
                if not description:
                    description = (
                        f"async function {program_name}(bot) {{\n"
                        f"    // skill description missing during vectordb repair\n"
                        f"}}"
                    )
                    entry["description"] = description
                texts.append(description)
                ids.append(program_name)
                metadatas.append({"name": program_name})
            self.vectordb.add_texts(texts=texts, ids=ids, metadatas=metadatas)
        self.vectordb.persist()

    def _ensure_vectordb_sync(self, repair=False):
        actual = self.vectordb._collection.count()
        expected = len(self.skills)
        if actual == expected:
            return
        message = (
            f"Skill Manager's vectordb is not synced with skills.json.\n"
            f"There are {actual} skills in vectordb but {expected} skills in skills.json."
        )
        if repair:
            print(f"\033[33m{message} Rebuilding vectordb from skills.json.\033[0m")
            self._rebuild_vectordb_from_skills()
            actual = self.vectordb._collection.count()
            if actual == expected:
                U.dump_json(self.skills, f"{self.ckpt_dir}/skill/skills.json")
                return
        raise AssertionError(
            message
            + "\nDid you set resume=False when initializing the manager?"
            + "\nYou may need to manually delete the vectordb directory for running from scratch."
        )

    def _delete_skill_artifacts(self, program_name):
        if program_name in self.skills:
            self.skills.pop(program_name, None)
        try:
            self.vectordb._collection.delete(ids=[program_name])
        except Exception:
            pass
        for pattern in (f"{program_name}.js", f"{program_name}V*.js"):
            for path in self.code_dir.glob(pattern):
                path.unlink(missing_ok=True)
        for pattern in (f"{program_name}.txt", f"{program_name}V*.txt"):
            for path in self.description_dir.glob(pattern):
                path.unlink(missing_ok=True)
        U.dump_json(self.skills, f"{self.ckpt_dir}/skill/skills.json")
        self.vectordb.persist()

    def maybe_delete_skill(self, program_name, reasons, *, force=False):
        state = self._skill_state(program_name)
        if force or (program_name in self.skills and state.get("failures", 0) >= self.POLICY_DELETE_FAILURES):
            self._delete_skill_artifacts(program_name)
            state["auto_deleted"] = True
            state["delete_candidate"] = False
            state["candidate_reasons"] = list(reasons)
            self._append_policy_event(program_name, "auto_deleted", reasons)
            self._save_policy_state()
            print(f"\033[33mSkill policy auto-deleted {program_name}: {', '.join(reasons)}\033[0m")
            return True
        return False

    def record_search_outcome(self, metrics):
        if not isinstance(metrics, dict):
            return None
        goal_type = str(metrics.get("goal_type") or "generic").strip() or "generic"
        state = self._search_state(goal_type)
        adjustments = state.setdefault(
            "adjustments",
            {
                "radius_bonus": 0,
                "time_budget_scale": 1.0,
                "progress_timeout_scale": 1.0,
                "force_domain": None,
            },
        )
        success = bool(metrics.get("success"))
        failure_reason = str(metrics.get("failure_reason") or "").strip() or None
        failure_category = str(metrics.get("failure_category") or "").strip() or None
        state["last_task"] = metrics.get("task")
        state["last_updated_at"] = time.time()
        if success:
            state["successes"] = int(state.get("successes", 0)) + 1
            state["consecutive_failures"] = 0
            adjustments["radius_bonus"] = max(0, int(adjustments.get("radius_bonus", 0)) - 1)
            adjustments["time_budget_scale"] = max(1.0, round(float(adjustments.get("time_budget_scale", 1.0)) - 0.05, 2))
            adjustments["progress_timeout_scale"] = min(1.0, round(float(adjustments.get("progress_timeout_scale", 1.0)) + 0.05, 2))
        else:
            state["failures"] = int(state.get("failures", 0)) + 1
            state["consecutive_failures"] = int(state.get("consecutive_failures", 0)) + 1
            state["last_failure_reason"] = failure_reason
            state["last_failure_category"] = failure_category
            if failure_reason:
                failure_reasons = state.setdefault("failure_reasons", {})
                failure_reasons[failure_reason] = int(failure_reasons.get(failure_reason, 0)) + 1
            if failure_category:
                failure_categories = state.setdefault("failure_categories", {})
                failure_categories[failure_category] = int(failure_categories.get(failure_category, 0)) + 1
            if failure_category in {"exhausted", "missing_target"}:
                adjustments["radius_bonus"] = min(2, int(adjustments.get("radius_bonus", 0)) + 1)
                adjustments["time_budget_scale"] = min(1.5, round(float(adjustments.get("time_budget_scale", 1.0)) + 0.1, 2))
            if failure_category in {"stuck", "timeout"}:
                adjustments["progress_timeout_scale"] = max(0.65, round(float(adjustments.get("progress_timeout_scale", 1.0)) - 0.1, 2))
            if goal_type in {"wood", "food"} and failure_reason in {"surface_not_found", "surface_recovery_exhausted"}:
                adjustments["force_domain"] = "surface"
            elif goal_type == "ore" and failure_reason == "surface_not_found":
                adjustments["force_domain"] = "underground"
        self._append_policy_event(
            f"search:{goal_type}",
            "search_outcome",
            [reason for reason in [failure_category, failure_reason] if reason],
            {
                "goal_type": goal_type,
                "success": success,
                "adjustments": dict(adjustments),
                "task": metrics.get("task"),
            },
        )
        self._save_policy_state()
        return {
            "goal_type": goal_type,
            "state": state,
            "adjustments": dict(adjustments),
        }

    def export_search_policy(self):
        payload = {}
        for goal_type, state in (self.policy_state.get("search", {}) or {}).items():
            if not isinstance(state, dict):
                continue
            adjustments = state.get("adjustments") if isinstance(state.get("adjustments"), dict) else {}
            payload[goal_type] = {
                "radiusBonus": int(adjustments.get("radius_bonus", 0) or 0),
                "timeBudgetScale": float(adjustments.get("time_budget_scale", 1.0) or 1.0),
                "progressTimeoutScale": float(adjustments.get("progress_timeout_scale", 1.0) or 1.0),
                "forceDomain": adjustments.get("force_domain"),
                "consecutiveFailures": int(state.get("consecutive_failures", 0) or 0),
                "lastFailureCategory": state.get("last_failure_category"),
                "lastFailureReason": state.get("last_failure_reason"),
            }
        return payload

    @property
    def programs(self):
        programs = ""
        for skill_name, entry in self.skills.items():
            programs += f"{entry['code']}\n\n"
        for primitives in self.control_primitives:
            programs += f"{primitives}\n\n"
        return programs

    def record_skill_outcome(self, info):
        program_name = info.get("program_name")
        program_code = info.get("program_code")
        if not program_name or not program_code:
            return None
        state = self._skill_state(program_name)
        review = self._analyze_skill(program_code)
        state["last_score"] = review["score"]
        state["last_task"] = info.get("task")
        state["last_updated_at"] = time.time()
        if info.get("success"):
            state["successes"] = int(state.get("successes", 0)) + 1
        else:
            state["failures"] = int(state.get("failures", 0)) + 1
            state["delete_candidate"] = True
            state["candidate_reasons"] = sorted(set(list(state.get("candidate_reasons", [])) + ["execution_failure"] + review["reasons"]))
            self._append_policy_event(program_name, "failed_run", state["candidate_reasons"], {"task": info.get("task")})
            self.maybe_delete_skill(program_name, state["candidate_reasons"])
        self._save_policy_state()
        return review

    def add_new_skill(self, info):
        if info["task"].startswith("Deposit useless items into the chest at"):
            # No need to reuse the deposit skill
            return
        program_name = info["program_name"]
        program_code = info["program_code"]
        review = self._analyze_skill(program_code)
        state = self._skill_state(program_name)
        family_name = self._skill_family_name(program_name, info.get("task"))
        state["family"] = family_name
        if review["reject_save"]:
            state["rejected_saves"] = int(state.get("rejected_saves", 0)) + 1
            state["delete_candidate"] = True
            state["candidate_reasons"] = sorted(set(list(state.get("candidate_reasons", [])) + review["reasons"]))
            self._append_policy_event(program_name, "save_rejected", state["candidate_reasons"], {"task": info.get("task"), "score": review["score"]})
            force_delete = any(reason in review["reasons"] for reason in ["cheat_dependency", "low_level_primitive_bypass"])
            self.maybe_delete_skill(program_name, state["candidate_reasons"], force=force_delete)
            self._save_policy_state()
            print(f"\033[33mSkill policy rejected save for {program_name}: {', '.join(review['reasons']) or 'policy'}\033[0m")
            return
        if not self._is_proved_success(info):
            state["pending_proof"] = True
            self._append_policy_event(
                program_name,
                "save_deferred_unproved",
                ["not_proved_routine"],
                {"task": info.get("task"), "family": family_name},
            )
            self._save_policy_state()
            print(f"\033[33mSkill Manager deferred save for {program_name}: routine is not yet proved by deterministic evidence.\033[0m")
            return
        state["proved_successes"] = int(state.get("proved_successes", 0)) + 1
        if int(state.get("proved_successes", 0)) < self.POLICY_MIN_PROVED_SUCCESSES:
            state["pending_proof"] = True
            self._append_policy_event(
                program_name,
                "save_deferred_pending_more_proof",
                ["insufficient_proved_runs"],
                {"task": info.get("task"), "family": family_name, "proved_successes": state.get("proved_successes", 0)},
            )
            self._save_policy_state()
            print(f"\033[33mSkill Manager deferred save for {program_name}: waiting for repeated proved success.\033[0m")
            return
        existing_family_skill = self._existing_family_skill(family_name, exclude=program_name)
        if existing_family_skill:
            existing_state = self.policy_state.get("skills", {}).get(existing_family_skill, {})
            existing_score = int(existing_state.get("last_score", 0) or 0)
            existing_successes = int(existing_state.get("proved_successes", 0) or 0)
            if existing_score > review["score"] or existing_successes > int(state.get("proved_successes", 0) or 0):
                state["pending_proof"] = False
                self._append_policy_event(
                    program_name,
                    "save_skipped_family_duplicate",
                    ["family_duplicate"],
                    {"task": info.get("task"), "family": family_name, "active_skill": existing_family_skill},
                )
                self._save_policy_state()
                print(f"\033[33mSkill Manager skipped save for {program_name}: family '{family_name}' already has proved representative {existing_family_skill}.\033[0m")
                return
        try:
            skill_description = self.generate_skill_description(program_name, program_code)
        except Exception as exc:
            state["delete_candidate"] = True
            state["candidate_reasons"] = sorted(set(list(state.get("candidate_reasons", [])) + ["description_generation_failed"]))
            self._append_policy_event(program_name, "description_generation_failed", ["description_generation_failed"], {"error": str(exc)})
            self._save_policy_state()
            print(f"\033[33mSkill Manager skipped save for {program_name} because description generation failed: {exc}\033[0m")
            return
        print(
            f"\033[33mSkill Manager generated description for {program_name}:\n{skill_description}\033[0m"
        )
        if program_name in self.skills:
            print(f"\033[33mSkill {program_name} already exists. Rewriting!\033[0m")
            self.vectordb._collection.delete(ids=[program_name])
            i = 2
            while f"{program_name}V{i}.js" in os.listdir(f"{self.ckpt_dir}/skill/code"):
                i += 1
            dumped_program_name = f"{program_name}V{i}"
        else:
            dumped_program_name = program_name
        self.vectordb.add_texts(
            texts=[skill_description],
            ids=[program_name],
            metadatas=[{"name": program_name}],
        )
        self.skills[program_name] = {
            "code": program_code,
            "description": skill_description,
        }
        if existing_family_skill and existing_family_skill != program_name:
            self.maybe_delete_skill(existing_family_skill, ["family_replaced"], force=True)
        state["delete_candidate"] = False
        state["candidate_reasons"] = []
        state["auto_deleted"] = False
        state["pending_proof"] = False
        self._append_policy_event(program_name, "saved", [], {"task": info.get("task"), "score": review["score"]})
        self._ensure_vectordb_sync(repair=True)
        U.dump_text(
            program_code, f"{self.ckpt_dir}/skill/code/{dumped_program_name}.js"
        )
        U.dump_text(
            skill_description,
            f"{self.ckpt_dir}/skill/description/{dumped_program_name}.txt",
        )
        U.dump_json(self.skills, f"{self.ckpt_dir}/skill/skills.json")
        self.vectordb.persist()
        self._save_policy_state()

    def generate_skill_description(self, program_name, program_code):
        messages = [
            SystemMessage(content=load_prompt("skill")),
            HumanMessage(
                content=program_code
                + "\n\n"
                + f"The main function is `{program_name}`."
            ),
        ]
        skill_description = f"    // { self.llm(messages).content}"
        return f"async function {program_name}(bot) {{\n{skill_description}\n}}"

    def retrieve_skills(self, query):
        k = min(self.vectordb._collection.count(), self.retrieval_top_k)
        if k == 0:
            return []
        print(f"\033[33mSkill Manager retrieving for {k} skills\033[0m")
        docs_and_scores = self.vectordb.similarity_search_with_score(query, k=k)
        docs_and_scores = [
            (doc, score) for doc, score in docs_and_scores if float(score) < 1.0
        ]
        print(
            f"\033[33mSkill Manager retrieved skills: "
            f"{', '.join([doc.metadata['name'] for doc, _ in docs_and_scores])}\033[0m"
        )
        skills = []
        for doc, _ in docs_and_scores:
            name = doc.metadata["name"]
            state = self.policy_state.get("skills", {}).get(name, {})
            if state.get("delete_candidate") or state.get("auto_deleted"):
                continue
            if name in self.skills:
                skills.append(self.skills[name]["code"])
        return skills
