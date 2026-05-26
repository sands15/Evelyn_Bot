async function mineOneWheatSeed(bot) {
  function countItem(name) {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  }
  if (countItem("wheat_seeds") >= 1) return;
  const plantNames = ["short_grass", "tall_grass", "fern", "large_fern"];
  for (const plantName of plantNames) {
    if (countItem("wheat_seeds") >= 1) return;
    const positions = bot.findBlocks({
      matching: block => block && block.name === plantName,
      maxDistance: 32,
      count: 16
    });
    if (positions.length === 0) continue;
    await mineBlock(bot, plantName, Math.min(positions.length, 16));
    await bot.waitForTicks(10);
    if (countItem("wheat_seeds") >= 1) return;
  }
  throw new Error("LOCAL_SEARCH_EXHAUSTED: no wheat seed dropped from nearby seed-dropping plants.");
}