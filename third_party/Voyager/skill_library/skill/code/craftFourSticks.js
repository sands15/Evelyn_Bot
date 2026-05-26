async function craftFourSticks(bot) {
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
    let craftedPlanks = false;
    for (const logName of Object.keys(logToPlank)) {
      if (countItem(logName) > 0) {
        await craftItem(bot, logToPlank[logName], 1);
        craftedPlanks = true;
        break;
      }
    }
    if (!craftedPlanks || totalPlanks() < 2) {
      throw new Error("Need at least one log to craft planks for sticks.");
    }
  }
  await craftItem(bot, "stick", 1);
  if (countItem("stick") < 4) {
    throw new Error("Failed to craft 4 sticks.");
  }
}