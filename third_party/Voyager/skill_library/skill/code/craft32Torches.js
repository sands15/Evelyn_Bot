async function craft32Torches(bot) {
  function countItem(name) {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  }
  if (countItem("torch") >= 32) return;
  const missingTorches = 32 - countItem("torch");
  const craftsNeeded = Math.ceil(missingTorches / 4);
  if (countItem("coal") + countItem("charcoal") < craftsNeeded) {
    await mineBlock(bot, "coal_ore", craftsNeeded - (countItem("coal") + countItem("charcoal")));
  }
  if (countItem("stick") < craftsNeeded) {
    throw new Error("Need more sticks to craft 32 torches.");
  }
  if (countItem("coal") >= craftsNeeded) {
    await craftItem(bot, "torch", craftsNeeded);
  } else if (countItem("charcoal") >= craftsNeeded) {
    await craftItem(bot, "torch", craftsNeeded);
  } else {
    throw new Error("Need more coal or charcoal to craft 32 torches.");
  }
  if (countItem("torch") < 32) {
    throw new Error("Failed to craft 32 torches.");
  }
}