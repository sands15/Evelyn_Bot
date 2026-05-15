async function craftEightJunglePlanks(bot) {
  const plankItem = mcData.itemsByName["jungle_planks"];
  const logItem = mcData.itemsByName["jungle_log"];
  if (bot.inventory.count(plankItem.id, null) >= 8) {
    return;
  }
  const missingPlanks = 8 - bot.inventory.count(plankItem.id, null);
  const craftsNeeded = Math.ceil(missingPlanks / 4);
  if (bot.inventory.count(logItem.id, null) < craftsNeeded) {
    await mineBlock(bot, "jungle_log", craftsNeeded - bot.inventory.count(logItem.id, null));
  }
  if (bot.inventory.count(plankItem.id, null) >= 8) {
    return;
  }
  if (bot.inventory.count(logItem.id, null) < craftsNeeded) {
    throw new Error("Not enough jungle_log to craft 8 jungle_planks.");
  }
  const recipe = bot.recipesFor(plankItem.id, null, 1, null)[0];
  if (!recipe) {
    throw new Error("No inventory recipe found for jungle_planks.");
  }
  await bot.craft(recipe, craftsNeeded, null);
  if (bot.inventory.count(plankItem.id, null) < 8) {
    throw new Error("Failed to craft 8 jungle_planks.");
  }
}