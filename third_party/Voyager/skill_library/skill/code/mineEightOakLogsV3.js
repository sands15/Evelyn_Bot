async function mineEightOakLogs(bot) {
  const oakLogItem = mcData.itemsByName["oak_log"];
  function oakLogCount() {
    return bot.inventory.count(oakLogItem.id, null);
  }
  async function mineNearbyOakLogs() {
    const needed = 8 - oakLogCount();
    if (needed <= 0) return;
    const oakLogs = bot.findBlocks({
      matching: block => block.name === "oak_log",
      maxDistance: 32,
      count: needed
    });
    if (oakLogs.length > 0) {
      await mineBlock(bot, "oak_log", Math.min(oakLogs.length, needed));
    }
  }
  if (oakLogCount() >= 8) return;
  await mineNearbyOakLogs();
  if (oakLogCount() >= 8) return;
  const directions = [new Vec3(1, 0, 1), new Vec3(-1, 0, -1)];
  for (const direction of directions) {
    const foundOakLog = await exploreUntil(bot, direction, 15, () => {
      return bot.findBlock({
        matching: block => block.name === "oak_log",
        maxDistance: 32
      });
    });
    if (foundOakLog) {
      await mineNearbyOakLogs();
      if (oakLogCount() >= 8) return;
    }
  }
  throw new Error("LOCAL_SEARCH_EXHAUSTED: could not find 8 oak_log nearby after two short surface probes.");
}