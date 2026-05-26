from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
APP_ROOT = REPO_ROOT.parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
RUNTIME_ROOT = APP_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from voyager.agents.curriculum_fallback_policy import CurriculumFallbackPolicy
from voyager.agents.action_validator_policy import ActionValidatorPolicy
from voyager.agents.curriculum import CurriculumAgent
from voyager.agents.food_signals import edible_food_total
from voyager.agents.outcome_policy import CriticOutcomePolicy
from voyager.agents.inventory_planner import InventoryFirstPlanner, InventoryState
from voyager.agents.objective_templates import infer_objective_template
from voyager.agents.progression_policy import EarlyGameProgressionPolicy
from voyager.agents.task_contract_policy import TaskContractPolicy, canonicalize_smelt_result_task
from voyager.agents.world_effect_verifier import verify_task_effect
from voyager.voyager import Voyager
from evelyn_core.upstream_voyager_runner import _compute_display_stage
from evelyn_core.voyager_service import _stability_signals


def observe_event(*, inventory=None, status=None, nearby_blocks=None, voxels=None):
    return [[
        "observe",
        {
            "inventory": inventory or {},
            "status": status or {},
            "nearby_blocks": list(nearby_blocks or []),
            "voxels": list(voxels or []),
        },
    ]]


class TaskContractFallbackTests(unittest.TestCase):
    def test_unknown_task_falls_to_shelter_at_night(self):
        policy = TaskContractPolicy()
        events = observe_event(
            inventory={},
            status={"timeOfDay": "night", "health": 20, "food": 20, "entities": {}},
            nearby_blocks=["stone"],
        )

        task, context, decision = policy.enforce_task_choice(
            "Scout for a scenic overlook",
            "Explore a new area.",
            events=events,
        )

        self.assertEqual(task, "Establish a lit temporary shelter")
        self.assertEqual(decision["state"], "fallback")
        self.assertTrue(decision["fallback_applied"])
        self.assertIn("non-verifiable", context)

    def test_unknown_task_defaults_to_bootstrap_wood(self):
        policy = TaskContractPolicy()
        events = observe_event(
            inventory={},
            status={"timeOfDay": "day", "health": 20, "food": 20, "entities": {}},
            nearby_blocks=["stone"],
        )

        task, _context, decision = policy.enforce_task_choice(
            "Admire the horizon",
            "Do something open-ended.",
            events=events,
        )

        self.assertEqual(task, "Obtain 8 wood logs")
        self.assertEqual(decision["state"], "fallback")

    def test_unknown_task_with_advanced_inventory_still_defaults_to_wood(self):
        policy = TaskContractPolicy()
        events = observe_event(
            inventory={
                "iron_pickaxe": 1,
                "iron_ingot": 8,
                "coal": 12,
                "torch": 16,
            },
            status={"timeOfDay": "day", "health": 20, "food": 20, "entities": {}},
            nearby_blocks=["stone", "coal_ore"],
        )

        task, _context, decision = policy.enforce_task_choice(
            "Organize the base perimeter",
            "Do a tidy-up pass before exploring.",
            events=events,
        )

        self.assertEqual(task, "Obtain 8 wood logs")
        self.assertEqual(decision["state"], "fallback")


class SurvivalOverrideTests(unittest.TestCase):
    def test_survival_override_forces_food_when_hungry(self):
        policy = EarlyGameProgressionPolicy(get_completed_tasks=lambda: [])
        events = observe_event(
            inventory={},
            status={"health": 20, "food": 7, "entities": {}, "timeOfDay": "day"},
            nearby_blocks=["stone"],
        )

        override = policy.survival_override(events)

        self.assertIsNotNone(override)
        self.assertEqual(override[0], "Acquire 1 edible food item")

    def test_survival_override_forces_shelter_at_night_with_hostiles(self):
        policy = EarlyGameProgressionPolicy(get_completed_tasks=lambda: [])
        events = observe_event(
            inventory={},
            status={
                "health": 10,
                "food": 20,
                "timeOfDay": "night",
                "entities": {"zombie": {"distance": 6}},
            },
            nearby_blocks=["stone"],
        )

        override = policy.survival_override(events)

        self.assertIsNotNone(override)
        self.assertEqual(override[0], "Establish a lit temporary shelter")

    def test_stage3_guardrail_rewrites_unrelated_iron_goal_to_food(self):
        policy = EarlyGameProgressionPolicy(
            get_completed_tasks=lambda: [],
            get_nearby_progression_candidates=lambda events: [],
        )
        events = observe_event(
            inventory={
                "stone_pickaxe": 1,
                "stone_axe": 1,
                "iron_ingot": 8,
                "furnace": 1,
                "coal": 4,
            },
            status={"health": 20, "food": 20, "entities": {}, "timeOfDay": "day"},
            nearby_blocks=["stone"],
        )

        decision = policy.guard_task(
            "Craft 1 iron_chestplate",
            "Upgrade armor.",
            events,
        )

        self.assertEqual(decision["stage"], 3)
        self.assertTrue(decision["changed"])
        self.assertEqual(decision["task"], "Acquire 1 edible food item")

    def test_iron_capability_keeps_run_out_of_stage3_raw_iron_loop(self):
        policy = EarlyGameProgressionPolicy(
            get_completed_tasks=lambda: [],
            get_nearby_progression_candidates=lambda events: [],
        )
        events = observe_event(
            inventory={
                "stone_pickaxe": 1,
                "stone_axe": 1,
                "iron_pickaxe": 1,
                "bread": 2,
                "torch": 12,
            },
            status={"health": 20, "food": 20, "entities": {}, "timeOfDay": "day"},
            nearby_blocks=["stone"],
        )

        decision = policy.guard_task(
            "Craft 1 iron_chestplate",
            "Upgrade armor.",
            events,
        )

        self.assertEqual(decision["stage"], 4)
        self.assertFalse(decision["changed"])
        self.assertEqual(decision["task"], "Craft 1 iron_chestplate")


class SearchFallbackTests(unittest.TestCase):
    def make_policy(self):
        return CurriculumFallbackPolicy(
            normalize_task=lambda task: str(task or "").strip(),
            is_repeatable_state_task=lambda task: False,
            task_inventory_satisfied=lambda task, inventory: False,
            predict_task_from_inventory=lambda inventory, events, source: None,
            nearby_progression_candidates=lambda events: [],
            recovery_fallback_task=lambda inventory: (
                "Move 24 blocks away from current position",
                "Generic recovery fallback.",
            ),
            count_logs=lambda inventory: 0,
            count_planks=lambda inventory: 0,
        )

    def test_ore_search_failure_falls_back_to_reposition(self):
        policy = self.make_policy()

        task, context = policy.fallback_after_local_search_failure(
            events=observe_event(
                inventory={},
                status={"health": 20, "food": 20},
                nearby_blocks=["stone"],
                voxels=["stone"],
            ),
            voxels=["stone"],
            inventory={},
            failed_record={"reason": "ore_scout_dead_end"},
            blocked_tasks=set(),
        )

        self.assertEqual(task, "Move 24 blocks away from current position")
        self.assertIn("ore scouting failed", context)

    def test_food_search_failure_uses_food_task_when_food_exists(self):
        policy = self.make_policy()

        task, context = policy.fallback_after_local_search_failure(
            events=observe_event(
                inventory={"bread": 1},
                status={"health": 20, "food": 20},
                nearby_blocks=["stone"],
            ),
            voxels=["stone"],
            inventory={"bread": 1},
            failed_record={"reason": "food_scout_loop"},
            blocked_tasks=set(),
        )

        self.assertEqual(task, "Acquire 1 edible food item")
        self.assertIn("already-owned food resources", context)

    def test_search_fallback_exhausted_candidates_collapse_to_reposition(self):
        policy = CurriculumFallbackPolicy(
            normalize_task=lambda task: str(task or "").strip(),
            is_repeatable_state_task=lambda task: False,
            task_inventory_satisfied=lambda task, inventory: task == "Obtain 8 wood logs",
            predict_task_from_inventory=lambda inventory, events, source: (
                "Obtain 8 wood logs",
                "Inventory prediction candidate.",
                "predicted",
            ),
            nearby_progression_candidates=lambda events: [
                ("Obtain 8 wood logs", "Nearby candidate repeats the same blocked task."),
            ],
            recovery_fallback_task=lambda inventory: (
                "Move 24 blocks away from current position",
                "Generic recovery fallback.",
            ),
            count_logs=lambda inventory: 0,
            count_planks=lambda inventory: 0,
        )

        task, context = policy.fallback_after_local_search_failure(
            events=observe_event(
                inventory={},
                status={"health": 20, "food": 20},
                nearby_blocks=["stone"],
                voxels=["stone"],
            ),
            voxels=["stone"],
            inventory={},
            failed_record={"reason": "local_search_stalled"},
            blocked_tasks={"Obtain 8 wood logs"},
        )

        self.assertEqual(task, "Move 24 blocks away from current position")
        self.assertIn("Generic recovery fallback", context)


class InventoryFirstPlannerTests(unittest.TestCase):
    def test_cooked_rabbit_counts_as_food_for_inventory_state(self):
        state = InventoryState.from_observation(
            inventory={"cooked_rabbit": 1},
            status={"health": 20, "food": 20},
            nearby_blocks=["stone"],
        )

        self.assertTrue(state.has_food)

    def test_raw_iron_does_not_force_smelt_after_iron_pickaxe_upgrade(self):
        planner = InventoryFirstPlanner()
        state = InventoryState.from_observation(
            inventory={
                "iron_pickaxe": 1,
                "iron_ingot": 11,
                "raw_iron": 5,
                "furnace": 1,
                "coal": 3,
                "stick": 5,
                "crafting_table": 1,
                "bread": 2,
            },
            status={"health": 20, "food": 20},
            nearby_blocks=["iron_ore", "coal_ore", "stone"],
        )

        task = planner.choose_next(state, previous_task="inventory_first", allow_optional=False)

        self.assertIsNotNone(task)
        self.assertEqual(task.task, "Obtain 3 diamond")
        self.assertEqual(task.capability, "diamond_pickaxe")

    def test_have_into_smelt_contract_is_satisfied_by_ingot_result(self):
        planner = InventoryFirstPlanner()

        satisfied = planner.is_task_satisfied(
            "Have 8 raw_iron into iron_ingots",
            {
                "iron_ingot": 18,
                "raw_iron": 16,
                "iron_pickaxe": 1,
                "furnace": 1,
                "coal": 1,
            },
        )

        self.assertTrue(satisfied)

    def test_have_into_smelt_contract_with_fuel_suffix_is_satisfied_by_ingot_result(self):
        planner = InventoryFirstPlanner()

        satisfied = planner.is_task_satisfied(
            "Have 16 raw_iron into iron_ingots using coal",
            {
                "iron_ingot": 18,
                "raw_iron": 16,
                "iron_pickaxe": 1,
                "furnace": 1,
                "coal": 1,
            },
        )

        self.assertTrue(satisfied)

    def test_armor_objective_prefers_diamond_pickaxe_then_armor(self):
        planner = InventoryFirstPlanner()
        state = InventoryState.from_observation(
            inventory={
                "iron_pickaxe": 1,
                "iron_ingot": 11,
                "diamond": 3,
                "stick": 5,
                "crafting_table": 1,
                "bread": 2,
            },
            status={"health": 20, "food": 20},
            nearby_blocks=["stone"],
        )

        task = planner.choose_next(
            state,
            previous_task="inventory_first",
            allow_optional=False,
            objective="armor_progression",
        )

        self.assertIsNotNone(task)
        self.assertEqual(task.task, "Craft 1 diamond_pickaxe")
        self.assertEqual(task.objective, "armor_progression")
        self.assertEqual(task.capability, "diamond_pickaxe")

    def test_progression_objective_advances_from_iron_capability_to_diamond_goal(self):
        planner = InventoryFirstPlanner()
        state = InventoryState.from_observation(
            inventory={
                "iron_pickaxe": 1,
                "bread": 2,
                "torch": 12,
                "crafting_table": 1,
            },
            status={"health": 20, "food": 20},
            nearby_blocks=["stone", "iron_ore"],
        )

        task = planner.choose_next(
            state,
            previous_task="inventory_first",
            allow_optional=False,
            objective="progression",
        )

        self.assertIsNotNone(task)
        self.assertEqual(task.task, "Obtain 3 diamond")
        self.assertEqual(task.objective, "progression")
        self.assertEqual(task.capability, "diamond_pickaxe")

    def test_base_objective_places_existing_chest_before_other_growth(self):
        planner = InventoryFirstPlanner()
        state = InventoryState.from_observation(
            inventory={
                "bread": 2,
                "torch": 12,
                "chest": 1,
                "crafting_table": 1,
            },
            status={"health": 20, "food": 20},
            nearby_blocks=["stone"],
        )

        task = planner.choose_next(
            state,
            previous_task="inventory_first",
            allow_optional=False,
            objective="base_establishment",
        )

        self.assertIsNotNone(task)
        self.assertEqual(task.task, "Place 1 chest")
        self.assertEqual(task.objective, "base_establishment")
        self.assertEqual(task.capability, "storage_access")

    def test_base_objective_crafts_crafting_table_after_storage_is_present(self):
        planner = InventoryFirstPlanner()
        state = InventoryState.from_observation(
            inventory={
                "bread": 2,
                "torch": 12,
                "oak_planks": 4,
            },
            status={"health": 20, "food": 20},
            nearby_blocks=["stone", "chest"],
        )

        task = planner.choose_next(
            state,
            previous_task="inventory_first",
            allow_optional=False,
            objective="base_establishment",
        )

        self.assertIsNotNone(task)
        self.assertEqual(task.task, "Craft 1 crafting_table")
        self.assertEqual(task.objective, "base_establishment")
        self.assertEqual(task.capability, "local_crafting_access")


class SmeltResultContractTests(unittest.TestCase):
    def test_edible_food_total_counts_cooked_rabbit(self):
        self.assertEqual(edible_food_total({"cooked_rabbit": 1}), 1)

    def test_outcome_policy_accepts_cooked_rabbit_as_edible_food(self):
        policy = CriticOutcomePolicy()

        result = policy.evaluate_post_action(
            "Acquire 1 edible food item",
            events=observe_event(
                inventory={"cooked_rabbit": 1},
                status={"health": 20, "food": 20},
                nearby_blocks=["stone"],
            ),
        )

        self.assertIsNotNone(result)
        self.assertTrue(result["success"])
        self.assertEqual(result["reason_code"], "food_already_in_inventory")

    def test_outcome_policy_does_not_accept_wheat_only_for_edible_food_contract(self):
        policy = CriticOutcomePolicy()

        result = policy.evaluate_post_action(
            "Acquire 1 edible food item",
            events=observe_event(
                inventory={"wheat": 3},
                status={"health": 20, "food": 20},
                nearby_blocks=["stone"],
            ),
        )

        self.assertIsNone(result)

    def test_world_effect_verifier_accepts_cooked_rabbit_as_edible_food(self):
        result = verify_task_effect(
            "Acquire 1 edible food item",
            before_events=observe_event(
                inventory={},
                status={"health": 20, "food": 20},
                nearby_blocks=["stone"],
            ),
            after_events=observe_event(
                inventory={"cooked_rabbit": 1},
                status={"health": 20, "food": 20},
                nearby_blocks=["stone"],
            ),
        )

        self.assertEqual(result["outcome"], "success")
        self.assertEqual(result["reason_code"], "edible_food_gained")

    def test_task_contract_canonicalizes_smelt_result_task_with_fuel_suffix(self):
        self.assertEqual(
            canonicalize_smelt_result_task("Have 16 raw_iron into iron_ingots using coal"),
            "Have 16 raw_iron into iron_ingots",
        )

    def test_outcome_policy_accepts_have_into_smelt_result_task(self):
        policy = CriticOutcomePolicy()

        result = policy.evaluate_post_action(
            "Have 8 raw_iron into iron_ingots",
            events=observe_event(
                inventory={
                    "iron_ingot": 18,
                    "raw_iron": 16,
                    "iron_pickaxe": 1,
                    "furnace": 1,
                    "coal": 1,
                },
                status={"health": 20, "food": 20},
                nearby_blocks=["stone", "furnace"],
            ),
        )

        self.assertIsNotNone(result)
        self.assertTrue(result["success"])
        self.assertIn(result["reason_code"], {"inventory_or_capability_satisfied", "inventory_threshold_met"})

    def test_outcome_policy_accepts_have_into_smelt_result_task_with_fuel_suffix(self):
        policy = CriticOutcomePolicy()

        result = policy.evaluate_post_action(
            "Have 16 raw_iron into iron_ingots using coal",
            events=observe_event(
                inventory={
                    "iron_ingot": 18,
                    "raw_iron": 16,
                    "iron_pickaxe": 1,
                    "furnace": 1,
                    "coal": 1,
                },
                status={"health": 20, "food": 20},
                nearby_blocks=["stone", "furnace"],
            ),
        )

        self.assertIsNotNone(result)
        self.assertTrue(result["success"])
        self.assertIn(result["reason_code"], {"inventory_or_capability_satisfied", "inventory_threshold_met"})

    def test_world_effect_verifier_reads_have_into_task_as_ingot_threshold(self):
        before_events = observe_event(
            inventory={"iron_ingot": 7, "raw_iron": 1, "furnace": 1, "coal": 1},
            status={"health": 20, "food": 20},
            nearby_blocks=["stone", "furnace"],
        )
        after_events = observe_event(
            inventory={"iron_ingot": 8, "raw_iron": 0, "furnace": 1},
            status={"health": 20, "food": 20},
            nearby_blocks=["stone", "furnace"],
        )

        result = verify_task_effect(
            "Have 8 raw_iron into iron_ingots",
            before_events=before_events,
            after_events=after_events,
        )

        self.assertEqual(result["outcome"], "success")
        self.assertEqual(result["reason_code"], "inventory_threshold_met")
        self.assertEqual(result["evidence"]["item"], "iron_ingot")

    def test_world_effect_verifier_reads_have_into_task_with_fuel_suffix_as_ingot_threshold(self):
        before_events = observe_event(
            inventory={"iron_ingot": 15, "raw_iron": 1, "furnace": 1, "coal": 1},
            status={"health": 20, "food": 20},
            nearby_blocks=["stone", "furnace"],
        )
        after_events = observe_event(
            inventory={"iron_ingot": 16, "raw_iron": 0, "furnace": 1},
            status={"health": 20, "food": 20},
            nearby_blocks=["stone", "furnace"],
        )

        result = verify_task_effect(
            "Have 16 raw_iron into iron_ingots using coal",
            before_events=before_events,
            after_events=after_events,
        )

        self.assertEqual(result["outcome"], "success")
        self.assertEqual(result["reason_code"], "inventory_threshold_met")
        self.assertEqual(result["evidence"]["item"], "iron_ingot")


class ActionValidatorTests(unittest.TestCase):
    def test_validator_rejects_bot_food_existence_check_as_food_inventory(self):
        validator = ActionValidatorPolicy()

        errors = validator.validate_program_code(
            """
            async function findFoodSource(bot) {
              const hasFood = bot.inventory.items().some((it) => it && it.name === "bread") || bot.food !== undefined;
              if (hasFood) return { success: true, reason: "FOOD_ALREADY_IN_INVENTORY_OR_SATISFYING" };
              return { success: false, reason: "NEEDS_REAL_FOOD" };
            }
            """,
            extract_explore_timeouts=lambda code: [],
        )

        self.assertTrue(errors)
        self.assertIn("bot.food existence", errors[0])


class ObjectiveTemplateTests(unittest.TestCase):
    def test_infer_armor_progression_objective(self):
        objective = infer_objective_template("Make full diamond armor", "")

        self.assertEqual(objective.id, "armor_progression")

    def test_infer_base_establishment_objective(self):
        objective = infer_objective_template("Settle a small base with storage", "")

        self.assertEqual(objective.id, "base_establishment")


class PersistentSessionFlowTests(unittest.TestCase):
    def _make_stub_voyager(self):
        voyager = object.__new__(Voyager)
        voyager.resume = True
        voyager.env_wait_ticks = 5
        voyager.max_iterations = 0
        voyager.recorder = SimpleNamespace(iteration=0)
        voyager.curriculum_agent = SimpleNamespace(
            set_objective_template=lambda *args, **kwargs: None,
            propose_next_task=lambda **kwargs: ("Acquire 1 edible food item", "Food first."),
            prepare_speculative_next_task=lambda task, events: None,
            last_speculative_decision=None,
            speculative_next_task=None,
            last_inventory_plan=None,
            completed_tasks=[],
            failed_tasks=[],
            update_exploration_progress=lambda info: None,
            summarize_failed_tasks=lambda: "None",
            get_task_context=lambda task: "Context",
        )
        reset_calls = []
        step_calls = []
        voyager.env = SimpleNamespace(
            reset=lambda options: reset_calls.append(options) or [],
            step=lambda code="": step_calls.append(code) or observe_event(),
        )
        voyager.refresh_live_state = lambda refresh_messages=False: None
        voyager._sync_search_policy_to_env = lambda: None
        voyager._ensure_execution_session = Voyager._ensure_execution_session.__get__(voyager, Voyager)
        voyager._set_phase = Voyager._set_phase.__get__(voyager, Voyager)
        voyager._next_trace_id = lambda prefix: f"{prefix}-1"
        voyager.execution_session = None
        voyager.last_phase = "initialized"
        voyager.last_phase_at = 0.0
        voyager.last_events = None
        voyager.last_rollout_info = None
        voyager.last_task_result = None
        voyager.last_task_result_at = None
        voyager.last_completion_reason = None
        voyager.last_success = None
        voyager.last_inventory_plan = None
        voyager.current_speculative_next_task = None
        voyager.last_speculative_decision = None
        voyager.skill_manager = SimpleNamespace(record_skill_outcome=lambda info: None, skills={})
        voyager._should_save_skill = lambda info: False
        rollout_calls = []
        def rollout(*, task, context, reset_env=True):
            rollout_calls.append({"task": task, "context": context, "reset_env": reset_env})
            voyager.recorder.iteration = voyager.max_iterations + 1
            return None, 0, True, {"task": task, "success": True, "completion_reason": "preflight_success"}
        voyager.rollout = rollout
        voyager.action_agent_task_max_retries = 4
        voyager.action_agent = SimpleNamespace(render_chest_observation=lambda: "")
        return voyager, reset_calls, step_calls, rollout_calls

    def test_learn_bootstraps_once_and_reuses_persistent_session(self):
        voyager, reset_calls, step_calls, rollout_calls = self._make_stub_voyager()

        result = Voyager.learn(voyager)

        self.assertEqual(len(reset_calls), 1)
        self.assertEqual(reset_calls[0]["mode"], "soft")
        self.assertEqual(len(step_calls), 1)
        self.assertEqual(len(rollout_calls), 1)
        self.assertFalse(rollout_calls[0]["reset_env"])
        self.assertEqual(voyager.last_phase, "objective_node_advanced")
        self.assertEqual(result["completed_tasks"], [])

    def test_inference_bootstraps_once_and_reuses_persistent_session(self):
        voyager, reset_calls, step_calls, rollout_calls = self._make_stub_voyager()
        voyager.curriculum_agent.completed_tasks = ["Acquire 1 edible food item"]

        result = Voyager.inference(voyager, task="Get food", sub_goals=["Acquire 1 edible food item"])

        self.assertEqual(len(reset_calls), 1)
        self.assertEqual(reset_calls[0]["mode"], "hard")
        self.assertEqual(len(step_calls), 1)
        self.assertEqual(len(rollout_calls), 1)
        self.assertFalse(rollout_calls[0]["reset_env"])
        self.assertEqual(result["status"], "completed")


class VerifierFirstOutcomeTests(unittest.TestCase):
    def _make_voyager_for_food_false_positive(self):
        def food_events():
            return [[
                "observe",
                {
                    "inventory": {},
                    "status": {"health": 20, "food": 20},
                    "nearby_blocks": ["stone"],
                    "voxels": [],
                    "nearbyChests": {},
                },
            ]]

        voyager = object.__new__(Voyager)
        voyager.task = "Acquire 1 edible food item"
        voyager.context = "Food first."
        voyager.messages = [
            SimpleNamespace(content="system"),
            SimpleNamespace(content="human"),
        ]
        voyager.conversations = []
        voyager.action_agent_rollout_num_iter = 0
        voyager.action_agent_task_max_retries = 4
        voyager.pending_countermeasure = None
        voyager.last_search_metrics = None
        voyager.last_world_effect_verification = None
        voyager.last_critic_result = None
        voyager.last_recovery_boundary = None
        voyager.current_task_bookkeeping = None
        voyager.last_task_bookkeeping = None
        voyager.last_rollout_info = None
        voyager.last_completion_reason = None
        voyager.last_success = None
        voyager.last_task_result = None
        voyager.last_task_result_at = None
        voyager.last_phase = "awaiting_action_llm"
        voyager.last_phase_at = 0.0
        voyager._trace_sequence = 0
        voyager.reset_placed_if_failed = False
        voyager.last_events = food_events()
        voyager.refresh_live_state = lambda refresh_messages=True: None
        voyager._set_phase = Voyager._set_phase.__get__(voyager, Voyager)
        voyager._next_trace_id = Voyager._next_trace_id.__get__(voyager, Voyager)
        voyager._observation_id_from_events = Voyager._observation_id_from_events.__get__(voyager, Voyager)
        voyager._start_task_bookkeeping = Voyager._start_task_bookkeeping.__get__(voyager, Voyager)
        voyager._set_task_bookkeeping = Voyager._set_task_bookkeeping.__get__(voyager, Voyager)
        voyager._bookkeeping_snapshot = Voyager._bookkeeping_snapshot.__get__(voyager, Voyager)
        voyager._classify_recovery_boundary = Voyager._classify_recovery_boundary.__get__(voyager, Voyager)
        voyager._apply_recovery_boundary = Voyager._apply_recovery_boundary.__get__(voyager, Voyager)
        voyager._active_countermeasure = lambda: None
        voyager._latest_event_payload = lambda events=None: (events or voyager.last_events)[-1][1] if (events or voyager.last_events) else {}
        voyager._event_position = lambda payload: None
        voyager._death_event_during_current_rollout = lambda events=None: None
        voyager.skill_manager = SimpleNamespace(
            programs={},
            retrieve_skills=lambda query: [],
        )
        voyager.recorder = SimpleNamespace(record=lambda events, task: None)
        voyager.action_agent = SimpleNamespace(
            llm=lambda messages: SimpleNamespace(content="async function acquireFood(bot) { return { success: true }; }"),
            process_ai_message=lambda message: {
                "program_code": "async function acquireFood(bot) { return { success: true, reason: 'FOOD_ALREADY_IN_INVENTORY_OR_SATISFYING' }; }",
                "exec_code": "await acquireFood(bot);",
                "program_name": "acquireFood",
            },
            update_chest_memory=lambda chests: None,
            render_chest_observation=lambda: "",
            summarize_chatlog=lambda events: "",
            render_system_message=lambda skills=None: SimpleNamespace(content="system"),
            render_human_message=lambda **kwargs: SimpleNamespace(content="human"),
        )
        critic_called = {"value": False}
        voyager.critic_agent = SimpleNamespace(
            preflight_task_success=lambda task, events=None: None,
            check_task_success_result=lambda **kwargs: critic_called.update(value=True) or {
                "success": True,
                "reason_code": "critic_should_not_run",
                "critique": "critic should not run after deterministic verifier fail",
            },
        )
        voyager.env = SimpleNamespace(step=lambda code, programs=None: food_events())
        return voyager, critic_called

    def test_step_uses_world_effect_verifier_before_accepting_food_success(self):
        voyager, critic_called = self._make_voyager_for_food_false_positive()

        _messages, _reward, done, info = Voyager.step(voyager)

        self.assertFalse(done)
        self.assertFalse(info["success"])
        self.assertEqual(info["completion_reason"], "retrying")
        self.assertEqual(info["world_effect_verification"]["outcome"], "fail")
        self.assertEqual(info["world_effect_verification"]["reason_code"], "edible_food_missing")
        self.assertFalse(critic_called["value"])


class ResetGuardrailTests(unittest.TestCase):
    def _make_voyager_for_reset_guard(self):
        voyager = object.__new__(Voyager)
        voyager.env_wait_ticks = 5
        voyager.last_phase = "initialized"
        voyager.last_phase_at = 0.0
        voyager.task = None
        voyager.execution_session = None
        voyager.reset_audit_log = []
        voyager._next_trace_id = lambda prefix: f"{prefix}-1"
        voyager._set_phase = Voyager._set_phase.__get__(voyager, Voyager)
        voyager._ensure_execution_session = Voyager._ensure_execution_session.__get__(voyager, Voyager)
        voyager._record_reset_audit = Voyager._record_reset_audit.__get__(voyager, Voyager)
        voyager._guarded_env_reset = Voyager._guarded_env_reset.__get__(voyager, Voyager)
        reset_calls = []
        voyager.env = SimpleNamespace(reset=lambda options: reset_calls.append(options) or [])
        voyager._ensure_execution_session(mode="learn", bootstrap_reset="soft")
        return voyager, reset_calls

    def test_guarded_env_reset_allows_recovery(self):
        voyager, reset_calls = self._make_voyager_for_reset_guard()

        Voyager._guarded_env_reset(voyager, cause="recovery", mode="hard", detail="test recovery")

        self.assertEqual(len(reset_calls), 1)
        self.assertEqual(reset_calls[0]["mode"], "hard")
        self.assertEqual(voyager.execution_session["recovery_reset_count"], 1)
        self.assertEqual(voyager.execution_session["unexpected_reset_count"], 0)

    def test_guarded_env_reset_rejects_unexpected_cause(self):
        voyager, reset_calls = self._make_voyager_for_reset_guard()

        with self.assertRaises(RuntimeError):
            Voyager._guarded_env_reset(voyager, cause="task_turnover", mode="soft", detail="should fail")

        self.assertEqual(len(reset_calls), 0)
        self.assertEqual(voyager.execution_session["unexpected_reset_count"], 1)
        self.assertEqual(voyager.last_phase, "reset_policy_violation")


class DisplayStageTests(unittest.TestCase):
    def test_task_session_start_maps_to_executing_task(self):
        stage = _compute_display_stage(
            running=True,
            current_task="Acquire 1 edible food item",
            last_phase="task_session_start",
            last_error=None,
            current_task_bookkeeping={"status": "running"},
            last_task_result=None,
        )

        self.assertEqual(stage, "executing_task")

    def test_objective_node_advanced_maps_to_between_tasks(self):
        stage = _compute_display_stage(
            running=True,
            current_task="Acquire 1 edible food item",
            last_phase="objective_node_advanced",
            last_error=None,
            current_task_bookkeeping={"status": "completed"},
            last_task_result={"success": True},
        )

        self.assertEqual(stage, "between_tasks")

    def test_error_maps_to_blocked(self):
        stage = _compute_display_stage(
            running=True,
            current_task="Acquire 1 edible food item",
            last_phase="awaiting_action_llm",
            last_error="runner_exception",
            current_task_bookkeeping={"status": "running"},
            last_task_result=None,
        )

        self.assertEqual(stage, "blocked")


class StabilitySignalTests(unittest.TestCase):
    def test_unexpected_reset_produces_alert(self):
        signals = _stability_signals(
            display_stage="executing_task",
            last_phase_at=100.0,
            execution_session={
                "reset_count": 2,
                "recovery_reset_count": 0,
                "unexpected_reset_count": 1,
            },
            reset_audit_log=[{"cause": "task_turnover", "allowed": False}],
            now_ts=110.0,
        )

        self.assertFalse(signals["healthy"])
        self.assertIn("unexpected_reset_detected", signals["alerts"])

    def test_between_tasks_stall_produces_alert(self):
        signals = _stability_signals(
            display_stage="between_tasks",
            last_phase_at=100.0,
            execution_session={
                "reset_count": 1,
                "recovery_reset_count": 0,
                "unexpected_reset_count": 0,
            },
            reset_audit_log=[],
            now_ts=131.0,
        )

        self.assertFalse(signals["healthy"])
        self.assertIn("between_tasks_stalled", signals["alerts"])

    def test_plan_churn_produces_alert(self):
        now_ts = 200.0
        history = [
            {"transition": "selected", "recorded_at": 100.0},
            {"transition": "advanced_to_next_node", "recorded_at": 110.0},
            {"transition": "selected", "recorded_at": 120.0},
            {"transition": "current_node_failed", "recorded_at": 130.0},
            {"transition": "selected", "recorded_at": 140.0},
            {"transition": "advanced_to_next_node", "recorded_at": 150.0},
        ]
        signals = _stability_signals(
            display_stage="executing_task",
            last_phase_at=195.0,
            execution_session={
                "reset_count": 1,
                "recovery_reset_count": 0,
                "unexpected_reset_count": 0,
            },
            reset_audit_log=[],
            active_plan_state={"transition_history": history},
            now_ts=now_ts,
        )

        self.assertFalse(signals["healthy"])
        self.assertIn("plan_churn_detected", signals["alerts"])
        self.assertEqual(signals["recent_plan_transition_count"], 6)


class ActivePlanStateTests(unittest.TestCase):
    def _make_curriculum_stub(self):
        curriculum = object.__new__(CurriculumAgent)
        curriculum.current_objective_template = infer_objective_template("", "")
        curriculum.active_plan_state = None
        curriculum.failed_tasks = []
        curriculum.completed_tasks = []
        curriculum.normalize_task = lambda task: str(task or "").strip()
        curriculum._completed_task_names = lambda: set(curriculum.completed_tasks)
        curriculum._extract_live_inventory_state = lambda events: ({}, {}, {})
        curriculum._is_repeatable_state_task = lambda task: False
        curriculum._task_inventory_satisfied = lambda task, inventory: False
        curriculum._recent_blocking_failure = lambda task, events: None
        return curriculum

    def test_active_plan_promotes_pending_successor_after_success(self):
        curriculum = self._make_curriculum_stub()

        curriculum._start_or_refresh_active_plan(
            task="Acquire 1 edible food item",
            context="Food first.",
            capability="food_security",
            reason="bootstrap_food",
            source="inventory_first",
            chain=[{"task": "Acquire 1 edible food item", "context": "Food first.", "capability": "food_security", "reason": "bootstrap_food"}],
        )
        curriculum._queue_pending_plan_node(
            trigger_task="Acquire 1 edible food item",
            next_task="Craft 1 iron_pickaxe",
            context="Advance tools.",
            reason="iron_progression",
            expected_minimums={"food": 1},
        )

        curriculum._apply_task_result_to_active_plan({"task": "Acquire 1 edible food item", "success": True})

        self.assertIsNotNone(curriculum.active_plan_state)
        self.assertEqual(curriculum.active_plan_state["last_transition"], "advanced_to_next_node")
        self.assertEqual(curriculum.active_plan_state["current_node"]["task"], "Craft 1 iron_pickaxe")
        self.assertEqual(curriculum.active_plan_state["current_node"]["status"], "ready")
        self.assertTrue(len(curriculum.active_plan_state["transition_history"]) >= 2)

    def test_consume_active_plan_reuses_current_node(self):
        curriculum = self._make_curriculum_stub()
        curriculum._start_or_refresh_active_plan(
            task="Craft 1 iron_pickaxe",
            context="Advance tools.",
            capability="iron_pickaxe",
            reason="iron_progression",
            source="inventory_first",
        )

        result = curriculum._consume_active_plan_task(observe_event())

        self.assertEqual(result, ("Craft 1 iron_pickaxe", "Advance tools."))
        self.assertEqual(curriculum.active_plan_state["last_transition"], "reused_current_node")


if __name__ == "__main__":
    unittest.main()
