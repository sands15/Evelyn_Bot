function countItem(bot, name) {
  const item = mcData.itemsByName[name];
  return item ? bot.inventory.count(item.id, null) : 0;
}

async function smeltThirteenRawIronIntoIronIngots(bot) {
  if (countItem(bot, "iron_ingot") >= 13) return;
  const needed = 13 - countItem(bot, "iron_ingot");
  if (countItem(bot, "raw_iron") < needed) {
    throw new Error("Need enough raw_iron to smelt 13 iron_ingots.");
  }
  if (countItem(bot, "coal") < needed) {
    throw new Error("Need enough coal to smelt the raw_iron.");
  }
  const furnace = bot.findBlock({
    matching: mcData.blocksByName["furnace"].id,
    maxDistance: 32
  });
  if (!furnace) {
    throw new Error("Need a nearby furnace to smelt raw_iron.");
  }
  await smeltItem(bot, "raw_iron", "coal", needed);
  if (countItem(bot, "iron_ingot") < 13) {
    throw new Error("Failed to smelt 13 iron_ingots.");
  }
}