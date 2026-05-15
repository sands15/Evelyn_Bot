async function obtainEightJungleLogs(bot) {
  const required = 8;
  function itemCount(name) {
    const item = mcData.itemsByName[name];
    if (!item) return 0;
    return bot.inventory.count(item.id, null);
  }
  if (itemCount("jungle_log") >= required) return;
  let remaining = required - itemCount("jungle_log");
  const nearbyLogs = bot.findBlocks({
    matching: block => block.name === "jungle_log",
    maxDistance: 32,
    count: remaining
  });
  if (nearbyLogs.length === 0) {
    const foundLog = await exploreUntil(bot, new Vec3(1, 0, 1), 60, () => {
      return bot.findBlock({
        matching: mcData.blocksByName["jungle_log"].id,
        maxDistance: 32
      });
    });
    if (!foundLog && itemCount("jungle_log") < required) {
      throw new Error("Could not find jungle logs nearby.");
    }
  }
  remaining = required - itemCount("jungle_log");
  if (remaining > 0) {
    await mineBlock(bot, "jungle_log", remaining);
  }
  if (itemCount("jungle_log") < required) {
    throw new Error("Failed to obtain 8 jungle logs.");
  }
}