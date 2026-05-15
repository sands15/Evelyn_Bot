async function craftIronBoots(bot) {
  const boots = mcData.itemsByName["iron_boots"];
  const ingot = mcData.itemsByName["iron_ingot"];
  if (bot.inventory.count(boots.id, null) >= 1) return;
  if (bot.inventory.count(ingot.id, null) < 4) {
    throw new Error("Need 4 iron_ingot to craft iron_boots.");
  }
  await craftItem(bot, "iron_boots", 1);
  if (bot.inventory.count(boots.id, null) < 1) {
    throw new Error("Failed to craft iron_boots.");
  }
}