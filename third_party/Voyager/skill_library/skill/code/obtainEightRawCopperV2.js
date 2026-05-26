async function obtainEightRawCopper(bot) {
  const rawCopperItem = mcData.itemsByName["raw_copper"];
  if (!rawCopperItem) throw new Error("raw_copper item not found in mcData.");
  function rawCopperCount() {
    return bot.inventory.count(rawCopperItem.id, null);
  }
  if (rawCopperCount() >= 8) return;
  const pickaxe = bot.inventory.findInventoryItem(mcData.itemsByName["iron_pickaxe"].id) || bot.inventory.findInventoryItem(mcData.itemsByName["stone_pickaxe"].id) || bot.inventory.findInventoryItem(mcData.itemsByName["wooden_pickaxe"].id);
  if (!pickaxe) {
    throw new Error("Need a pickaxe to mine copper_ore.");
  }
  await bot.equip(pickaxe, "hand");
  let needed = 8 - rawCopperCount();
  let nearbyCopper = bot.findBlocks({
    matching: block => block.name === "copper_ore",
    maxDistance: 32,
    count: needed
  });
  if (nearbyCopper.length > 0) {
    await mineBlock(bot, "copper_ore", Math.min(nearbyCopper.length, needed));
    if (rawCopperCount() >= 8) return;
  }
  needed = 8 - rawCopperCount();
  const searchResult = await searchForOre(bot, {
    oreName: "copper_ore",
    quantity: needed,
    maxSearchBudgetSec: 20
  });
  if (!searchResult || searchResult.success === false) {
    throw new Error(searchResult?.reason || "LOCAL_SEARCH_EXHAUSTED: copper_ore was not nearby.");
  }
  needed = 8 - rawCopperCount();
  await bot.equip(pickaxe, "hand");
  await mineBlock(bot, "copper_ore", needed);
  if (rawCopperCount() < 8) {
    throw new Error("Failed to obtain 8 raw_copper.");
  }
}