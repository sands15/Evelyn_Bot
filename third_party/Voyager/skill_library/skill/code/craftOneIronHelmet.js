async function craftOneIronHelmet(bot) {
  const helmet = mcData.itemsByName["iron_helmet"];
  const ingot = mcData.itemsByName["iron_ingot"];
  if (bot.inventory.count(helmet.id, null) >= 1) return;
  if (bot.inventory.count(ingot.id, null) < 5) {
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
  if (bot.inventory.count(helmet.id, null) < 1) {
    throw new Error("Failed to craft iron_helmet.");
  }
}