async function craftOneIronSword(bot) {
  const sword = mcData.itemsByName["iron_sword"];
  const ingot = mcData.itemsByName["iron_ingot"];
  const stick = mcData.itemsByName["stick"];
  if (bot.inventory.count(sword.id, null) >= 1) return;
  if (bot.inventory.count(ingot.id, null) < 2) {
    throw new Error("Need 2 iron_ingot to craft an iron_sword.");
  }
  if (bot.inventory.count(stick.id, null) < 1) {
    throw new Error("Need 1 stick to craft an iron_sword.");
  }
  const craftingTable = bot.findBlock({
    matching: mcData.blocksByName["crafting_table"].id,
    maxDistance: 32
  });
  if (!craftingTable) {
    throw new Error("Need a nearby crafting_table to craft an iron_sword.");
  }
  await craftItem(bot, "iron_sword", 1);
  if (bot.inventory.count(sword.id, null) < 1) {
    throw new Error("Failed to craft iron_sword.");
  }
}