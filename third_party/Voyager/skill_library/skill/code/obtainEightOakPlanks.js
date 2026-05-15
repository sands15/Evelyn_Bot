async function obtainEightOakPlanks(bot) {
  const oakPlanks = mcData.itemsByName["oak_planks"];
  const oakLog = mcData.itemsByName["oak_log"];
  function plankCount() {
    return bot.inventory.count(oakPlanks.id, null);
  }
  function logCount() {
    return bot.inventory.count(oakLog.id, null);
  }
  if (plankCount() >= 8) return;
  const logsNeeded = Math.ceil((8 - plankCount()) / 4);
  if (logCount() < logsNeeded) {
    const nearbyLogs = bot.findBlocks({
      matching: block => block.name === "oak_log",
      maxDistance: 32,
      count: logsNeeded - logCount()
    });
    if (nearbyLogs.length > 0) {
      await mineBlock(bot, "oak_log", logsNeeded - logCount());
    }
  }
  if (plankCount() >= 8) return;
  if (logCount() < Math.ceil((8 - plankCount()) / 4)) {
    throw new Error("Not enough oak_log to craft 8 oak_planks.");
  }
  const recipe = bot.recipesFor(oakPlanks.id, null, 1, null)[0];
  if (!recipe) {
    throw new Error("No inventory recipe found for oak_planks.");
  }
  const craftsNeeded = Math.ceil((8 - plankCount()) / 4);
  if (craftsNeeded > 0) {
    await bot.craft(recipe, craftsNeeded, null);
  }
  if (plankCount() < 8) {
    throw new Error("Failed to obtain 8 oak_planks.");
  }
}