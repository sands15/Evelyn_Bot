async function craftWoodenPickaxe(bot) {
  const pickaxeItem = mcData.itemsByName["wooden_pickaxe"];
  const planksItem = mcData.itemsByName["jungle_planks"];
  const sticksItem = mcData.itemsByName["stick"];
  if (bot.inventory.count(pickaxeItem.id, null) >= 1) return;
  if (bot.inventory.count(planksItem.id, null) < 3) {
    throw new Error("Need 3 jungle_planks to craft a wooden_pickaxe.");
  }
  if (bot.inventory.count(sticksItem.id, null) < 2) {
    throw new Error("Need 2 sticks to craft a wooden_pickaxe.");
  }
  const craftingTable = bot.findBlock({
    matching: mcData.blocksByName["crafting_table"].id,
    maxDistance: 32
  });
  if (!craftingTable) {
    throw new Error("Need a nearby crafting_table to craft a wooden_pickaxe.");
  }
  await craftItem(bot, "wooden_pickaxe", 1);
  if (bot.inventory.count(pickaxeItem.id, null) < 1) {
    throw new Error("Failed to craft wooden_pickaxe.");
  }
}