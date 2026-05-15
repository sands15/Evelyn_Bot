async function obtainEightJungleLogs(bot) {
  const required = 8;
  const jungleLog = mcData.itemsByName["jungle_log"];
  function currentCount() {
    return bot.inventory.count(jungleLog.id, null);
  }
  if (currentCount() >= required) {
    return;
  }
  let needed = required - currentCount();
  const nearbyLogs = bot.findBlocks({
    matching: block => block.name === "jungle_log",
    maxDistance: 32,
    count: needed
  });
  if (nearbyLogs.length > 0) {
    await mineBlock(bot, "jungle_log", needed);
    if (currentCount() >= required) {
      return;
    }
  }
  const foundLog = await exploreUntil(bot, new Vec3(1, 0, 1), 60, () => {
    return bot.findBlock({
      matching: mcData.blocksByName["jungle_log"].id,
      maxDistance: 32
    });
  });
  if (!foundLog) {
    throw new Error("Could not find jungle_log nearby.");
  }
  needed = required - currentCount();
  if (needed > 0) {
    await mineBlock(bot, "jungle_log", needed);
  }
  if (currentCount() < required) {
    throw new Error("Failed to obtain 8 jungle_log.");
  }
}