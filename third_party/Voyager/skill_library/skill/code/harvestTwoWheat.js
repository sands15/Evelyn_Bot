async function harvestTwoWheat(bot) {
  function countItem(name) {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  }
  if (countItem("wheat") >= 2) return;
  const wheatBlocks = bot.findBlocks({
    matching: block => block.name === "wheat" && block.getProperties && block.getProperties().age === 7,
    maxDistance: 32,
    count: 2
  });
  if (wheatBlocks.length === 0) {
    throw new Error("LOCAL_SEARCH_EXHAUSTED: No mature wheat was found nearby.");
  }
  const needed = 2 - countItem("wheat");
  const targets = [];
  for (let i = 0; i < Math.min(wheatBlocks.length, needed); i++) {
    targets.push(bot.blockAt(wheatBlocks[i]));
  }
  await bot.collectBlock.collect(targets, {
    ignoreNoPath: true
  });
  if (countItem("wheat") < 2) {
    throw new Error("Failed to harvest 2 wheat from nearby mature crops.");
  }
}