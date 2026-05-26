function countItem(bot, name) {
  const item = mcData.itemsByName[name];
  return item ? bot.inventory.count(item.id, null) : 0;
}

async function craftFourTorches(bot) {
  if (countItem(bot, "torch") >= 4) return;
  const hasCoal = countItem(bot, "coal") >= 1;
  const hasCharcoal = countItem(bot, "charcoal") >= 1;
  if (!hasCoal && !hasCharcoal) {
    throw new Error("SCARCITY: need 1 coal or charcoal to craft 4 torches");
  }
  if (countItem(bot, "stick") < 1) {
    if (countItem(bot, "birch_planks") >= 2 || countItem(bot, "oak_planks") >= 2 || countItem(bot, "cherry_planks") >= 2) {
      await craftItem(bot, "stick", 1);
    } else {
      throw new Error("SCARCITY: need 1 stick or enough planks to craft sticks");
    }
  }
  if (countItem(bot, "torch") >= 4) return;
  if (countItem(bot, "stick") < 1) {
    throw new Error("SCARCITY: failed to obtain stick for torches");
  }
  await craftItem(bot, "torch", 1);
  if (countItem(bot, "torch") < 4) {
    throw new Error("CRAFT_FAILED: torch count is still below 4 after crafting");
  }
}