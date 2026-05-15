async function obtainEightJungleLogs(bot) {
  const required = 8;
  const item = mcData.itemsByName["jungle_log"];
  const current = bot.inventory.count(item.id, null);
  if (current >= required) {
    return;
  }
  const needed = required - current;
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
  const remaining = required - bot.inventory.count(item.id, null);
  if (remaining > 0) {
    await mineBlock(bot, "jungle_log", remaining);
  }
}