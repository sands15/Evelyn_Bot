async function craftOneIronPickaxe(bot) {
  const ironPickaxe = mcData.itemsByName["iron_pickaxe"];
  if (bot.inventory.count(ironPickaxe.id, null) >= 1) return;
  const ironIngot = mcData.itemsByName["iron_ingot"];
  const stick = mcData.itemsByName["stick"];
  if (bot.inventory.count(ironIngot.id, null) < 3) {
    throw new Error("Need 3 iron_ingot to craft an iron_pickaxe.");
  }
  if (bot.inventory.count(stick.id, null) < 2) {
    throw new Error("Need 2 sticks to craft an iron_pickaxe.");
  }
  await craftItem(bot, "iron_pickaxe", 1);
  if (bot.inventory.count(ironPickaxe.id, null) < 1) {
    throw new Error("Failed to craft iron_pickaxe.");
  }
}