async function obtainSixJungleLogs(bot) {
  const required = 6;
  const jungleLog = mcData.itemsByName["jungle_log"];
  function jungleLogCount() {
    return bot.inventory.count(jungleLog.id, null);
  }
  if (jungleLogCount() >= required) return;
  const axe = bot.inventory.findInventoryItem(mcData.itemsByName["stone_axe"].id) || bot.inventory.findInventoryItem(mcData.itemsByName["wooden_axe"]?.id) || bot.inventory.findInventoryItem(mcData.itemsByName["iron_axe"]?.id);
  if (axe) {
    await bot.equip(axe, "hand");
  }
  let needed = required - jungleLogCount();
  let nearbyLogs = bot.findBlocks({
    matching: block => block.name === "jungle_log",
    maxDistance: 32,
    count: needed
  });
  if (nearbyLogs.length > 0) {
    await mineBlock(bot, "jungle_log", Math.min(nearbyLogs.length, needed));
  }
  if (jungleLogCount() >= required) return;
  const foundLog = await exploreUntil(bot, new Vec3(1, 0, 1), 60, () => {
    return bot.findBlock({
      matching: mcData.blocksByName["jungle_log"].id,
      maxDistance: 32
    });
  });
  if (!foundLog) {
    throw new Error("Could not find jungle_log.");
  }
  if (axe) {
    await bot.equip(axe, "hand");
  }
  needed = required - jungleLogCount();
  nearbyLogs = bot.findBlocks({
    matching: block => block.name === "jungle_log",
    maxDistance: 32,
    count: needed
  });
  if (nearbyLogs.length > 0) {
    await mineBlock(bot, "jungle_log", Math.min(nearbyLogs.length, needed));
  }
  if (jungleLogCount() < required) {
    throw new Error("Failed to obtain 6 jungle_log.");
  }
}