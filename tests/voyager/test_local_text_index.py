from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
VOYAGER_ROOT = REPO_ROOT / "third_party" / "Voyager"
CORE_RUNTIME = REPO_ROOT / "evelyn_core" / "runtime"
if str(CORE_RUNTIME) not in sys.path:
    sys.path.insert(0, str(CORE_RUNTIME))
if str(VOYAGER_ROOT) not in sys.path:
    sys.path.insert(0, str(VOYAGER_ROOT))

from voyager.agents.local_text_index import LocalTextIndex  # noqa: E402


class LocalTextIndexTests(unittest.TestCase):
    def test_import_does_not_load_requests_or_heavy_voyager_runtime(self) -> None:
        script = textwrap.dedent(
            f"""
            import sys

            class BlockRequests:
                def find_spec(self, fullname, path=None, target=None):
                    if fullname == "requests" or fullname.startswith("requests."):
                        raise ModuleNotFoundError("requests deliberately unavailable")
                    return None

            sys.meta_path.insert(0, BlockRequests())
            sys.path.insert(0, {str(VOYAGER_ROOT)!r})

            from voyager.agents.local_text_index import LocalTextIndex

            assert "requests" not in sys.modules
            assert "voyager.voyager" not in sys.modules
            assert "voyager.agents.action" not in sys.modules
            assert "voyager.agents.critic" not in sys.modules
            assert "voyager.agents.curriculum" not in sys.modules
            assert "voyager.agents.skill" not in sys.modules
            assert LocalTextIndex(collection_name="smoke")._collection.count() == 0

            import types
            import voyager
            import voyager.agents as agents

            assert voyager.__all__ == ["Voyager"]
            assert agents.__all__ == [
                "ActionAgent",
                "CriticAgent",
                "CurriculumAgent",
                "SkillManager",
            ]
            assert "Voyager" in dir(voyager)
            assert all(name in dir(agents) for name in agents.__all__)

            voyager_class = type("Voyager", (), {{}})
            voyager_module = types.ModuleType("voyager.voyager")
            voyager_module.Voyager = voyager_class
            sys.modules["voyager.voyager"] = voyager_module

            agent_classes = {{}}
            for export_name, module_name in (
                ("ActionAgent", "action"),
                ("CriticAgent", "critic"),
                ("CurriculumAgent", "curriculum"),
                ("SkillManager", "skill"),
            ):
                agent_class = type(export_name, (), {{}})
                agent_module = types.ModuleType(f"voyager.agents.{{module_name}}")
                setattr(agent_module, export_name, agent_class)
                sys.modules[agent_module.__name__] = agent_module
                agent_classes[export_name] = agent_class

            from voyager import Voyager
            from voyager.agents import ActionAgent, CriticAgent, CurriculumAgent, SkillManager

            assert Voyager is voyager_class
            assert ActionAgent is agent_classes["ActionAgent"]
            assert CriticAgent is agent_classes["CriticAgent"]
            assert CurriculumAgent is agent_classes["CurriculumAgent"]
            assert SkillManager is agent_classes["SkillManager"]
            """
        )

        result = subprocess.run(
            [sys.executable, "-I", "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

        self.assertEqual(
            result.returncode,
            0,
            msg=f"isolated import failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

    def test_skill_query_prefers_matching_local_description(self) -> None:
        index = LocalTextIndex(collection_name="skills")
        index.add_texts(
            texts=[
                "Mine and collect a reachable wood log with pathfinding.",
                "Smelt raw iron into iron ingots using a furnace.",
            ],
            ids=["mineWood", "smeltIron"],
            metadatas=[{"name": "mineWood"}, {"name": "smeltIron"}],
        )

        results = index.similarity_search_with_score("Mine 1 wood log", k=2)

        self.assertEqual(results[0][0].metadata["name"], "mineWood")
        self.assertLess(results[0][1], results[1][1])

    def test_normalized_near_exact_question_meets_qa_cache_threshold(self) -> None:
        index = LocalTextIndex(collection_name="qa")
        index.add_texts(texts=["How do I craft a wooden pickaxe?"])

        results = index.similarity_search_with_score("how do i craft a wooden pickaxe", k=1)

        self.assertEqual(results[0][1], 0.0)

    def test_collection_contract_supports_count_get_and_delete(self) -> None:
        index = LocalTextIndex(collection_name="contract")
        index.add_texts(texts=["first", "second"], ids=["a", "b"])

        self.assertEqual(index._collection.count(), 2)
        self.assertEqual(index._collection.get()["ids"], ["a", "b"])
        index._collection.delete(ids=["a"])
        self.assertEqual(index._collection.get()["ids"], ["b"])

    def test_skill_index_marks_unrelated_long_program_as_irrelevant(self) -> None:
        index = LocalTextIndex(collection_name="skill_vectordb")
        index.add_texts(
            texts=[
                "async function mineLapis(bot) { check inventory before mining lapis ore; }",
                "Build a temporary shelter with walls, a roof, and lighting.",
            ],
            ids=["mineLapis", "buildTemporaryShelter"],
            metadatas=[{"name": "mineLapis"}, {"name": "buildTemporaryShelter"}],
        )

        results = index.similarity_search_with_score("Establish a lit temporary shelter", k=2)

        self.assertEqual(results[0][0].metadata["name"], "buildTemporaryShelter")
        self.assertLess(results[0][1], 1.0)
        self.assertEqual(results[1][1], 1.0)


if __name__ == "__main__":
    unittest.main()
