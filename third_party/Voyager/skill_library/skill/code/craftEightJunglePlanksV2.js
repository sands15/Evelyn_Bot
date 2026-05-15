async function craftEightJunglePlanks(bot) {
  const junglePlanks = mcData.itemsByName["jungle_planks"];
  const jungleLog = mcData.itemsByName["jungle_log"];
  const plankCount = () => bot.inventory.count(junglePlanks.id, null);
  const logCount = () => bot.inventory.count(jungleLog.id, null);
  if (plankCount() >= 8) return;
  const missingPlanks = 8 - plankCount();
  const logsNeeded = Math.ceil(missingPlanks / 4);
  if (logCount() < logsNeeded) {
    const needLogs = logsNeeded - logCount();
    const nearbyLogs = bot.findBlocks({
      matching: block => block.name === "jungle_log",
      maxDistance: 32,
      count: needLogs
    });
    if (nearbyLogs.length > 0) {
      await mineBlock(bot, "jungle_log", needLogs);
    }
  }
  if (plankCount() >= 8) return;
  if (logCount() < Math.ceil((8 - plankCount()) / 4)) {
    throw new Error("Not enough jungle_log to craft 8 jungle_planks.");
  }
  const recipe = bot.recipesFor(junglePlanks.id, null, 1, null)[0];
  if (!recipe) {
    throw new Error("No inventory recipe found for jungle_planks.");
  }
  const craftsNeeded = Math.ceil((8 - plankCount()) / 4);
  await bot.craft(recipe, craftsNeeded, null);
  if (plankCount() < 8) {
    throw new Error("Failed to craft 8 jungle_planks.");
  }
}