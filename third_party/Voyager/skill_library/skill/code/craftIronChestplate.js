async function craftIronChestplate(bot) {
  const chestplateItem = mcData.itemsByName["iron_chestplate"];
  const ingotItem = mcData.itemsByName["iron_ingot"];
  if (bot.inventory.count(chestplateItem.id, null) >= 1) return;
  if (bot.inventory.count(ingotItem.id, null) < 8) {
    throw new Error("Need 8 iron_ingot to craft an iron_chestplate.");
  }
  const craftingTable = bot.findBlock({
    matching: mcData.blocksByName["crafting_table"].id,
    maxDistance: 32
  });
  if (!craftingTable) {
    throw new Error("Need a nearby crafting_table to craft an iron_chestplate.");
  }
  await craftItem(bot, "iron_chestplate", 1);
  if (bot.inventory.count(chestplateItem.id, null) < 1) {
    throw new Error("Failed to craft iron_chestplate.");
  }
}