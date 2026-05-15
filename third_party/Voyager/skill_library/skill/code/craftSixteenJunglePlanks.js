async function craftSixteenJunglePlanks(bot) {
  const plankItem = mcData.itemsByName["jungle_planks"];
  const logItem = mcData.itemsByName["jungle_log"];
  const countPlanks = () => bot.inventory.count(plankItem.id, null);
  const countLogs = () => bot.inventory.count(logItem.id, null);
  if (countPlanks() >= 16) return;
  let craftsNeeded = Math.ceil((16 - countPlanks()) / 4);
  if (countLogs() < craftsNeeded) {
    await mineBlock(bot, "jungle_log", craftsNeeded - countLogs());
  }
  if (countPlanks() >= 16) return;
  craftsNeeded = Math.ceil((16 - countPlanks()) / 4);
  if (countLogs() < craftsNeeded) {
    throw new Error("Not enough jungle_log to craft 16 jungle_planks.");
  }
  const recipe = bot.recipesFor(plankItem.id, null, 1, null)[0];
  if (!recipe) {
    throw new Error("No inventory recipe found for jungle_planks.");
  }
  await bot.craft(recipe, craftsNeeded, null);
  if (countPlanks() < 16) {
    throw new Error("Failed to craft 16 jungle_planks.");
  }
}