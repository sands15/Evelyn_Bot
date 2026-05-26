async function craftOneChestFromNearbyWood(bot) {
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
  while (totalPlanks() < 8) {
    let craftedAny = false;
    for (const logName of Object.keys(logToPlank)) {
      if (totalPlanks() >= 8) break;
      const logCount = countItem(logName);
      if (logCount <= 0) continue;
      const craftsNeeded = Math.min(logCount, Math.ceil((8 - totalPlanks()) / 4));
      await craftItem(bot, logToPlank[logName], craftsNeeded);
      craftedAny = true;
    }
    if (totalPlanks() >= 8) break;
    if (!craftedAny) {
      const oakLog = bot.findBlock({
        matching: mcData.blocksByName.oak_log.id,
        maxDistance: 32
      });
      if (oakLog) {
        const logsNeeded = Math.ceil((8 - totalPlanks()) / 4);
        await mineBlock(bot, "oak_log", logsNeeded);
      } else {
        const harvest = await searchAndHarvest(bot, {
          goalType: "wood",
          quantity: Math.ceil((8 - totalPlanks()) / 4),
          maxSearchBudgetSec: 18
        });
        if (!harvest.success) throw new Error(harvest.reason || "WOOD_SEARCH_FAILED");
      }
    }
  }
  if (totalPlanks() < 8) {
    throw new Error("Not enough planks to craft 1 chest.");
  }
  if (countItem("chest") >= 1) return;
  await craftItem(bot, "chest", 1);
  if (countItem("chest") < 1) {
    throw new Error("Failed to craft 1 chest.");
  }
}