from __future__ import annotations
import sys, unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock
REPO_ROOT=next(p for p in Path(__file__).resolve().parents if (p/"main.py").exists()); RUNTIME_ROOT=REPO_ROOT/"evelyn_core"/"runtime"
if str(RUNTIME_ROOT) not in sys.path: sys.path.insert(0,str(RUNTIME_ROOT))
from evelyn_core.minecraft_mode_composition import MinecraftModeComposition, MinecraftModeCompositionDeps

class MinecraftModeCompositionTests(unittest.IsolatedAsyncioTestCase):
    def build(self,statuses):
        client=Mock(); client.status=AsyncMock(side_effect=statuses); client.start=AsyncMock(return_value={"voyager_repo_present":True}); client.stop=AsyncMock()
        times=iter([0.0,0.0,0.2,1.0,2.0]); deps=MinecraftModeCompositionDeps(get_client=lambda:client,merge_status=lambda a,b:dict(b or {}),clean_text=str.strip,monotonic=lambda:next(times),sleep=AsyncMock())
        return MinecraftModeComposition(deps),client
    async def test_wait_returns_connected_observation(self):
        c,_=self.build([{"observation":{"connected":True,"position":{"x":1}}}]); result=await c.wait_for_minecraft_ready(1)
        self.assertTrue(result["connected"])
    async def test_enable_and_disable_use_same_client(self):
        c,client=self.build([{"connected":True}]); result=await c.enable_minecraft_mode(1,"goal"); await c.disable_minecraft_mode(1)
        self.assertTrue(result["voyager_repo_present"]); client.stop.assert_awaited_once_with()
    def test_main_bindings(self):
        s=(REPO_ROOT/"main.py").read_text(encoding="utf-8"); self.assertIn("minecraft_mode_composition = MinecraftModeComposition(",s)
        self.assertIn("enable_minecraft_mode = minecraft_mode_composition.enable_minecraft_mode",s)
if __name__=="__main__": unittest.main()
