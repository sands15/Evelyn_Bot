async function craftFourSticksForReserve(bot) {
  function countItem(name) {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  }
  if (countItem("stick") >= 4) return;
  const plankNames = ["oak_planks", "spruce_planks", "birch_planks", "jungle_planks", "acacia_planks", "dark_oak_planks", "mangrove_planks", "cherry_planks", "bamboo_planks", "crimson_planks", "warped_planks"];
  const logToPlank = {
    oak_log: "oak_planks",
    spruce_log: "spruce_planks",
    birch_log: "birch_planks",
    jungle_log: "jungle_planks",
    acacia_log: "acacia_planks",
    dark_oak_log: "dark_oak_planks",
    mangrove_log: "mangrove_planks",
    cherry_log: "cherry_planks",
    crimson_stem: "crimson_planks",
    warped_stem: "warped_planks"
  };
  function totalPlanks() {
    return plankNames.reduce((sum, name) => sum + countItem(name), 0);
  }
  if (totalPlanks() < 2) {
    let madePlanks = false;
    for (const [logName, plankName] of Object.entries(logToPlank)) {
      if (countItem(logName) > 0) {
        await craftItem(bot, plankName, 1);
        madePlanks = true;
        break;
      }
    }
    if (!madePlanks || totalPlanks() < 2) {
      throw new Error("Need at least 2 wooden planks or 1 log to craft 4 sticks.");
    }
  }
  await craftItem(bot, "stick", 1);
  if (countItem("stick") < 4) {
    throw new Error("Failed to craft 4 sticks.");
  }
}