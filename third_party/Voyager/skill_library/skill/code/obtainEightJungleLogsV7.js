async function obtainEightJungleLogs(bot) {
  const required = 8;
  const jungleLogItem = mcData.itemsByName["jungle_log"];
  function jungleLogCount() {
    return bot.inventory.count(jungleLogItem.id, null);
  }
  if (jungleLogCount() >= required) {
    return;
  }
  let needed = required - jungleLogCount();
  const nearbyLogs = bot.findBlocks({
    matching: block => block.name === "jungle_log",
    maxDistance: 32,
    count: needed
  });
  if (nearbyLogs.length === 0) {
    const foundLog = await exploreUntil(bot, new Vec3(1, 0, 1), 60, () => {
      return bot.findBlock({
        matching: mcData.blocksByName["jungle_log"].id,
        maxDistance: 32
      });
    });
    if (!foundLog) {
      throw new Error("Could not find jungle logs nearby.");
    }
  }
  if (jungleLogCount() >= required) {
    return;
  }
  needed = required - jungleLogCount();
  await mineBlock(bot, "jungle_log", needed);
  if (jungleLogCount() < required) {
    throw new Error("Failed to obtain 8 jungle logs.");
  }
}