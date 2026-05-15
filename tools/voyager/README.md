# Voyager tools

- debug/ → ad-hoc Voyager/Minecraft debug helpers and temp probes.
- prompts/ → scratch prompt/snippet files used during Voyager debugging.

Left in project root on purpose:
- minecraft_status.json
- minecraft_debug.log
- oyager_service.log

Those are still produced/consumed by the current runtime flow, so moving them now would risk breaking live automation.
