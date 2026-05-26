async function mineFourIronOre(bot) {
  function countItem(name) {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  }
  function ironResourceCount() {
    return countItem("raw_iron") + countItem("iron_ore");
  }
  if (ironResourceCount() >= 4) return;
  const pickaxe = bot.inventory.findInventoryItem(mcData.itemsByName["iron_pickaxe"].id) || bot.inventory.findInventoryItem(mcData.itemsByName["stone_pickaxe"].id);
  if (!pickaxe) {
    throw new Error("Need at least a stone pickaxe to mine iron_ore.");
  }
  await bot.equip(pickaxe, "hand");
  let remaining = 4 - ironResourceCount();
  let nearbyIron = bot.findBlocks({
    matching: block => block.name === "iron_ore",
    maxDistance: 32,
    count: remaining
  });
  if (nearbyIron.length > 0) {
    await mineBlock(bot, "iron_ore", Math.min(remaining, nearbyIron.length));
    if (ironResourceCount() >= 4) return;
  }
  remaining = 4 - ironResourceCount();
  const searchResult = await searchForOre(bot, {
    oreName: "iron_ore",
    quantity: remaining,
    maxSearchBudgetSec: 18
  });
  if (!searchResult || searchResult.success === false) {
    throw new Error(searchResult?.reason || "LOCAL_SEARCH_EXHAUSTED: iron_ore was not nearby.");
  }
  remaining = 4 - ironResourceCount();
  if (remaining <= 0) return;
  nearbyIron = bot.findBlocks({
    matching: block => block.name === "iron_ore",
    maxDistance: 32,
    count: remaining
  });
  if (nearbyIron.length === 0) {
    throw new Error("LOCAL_SEARCH_EXHAUSTED: search ended without nearby iron_ore.");
  }
  await mineBlock(bot, "iron_ore", Math.min(remaining, nearbyIron.length));
  if (ironResourceCount() < 4) {
    throw new Error("Failed to mine 4 iron_ore.");
  }
}