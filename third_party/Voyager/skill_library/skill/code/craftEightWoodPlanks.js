async function craftEightWoodPlanks(bot) {
  const plankNames = ["oak_planks", "spruce_planks", "birch_planks", "jungle_planks", "acacia_planks", "dark_oak_planks", "mangrove_planks", "cherry_planks"];
  const logToPlank = {
    oak_log: "oak_planks",
    spruce_log: "spruce_planks",
    birch_log: "birch_planks",
    jungle_log: "jungle_planks",
    acacia_log: "acacia_planks",
    dark_oak_log: "dark_oak_planks",
    mangrove_log: "mangrove_planks",
    cherry_log: "cherry_planks"
  };
  function countItem(name) {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  }
  function totalPlanks() {
    return plankNames.reduce((sum, name) => sum + countItem(name), 0);
  }
  if (totalPlanks() >= 8) return;
  for (const logName of Object.keys(logToPlank)) {
    if (totalPlanks() >= 8) return;
    const logCount = countItem(logName);
    if (logCount <= 0) continue;
    const missingPlanks = 8 - totalPlanks();
    const craftsNeeded = Math.min(logCount, Math.ceil(missingPlanks / 4));
    const plankName = logToPlank[logName];
    const plankItem = mcData.itemsByName[plankName];
    const recipe = bot.recipesFor(plankItem.id, null, 1, null)[0];
    if (!recipe) {
      throw new Error(`No inventory recipe found for ${plankName}.`);
    }
    await bot.craft(recipe, craftsNeeded, null);
  }
  if (totalPlanks() < 8) {
    throw new Error("Not enough logs to craft 8 wood planks.");
  }
}