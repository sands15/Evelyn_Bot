async function mineTwoIronOre(bot) {
  function countItem(name) {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  }
  function minedIronProgress(startRawIron, startIronOreItem) {
    return countItem("raw_iron") - startRawIron + countItem("iron_ore") - startIronOreItem;
  }
  const startRawIron = countItem("raw_iron");
  const startIronOreItem = countItem("iron_ore");
  const pickaxe = bot.inventory.findInventoryItem(mcData.itemsByName["iron_pickaxe"]?.id) || bot.inventory.findInventoryItem(mcData.itemsByName["stone_pickaxe"]?.id) || bot.inventory.findInventoryItem(mcData.itemsByName["copper_pickaxe"]?.id);
  if (!pickaxe) {
    throw new Error("Need a stone, copper, or iron pickaxe to mine iron_ore.");
  }
  await bot.equip(pickaxe, "hand");
  let remaining = 2 - minedIronProgress(startRawIron, startIronOreItem);
  if (remaining <= 0) return;
  const nearbyIron = bot.findBlocks({
    matching: block => block.name === "iron_ore",
    maxDistance: 32,
    count: remaining
  });
  if (nearbyIron.length < remaining) {
    const searchResult = await searchForOre(bot, {
      oreName: "iron_ore",
      quantity: remaining,
      maxSearchBudgetSec: 12
    });
    if (!searchResult || searchResult.success === false) {
      throw new Error(searchResult?.reason || "LOCAL_SEARCH_EXHAUSTED: iron_ore was not nearby.");
    }
  }
  remaining = 2 - minedIronProgress(startRawIron, startIronOreItem);
  if (remaining <= 0) return;
  await mineBlock(bot, "iron_ore", remaining);
  if (minedIronProgress(startRawIron, startIronOreItem) < 2) {
    throw new Error("FAILED_TO_MINE_2_IRON_ORE: not enough reachable iron_ore was collected.");
  }
}