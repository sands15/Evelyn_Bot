async function craftOneStoneAxe(bot) {
  const stoneAxe = mcData.itemsByName["stone_axe"];
  const cobblestone = mcData.itemsByName["cobblestone"];
  const stick = mcData.itemsByName["stick"];
  if (bot.inventory.count(stoneAxe.id, null) >= 1) return;
  if (bot.inventory.count(cobblestone.id, null) < 3) {
    throw new Error("Need 3 cobblestone to craft a stone_axe.");
  }
  if (bot.inventory.count(stick.id, null) < 2) {
    throw new Error("Need 2 sticks to craft a stone_axe.");
  }
  const craftingTable = bot.findBlock({
    matching: mcData.blocksByName["crafting_table"].id,
    maxDistance: 32
  });
  if (!craftingTable) {
    throw new Error("Need a nearby crafting_table to craft a stone_axe.");
  }
  await craftItem(bot, "stone_axe", 1);
  if (bot.inventory.count(stoneAxe.id, null) < 1) {
    throw new Error("Failed to craft stone_axe.");
  }
}