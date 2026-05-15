async function obtainEightOakLogs(bot) {
  const oakLogItem = mcData.itemsByName["oak_log"];
  function oakLogCount() {
    return bot.inventory.count(oakLogItem.id, null);
  }
  function nearbyOakLogCount(limit) {
    return bot.findBlocks({
      matching: block => block.name === "oak_log",
      maxDistance: 32,
      count: limit
    }).length;
  }
  async function mineNearbyOakLogs() {
    if (oakLogCount() >= 8) return;
    const needed = 8 - oakLogCount();
    const available = nearbyOakLogCount(needed);
    if (available <= 0) return;
    await mineBlock(bot, "oak_log", Math.min(available, needed));
  }
  if (oakLogCount() >= 8) return;
  await mineNearbyOakLogs();
  if (oakLogCount() >= 8) return;
  const directions = [new Vec3(1, 0, 1), new Vec3(-1, 0, -1)];
  for (const direction of directions) {
    if (oakLogCount() >= 8) return;
    const found = await exploreUntil(bot, direction, 15, () => {
      return bot.findBlock({
        matching: block => block.name === "oak_log",
        maxDistance: 32
      });
    });
    if (found) {
      await mineNearbyOakLogs();
    }
  }
  if (oakLogCount() < 8) {
    throw new Error("LOCAL_SEARCH_EXHAUSTED: fewer than 8 oak_log were available nearby after two short surface probes.");
  }
}