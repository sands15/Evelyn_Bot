async function craftOneChest(bot) {
  const plankNames = ["oak_planks", "spruce_planks", "birch_planks", "jungle_planks", "acacia_planks", "dark_oak_planks", "mangrove_planks", "cherry_planks", "bamboo_planks", "crimson_planks", "warped_planks"];
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
  if (countItem("chest") >= 1) return;
  for (const logName of Object.keys(logToPlank)) {
    if (totalPlanks() >= 8) break;
    const logCount = countItem(logName);
    if (logCount <= 0) continue;
    const missingPlanks = 8 - totalPlanks();
    const craftsNeeded = Math.min(logCount, Math.ceil(missingPlanks / 4));
    await craftItem(bot, logToPlank[logName], craftsNeeded);
  }
  if (totalPlanks() < 8) {
    const result = await searchAndHarvest(bot, {
      goalType: "wood",
      quantity: 2,
      maxSearchBudgetSec: 24
    });
    if (!result.success) throw new Error(result.reason || "WOOD_SEARCH_FAILED");
    for (const logName of Object.keys(logToPlank)) {
      if (totalPlanks() >= 8) break;
      const logCount = countItem(logName);
      if (logCount <= 0) continue;
      const missingPlanks = 8 - totalPlanks();
      const craftsNeeded = Math.min(logCount, Math.ceil(missingPlanks / 4));
      await craftItem(bot, logToPlank[logName], craftsNeeded);
    }
  }
  if (totalPlanks() < 8) {
    throw new Error("Not enough wooden planks to craft a chest.");
  }
  await craftItem(bot, "chest", 1);
  if (countItem("chest") < 1) {
    throw new Error("Failed to craft 1 chest.");
  }
}