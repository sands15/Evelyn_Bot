function countItem(bot, name) {
  const item = mcData.itemsByName[name];
  return item ? bot.inventory.count(item.id, null) : 0;
}

async function smeltTwoRawIronIntoIronIngots(bot) {
  if (countItem(bot, "iron_ingot") >= 2) return;
  const needed = 2 - countItem(bot, "iron_ingot");
  if (countItem(bot, "raw_iron") < needed) {
    throw new Error("Need enough raw_iron to smelt 2 iron_ingots.");
  }
  if (countItem(bot, "coal") < needed) {
    throw new Error("Need coal to smelt the raw_iron.");
  }
  const furnace = bot.findBlock({
    matching: mcData.blocksByName["furnace"].id,
    maxDistance: 32
  });
  if (!furnace) {
    throw new Error("Need a nearby furnace to smelt raw_iron.");
  }
  await smeltItem(bot, "raw_iron", "coal", needed);
  if (countItem(bot, "iron_ingot") < 2) {
    throw new Error("Failed to smelt 2 iron_ingots.");
  }
}