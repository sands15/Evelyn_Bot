async function craftOneIronPickaxe(bot) {
  const ironPickaxe = mcData.itemsByName["iron_pickaxe"];
  const ironIngot = mcData.itemsByName["iron_ingot"];
  const stick = mcData.itemsByName["stick"];
  if (!ironPickaxe || !ironIngot || !stick) {
    throw new Error("MISSING_ITEM: iron_pickaxe, iron_ingot, or stick");
  }
  if (bot.inventory.count(ironPickaxe.id, null) >= 1) return;
  if (bot.inventory.count(ironIngot.id, null) < 3) {
    throw new Error("SCARCITY: need 3 iron_ingot to craft iron_pickaxe");
  }
  if (bot.inventory.count(stick.id, null) < 2) {
    throw new Error("SCARCITY: need 2 stick to craft iron_pickaxe");
  }
  await craftItem(bot, "iron_pickaxe", 1);
  if (bot.inventory.count(ironPickaxe.id, null) < 1) {
    throw new Error("CRAFT_FAILED: iron_pickaxe not found in inventory after crafting");
  }
}