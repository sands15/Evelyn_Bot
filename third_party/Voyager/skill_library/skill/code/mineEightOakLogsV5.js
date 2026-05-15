async function mineEightOakLogs(bot) {
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
    const needed = 8 - oakLogCount();
    if (needed <= 0) return;
    const available = nearbyOakLogCount(needed);
    if (available <= 0) return;
    await mineBlock(bot, "oak_log", Math.min(available, needed));
  }
  if (oakLogCount() >= 8) return;
  await mineNearbyOakLogs();
  if (oakLogCount() >= 8) return;
  const probeDirections = [new Vec3(1, 0, 1), new Vec3(-1, 0, -1)];
  for (const direction of probeDirections) {
    if (oakLogCount() >= 8) return;
    const foundOakLog = await exploreUntil(bot, direction, 15, () => {
      return bot.findBlock({
        matching: block => block.name === "oak_log",
        maxDistance: 32
      });
    });
    if (foundOakLog) {
      await mineNearbyOakLogs();
    }
  }
  if (oakLogCount() < 8) {
    throw new Error("LOCAL_SEARCH_EXHAUSTED: could not find enough oak_log nearby in the current terrain.");
  }
}