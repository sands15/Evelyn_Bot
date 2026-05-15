async function craftEightJunglePlanks(bot) {
  const junglePlanks = mcData.itemsByName["jungle_planks"];
  const jungleLog = mcData.itemsByName["jungle_log"];
  function plankCount() {
    return bot.inventory.count(junglePlanks.id, null);
  }
  function logCount() {
    return bot.inventory.count(jungleLog.id, null);
  }
  if (plankCount() >= 8) return;
  let missingPlanks = 8 - plankCount();
  let logsNeeded = Math.ceil(missingPlanks / 4);
  if (logCount() < logsNeeded) {
    let needLogs = logsNeeded - logCount();
    const nearbyLogs = bot.findBlocks({
      matching: block => block.name === "jungle_log",
      maxDistance: 32,
      count: needLogs
    });
    if (nearbyLogs.length > 0) {
      await mineBlock(bot, "jungle_log", needLogs);
    }
    if (logCount() < logsNeeded) {
      const foundLog = await exploreUntil(bot, new Vec3(1, 0, 1), 60, () => {
        return bot.findBlock({
          matching: mcData.blocksByName["jungle_log"].id,
          maxDistance: 32
        });
      });
      if (!foundLog) {
        throw new Error("Could not find enough jungle_log to craft 8 jungle_planks.");
      }
      needLogs = logsNeeded - logCount();
      if (needLogs > 0) {
        await mineBlock(bot, "jungle_log", needLogs);
      }
    }
  }
  if (plankCount() >= 8) return;
  if (logCount() < Math.ceil((8 - plankCount()) / 4)) {
    throw new Error("Not enough jungle_log to craft 8 jungle_planks.");
  }
  const craftingTable = bot.findBlock({
    matching: mcData.blocksByName["crafting_table"].id,
    maxDistance: 32
  });
  if (!craftingTable) {
    throw new Error("No nearby crafting_table available to craft jungle_planks.");
  }
  missingPlanks = 8 - plankCount();
  const craftsNeeded = Math.ceil(missingPlanks / 4);
  await craftItem(bot, "jungle_planks", craftsNeeded);
  if (plankCount() < 8) {
    throw new Error("Failed to craft 8 jungle_planks.");
  }
}