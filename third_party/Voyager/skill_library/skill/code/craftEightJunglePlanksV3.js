async function craftEightJunglePlanks(bot) {
  const junglePlanks = mcData.itemsByName["jungle_planks"];
  const jungleLog = mcData.itemsByName["jungle_log"];
  if (bot.inventory.count(junglePlanks.id, null) >= 8) {
    return;
  }
  const missingPlanks = 8 - bot.inventory.count(junglePlanks.id, null);
  const logsNeeded = Math.ceil(missingPlanks / 4);
  if (bot.inventory.count(jungleLog.id, null) < logsNeeded) {
    throw new Error("Not enough jungle_log to craft 8 jungle_planks.");
  }
  const recipe = bot.recipesFor(junglePlanks.id, null, 1, null)[0];
  if (!recipe) {
    throw new Error("No inventory crafting recipe found for jungle_planks.");
  }
  await bot.craft(recipe, logsNeeded, null);
  if (bot.inventory.count(junglePlanks.id, null) < 8) {
    throw new Error("Failed to craft 8 jungle_planks.");
  }
}