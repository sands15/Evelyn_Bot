from __future__ import annotations

import json
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.minecraft_world_lease_contract import (
    load_valid_world_lease,
)
from evelyn_core.paths import get_runtime_artifacts_root

SERVICE_URL = "http://127.0.0.1:8765"
BRIDGE_URL = "http://127.0.0.1:3000"
DEFAULT_MC_PORT = 25565
WORLD_LEASE_STATUS_PATH = (
    get_runtime_artifacts_root()
    / "minecraft_world_lease"
    / "status.json"
)


@dataclass
class ChestRegressionResult:
    mode: str
    success: bool
    detail: str
    event_types: list[str]
    interaction: dict[str, Any]
    window_result: dict[str, Any] | None


def _post_json(url: str, payload: dict[str, Any], timeout: int = 60) -> Any:
    response = requests.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if isinstance(data, str):
        return json.loads(data)
    return data


def ensure_bridge_ready(timeout_seconds: int = 45) -> None:
    lease, error = load_valid_world_lease(
        WORLD_LEASE_STATUS_PATH,
    )
    if not lease:
        raise RuntimeError(
            "Minecraft world lease is required before regression: "
            f"{error}"
        )
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", 3000), timeout=1):
                return
        except OSError:
            time.sleep(1)
    raise RuntimeError(
        "Mineflayer bridge did not become ready under the active "
        "world lease"
    )


def attach_bridge(mc_port: int = DEFAULT_MC_PORT) -> None:
    lease, error = load_valid_world_lease(
        WORLD_LEASE_STATUS_PATH,
    )
    if not lease:
        raise RuntimeError(
            "Minecraft world lease is required before bridge attach: "
            f"{error}"
        )
    _post_json(
        f"{BRIDGE_URL}/start",
        {
            "port": mc_port,
            "reset": "soft",
            "inventory": {},
            "equipment": [],
            "spread": False,
            "waitTicks": 20,
            "position": None,
        },
        timeout=60,
    )


def _step_payload(blocked: bool) -> str:
    blocked_literal = "true" if blocked else "false"
    label = "blocked chest regression" if blocked else "normal chest regression"
    template = '''
const AIR_NAMES = new Set(["air", "cave_air", "void_air"]);
const chestIds = [mcData.blocksByName.chest.id, mcData.blocksByName.trapped_chest.id].filter(Boolean);
const chestPositions = bot.findBlocks({
  matching: (block) => chestIds.includes(block.type),
  maxDistance: 16,
  count: 16,
});
if (!chestPositions || chestPositions.length === 0) {
  throw new Error("No nearby chest found for regression test");
}
let chestBlock = null;
for (const pos of chestPositions) {
  const candidate = bot.blockAt(pos);
  if (!candidate) continue;
  const above = bot.blockAt(candidate.position.offset(0, 1, 0));
  const blockedAbove = above && above.boundingBox === "block";
  if (!__BLOCKED__ && blockedAbove) continue;
  chestBlock = candidate;
  break;
}
if (!chestBlock) {
  chestBlock = bot.blockAt(chestPositions[0]);
}
if (!chestBlock) {
  throw new Error("Chest block disappeared during regression test");
}
const chestParts = [chestBlock];
for (const offset of [new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1)]) {
  const neighbor = bot.blockAt(chestBlock.position.plus(offset));
  if (neighbor && neighbor.name === chestBlock.name && !chestParts.find((block) => block.position.equals(neighbor.position))) {
    chestParts.push(neighbor);
  }
}
const aboveEntries = chestParts.map((block) => {
  const abovePos = block.position.offset(0, 1, 0);
  const aboveBlock = bot.blockAt(abovePos);
  const aboveName = aboveBlock && aboveBlock.name ? aboveBlock.name : "air";
  return {
    abovePos,
    restoreName: AIR_NAMES.has(aboveName) ? "air" : aboveName,
  };
});
for (const entry of aboveEntries) {
  if (__BLOCKED__) {
    bot.chat(`/setblock ${entry.abovePos.x} ${entry.abovePos.y} ${entry.abovePos.z} stone`);
  } else {
    bot.chat(`/setblock ${entry.abovePos.x} ${entry.abovePos.y} ${entry.abovePos.z} air`);
  }
}
await bot.waitForTicks(20);
const appliedAboveStates = aboveEntries.map((entry) => {
  const block = bot.blockAt(entry.abovePos);
  return {
    name: block && block.name ? block.name : "missing",
    solid: !!block && block.boundingBox === "block",
  };
});
if (__BLOCKED__ && !appliedAboveStates.some((state) => state.solid)) {
  throw new Error(`Blocked regression setup failed: ${appliedAboveStates.map((state) => state.name).join(", ")}`);
}
try {
  bot.chat(`/tp @s ${chestBlock.position.x} ${chestBlock.position.y} ${chestBlock.position.z - 1}`);
  await bot.waitForTicks(10);
  await bot.lookAt(chestBlock.position.offset(0.5, 0.5, 0.5), true);
  const chest = await bot.openContainer(chestBlock);
  await bot.waitForTicks(10);
  if (chest && typeof chest.close === "function") {
    await chest.close();
  }
} finally {
  for (const entry of aboveEntries) {
    bot.chat(`/setblock ${entry.abovePos.x} ${entry.abovePos.y} ${entry.abovePos.z} ${entry.restoreName}`);
  }
  await bot.waitForTicks(10);
}
'''
    return template.replace("__BLOCKED__", blocked_literal).replace("__LABEL__", label)


def run_case(mode: str, mc_port: int = DEFAULT_MC_PORT) -> ChestRegressionResult:
    blocked = mode == "blocked"
    ensure_bridge_ready()
    attach_bridge(mc_port=mc_port)
    events = _post_json(
        f"{BRIDGE_URL}/step",
        {"code": _step_payload(blocked), "programs": ""},
        timeout=180,
    )
    event_types = [event_type for event_type, _ in events]
    last_observation = events[-1][1] if events else {}
    interaction = last_observation.get("voyagerContainerInteraction") or {}
    window_result = last_observation.get("voyagerWindowResult")
    errors = [payload.get("onError") for event_type, payload in events if event_type == "onError" and isinstance(payload, dict)]

    if blocked:
        success = bool(interaction.get("blockedAbove")) and not bool(interaction.get("opened")) and not bool(interaction.get("interacted"))
        detail = errors[0] if errors else interaction.get("error") or "Blocked chest did not emit the expected blocked-above signal"
    else:
        success = not bool(interaction.get("blockedAbove")) and (bool(interaction.get("opened")) or bool(interaction.get("interacted")))
        detail = errors[0] if errors else interaction.get("error") or json.dumps(interaction, ensure_ascii=False)

    return ChestRegressionResult(
        mode=mode,
        success=success,
        detail=str(detail),
        event_types=event_types,
        interaction=interaction,
        window_result=window_result,
    )


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else "normal"
    if mode not in {"normal", "blocked"}:
        raise SystemExit("Usage: python chest_regression_common.py [normal|blocked]")
    result = run_case(mode)
    print(json.dumps({
        "mode": result.mode,
        "success": result.success,
        "detail": result.detail,
        "event_types": result.event_types,
        "interaction": result.interaction,
        "window_result": result.window_result,
    }, ensure_ascii=False, indent=2))
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
