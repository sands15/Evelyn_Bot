function countItem(bot, name) {
  const item = mcData.itemsByName[name];
  return item ? bot.inventory.count(item.id, null) : 0;
}

async function craftOneIronHelmet(bot) {
  if (countItem(bot, "iron_helmet") >= 1) return;
  if (countItem(bot, "iron_ingot") < 5) {
    throw new Error("Need 5 iron_ingot to craft an iron_helmet.");
  }
  const craftingTable = bot.findBlock({
    matching: mcData.blocksByName["crafting_table"].id,
    maxDistance: 32
  });
  if (!craftingTable) {
    throw new Error("Need a nearby crafting_table to craft an iron_helmet.");
  }
  await craftItem(bot, "iron_helmet", 1);
  if (countItem(bot, "iron_helmet") < 1) {
    throw new Error("Failed to craft iron_helmet.");
  }
}