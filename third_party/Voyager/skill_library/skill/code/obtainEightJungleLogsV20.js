async function obtainEightJungleLogs(bot) {
  const item = mcData.itemsByName["jungle_log"];
  const current = bot.inventory.count(item.id, null);
  if (current >= 8) {
    return;
  }
  const needed = 8 - current;
  const nearbyLogs = bot.findBlocks({
    matching: block => block.name === "jungle_log",
    maxDistance: 32,
    count: needed
  });
  if (nearbyLogs.length > 0) {
    await mineBlock(bot, "jungle_log", needed);
  }
  if (bot.inventory.count(item.id, null) >= 8) {
    return;
  }
  const foundLog = await exploreUntil(bot, new Vec3(1, 0, 1), 60, () => {
    return bot.findBlock({
      matching: mcData.blocksByName["jungle_log"].id,
      maxDistance: 32
    });
  });
  if (!foundLog) {
    throw new Error("Could not find enough jungle_log.");
  }
  const remaining = 8 - bot.inventory.count(item.id, null);
  if (remaining > 0) {
    await mineBlock(bot, "jungle_log", remaining);
  }
  if (bot.inventory.count(item.id, null) < 8) {
    throw new Error("Failed to obtain 8 jungle_log.");
  }
}