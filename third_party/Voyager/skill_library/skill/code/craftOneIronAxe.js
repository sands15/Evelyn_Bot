async function craftOneIronAxe(bot) {
  const ironAxe = mcData.itemsByName["iron_axe"];
  const ironIngot = mcData.itemsByName["iron_ingot"];
  const stick = mcData.itemsByName["stick"];
  if (bot.inventory.count(ironAxe.id, null) >= 1) return;
  if (bot.inventory.count(ironIngot.id, null) < 3) {
    throw new Error("Need 3 iron_ingot to craft an iron_axe.");
  }
  if (bot.inventory.count(stick.id, null) < 2) {
    throw new Error("Need 2 sticks to craft an iron_axe.");
  }
  const craftingTable = bot.findBlock({
    matching: mcData.blocksByName["crafting_table"].id,
    maxDistance: 32
  });
  if (!craftingTable) {
    throw new Error("Need a nearby crafting_table to craft an iron_axe.");
  }
  await craftItem(bot, "iron_axe", 1);
  if (bot.inventory.count(ironAxe.id, null) < 1) {
    throw new Error("Failed to craft iron_axe.");
  }
}