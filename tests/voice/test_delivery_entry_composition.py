from __future__ import annotations
import sys, unittest
from pathlib import Path
from unittest.mock import Mock, patch
REPO_ROOT=next(p for p in Path(__file__).resolve().parents if (p/"main.py").exists()); RUNTIME_ROOT=REPO_ROOT/"evelyn_core"/"runtime"
if str(RUNTIME_ROOT) not in sys.path: sys.path.insert(0,str(RUNTIME_ROOT))
from evelyn_core.delivery_entry_composition import DiscordDeliveryEntryDeps, DeliveryEntryComposition, LocalDeliveryEntryDeps
import evelyn_core.delivery_entry_composition as delivery_entry

class DeliveryEntryCompositionTests(unittest.TestCase):
    def build(self):
        deps=LocalDeliveryEntryDeps(memory_index_dir=Path("unused-memory-index"),queue_factory=Mock(return_value="queue"),sink_factory=Mock(return_value="sink"),
            stream_local_tts_sentences=Mock(return_value="stream"),create_scoped_task=Mock(return_value="task"),
            streaming_delivery_factory=Mock(return_value="delivery"),log_voice_stage=Mock(),mark_turn_stage=Mock(),
            log_voice_latency=Mock(),local_control_tts=lambda:"local-deps",prefetch_chunks=2,log=Mock())
        discord=DiscordDeliveryEntryDeps(memory_index_dir=Path("unused-memory-index"),request_factory=Mock(return_value="request"),build_streaming_delivery=Mock(return_value="discord-delivery"),
            stream_tts_sentences=Mock(),create_scoped_task=Mock(),log_voice_stage=Mock(),prefetch_chunks=2,log=Mock())
        return DeliveryEntryComposition(deps,discord),deps
    def test_first_playback_marks_once_and_logs(self):
        c,d=self.build(); c.mark_local_tts_first_playback({"marks":{}},turn_id="t",chunk_index=1,session_key="s")
        d.mark_turn_stage.assert_called_once(); d.log_voice_latency.assert_called_once()
    def test_streaming_local_builds_delivery(self):
        c,d=self.build(); self.assertEqual(c.start_streaming_local_voice_delivery(metrics={},turn_id="t",session_key="s",turn_scope=None),"delivery")
        d.create_scoped_task.assert_not_called()
        start_task=d.streaming_delivery_factory.call_args.args[2]
        self.assertEqual(start_task(),"task")
        guarded_coro=d.create_scoped_task.call_args.args[0]
        guarded_coro.close()
    def test_local_playback_binds_exposure_when_lazy_task_starts(self):
        c,d=self.build()
        with patch.object(delivery_entry,"current_memory_exposure_position",return_value="E") as current:
            c.start_streaming_local_voice_delivery(metrics={},turn_id="t",session_key="s",turn_scope=None)
            current.assert_not_called()
            start_task=d.streaming_delivery_factory.call_args.args[2]
            start_task()
            current.assert_called_once_with()
            d.create_scoped_task.call_args.args[0].close()
    def test_local_control_delegates(self):
        c,_=self.build()
        with patch("evelyn_core.delivery_entry_composition.schedule_local_control_tts_from_runtime",return_value="task") as runtime:
            self.assertEqual(c.schedule_local_control_tts("answer"),"task")
        self.assertEqual(runtime.call_args.kwargs["deps"],"local-deps")
    def test_discord_streaming_builds_request(self):
        c,_=self.build(); self.assertEqual(c.start_streaming_voice_delivery("vc",metrics={},turn_id="t",session_key="s",turn_scope=None),"discord-delivery")
    def test_main_bindings(self):
        s=(REPO_ROOT/"main.py").read_text(encoding="utf-8"); self.assertIn("delivery_entry_composition = DeliveryEntryComposition(",s)
        self.assertIn("schedule_local_control_tts = delivery_entry_composition.schedule_local_control_tts",s)
if __name__=="__main__": unittest.main()
