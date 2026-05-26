async function craftFourTorches(bot) {
  function countItem(name) {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  }
  const startingTorches = countItem("torch");
  const targetTorches = startingTorches + 4;
  if (countItem("coal") < 1) {
    throw new Error("Need 1 coal to craft 4 torches.");
  }
  if (countItem("stick") < 1) {
    if (countItem("oak_planks") >= 2 || countItem("birch_planks") >= 2 || countItem("cherry_planks") >= 2) {
      await craftItem(bot, "stick", 1);
    } else if (countItem("oak_log") >= 1) {
      await craftItem(bot, "oak_planks", 1);
      await craftItem(bot, "stick", 1);
    } else if (countItem("birch_log") >= 1) {
      await craftItem(bot, "birch_planks", 1);
      await craftItem(bot, "stick", 1);
    } else if (countItem("cherry_log") >= 1) {
      await craftItem(bot, "cherry_planks", 1);
      await craftItem(bot, "stick", 1);
    } else {
      throw new Error("Need 1 stick or wood/planks to craft 4 torches.");
    }
  }
  if (countItem("torch") >= targetTorches) return;
  await craftItem(bot, "torch", 1);
  if (countItem("torch") < targetTorches) {
    throw new Error("Failed to craft 4 torches.");
  }
}