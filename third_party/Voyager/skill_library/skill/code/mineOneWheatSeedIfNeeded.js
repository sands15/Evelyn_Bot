async function mineOneWheatSeedIfNeeded(bot) {
  function countItem(name) {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  }
  if (countItem("wheat_seeds") >= 1) return;
  await mineOneWheatSeed(bot);
  if (countItem("wheat_seeds") < 1) {
    throw new Error("LOCAL_SEARCH_EXHAUSTED: failed to obtain 1 wheat seed from nearby seed-dropping plants.");
  }
}