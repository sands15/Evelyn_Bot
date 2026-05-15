async function harvestOneWheat(bot) {
  function countItem(name) {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  }
  if (countItem("wheat") >= 1) return;
  const wheatBlocks = bot.findBlocks({
    matching: block => {
      return block.name === "wheat" && block.getProperties && block.getProperties().age === 7;
    },
    maxDistance: 32,
    count: 8
  });
  if (wheatBlocks.length === 0) {
    throw new Error("LOCAL_SEARCH_EXHAUSTED: No mature wheat was found nearby.");
  }
  const targets = [bot.blockAt(wheatBlocks[0])];
  await bot.collectBlock.collect(targets, {
    ignoreNoPath: true
  });
  if (countItem("wheat") < 1) {
    throw new Error("Failed to harvest wheat.");
  }
}