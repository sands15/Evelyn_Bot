async function mineEightOakLogs(bot) {
  const oakLogItem = mcData.itemsByName["oak_log"];
  function oakLogCount() {
    return bot.inventory.count(oakLogItem.id, null);
  }
  function findNearbyOakLog() {
    return bot.findBlock({
      matching: block => block.name === "oak_log",
      maxDistance: 32
    });
  }
  async function mineOneNearbyOakLog() {
    const before = oakLogCount();
    const oakLog = findNearbyOakLog();
    if (!oakLog) return false;
    await mineBlock(bot, "oak_log", 1);
    if (oakLogCount() <= before) {
      throw new Error("Failed to collect oak_log after mining nearby block.");
    }
    return true;
  }
  if (oakLogCount() >= 8) return;
  for (let i = 0; i < 8; i++) {
    if (oakLogCount() >= 8) return;
    if (!findNearbyOakLog()) break;
    await mineOneNearbyOakLog();
  }
  if (oakLogCount() >= 8) return;
  const probeDirections = [new Vec3(1, 0, 1), new Vec3(-1, 0, -1)];
  for (const direction of probeDirections) {
    if (oakLogCount() >= 8) return;
    const foundOakLog = await exploreUntil(bot, direction, 15, () => {
      return findNearbyOakLog();
    });
    if (!foundOakLog) continue;
    for (let i = 0; i < 8; i++) {
      if (oakLogCount() >= 8) return;
      if (!findNearbyOakLog()) break;
      await mineOneNearbyOakLog();
    }
  }
  throw new Error("LOCAL_SEARCH_EXHAUSTED: could not collect 8 oak_log nearby; reachable oak logs were insufficient or inefficient in this terrain.");
}