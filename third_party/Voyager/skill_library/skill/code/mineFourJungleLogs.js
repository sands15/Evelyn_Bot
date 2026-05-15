async function mineFourJungleLogs(bot) {
  const required = 4;
  const jungleLogItem = mcData.itemsByName["jungle_log"];
  function jungleLogCount() {
    return bot.inventory.count(jungleLogItem.id, null);
  }
  async function mineNearbyJungleLogs() {
    const remaining = required - jungleLogCount();
    if (remaining <= 0) return;
    const nearbyLogs = bot.findBlocks({
      matching: block => block.name === "jungle_log",
      maxDistance: 32,
      count: remaining
    });
    if (nearbyLogs.length > 0) {
      await mineBlock(bot, "jungle_log", Math.min(nearbyLogs.length, remaining));
    }
  }
  if (jungleLogCount() >= required) return;
  await mineNearbyJungleLogs();
  if (jungleLogCount() >= required) return;
  await exploreUntil(bot, new Vec3(1, 0, 1), 15, () => {
    return bot.findBlock({
      matching: mcData.blocksByName["jungle_log"].id,
      maxDistance: 32
    });
  });
  await mineNearbyJungleLogs();
  if (jungleLogCount() >= required) return;
  await exploreUntil(bot, new Vec3(-1, 0, -1), 15, () => {
    return bot.findBlock({
      matching: mcData.blocksByName["jungle_log"].id,
      maxDistance: 32
    });
  });
  await mineNearbyJungleLogs();
  if (jungleLogCount() >= required) return;
  throw new Error("LOCAL_SEARCH_EXHAUSTED: fewer than 4 jungle_log were available nearby after two short local probes.");
}