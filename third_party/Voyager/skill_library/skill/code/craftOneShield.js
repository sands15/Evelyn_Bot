async function craftOneShield(bot) {
  function countItem(name) {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  }
  function totalPlanks() {
    const plankNames = ["oak_planks", "spruce_planks", "birch_planks", "jungle_planks", "acacia_planks", "dark_oak_planks", "mangrove_planks", "cherry_planks", "bamboo_planks", "crimson_planks", "warped_planks"];
    return plankNames.reduce((sum, name) => sum + countItem(name), 0);
  }
  if (countItem("shield") >= 1) return;
  if (countItem("iron_ingot") < 1) {
    throw new Error("Need 1 iron_ingot to craft a shield.");
  }
  if (totalPlanks() < 6 && countItem("oak_log") > 0) {
    const craftsNeeded = Math.ceil((6 - totalPlanks()) / 4);
    await craftItem(bot, "oak_planks", craftsNeeded);
  }
  if (totalPlanks() < 6) {
    throw new Error("Need 6 wooden planks to craft a shield.");
  }
  const craftingTable = bot.findBlock({
    matching: mcData.blocksByName["crafting_table"].id,
    maxDistance: 32
  });
  if (!craftingTable) {
    throw new Error("Need a nearby crafting_table to craft a shield.");
  }
  await craftItem(bot, "shield", 1);
  if (countItem("shield") < 1) {
    throw new Error("Failed to craft shield.");
  }
}