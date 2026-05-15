function countItem(bot, name) {
  const item = mcData.itemsByName[name];
  return item ? bot.inventory.count(item.id, null) : 0;
}

async function smeltSixRawIronIntoIronIngots(bot) {
  const rawBefore = countItem(bot, "raw_iron");
  const ingotsBefore = countItem(bot, "iron_ingot");
  if (rawBefore < 6) {
    throw new Error("Need 6 raw_iron to smelt into iron_ingots.");
  }
  if (countItem(bot, "coal") < 6) {
    throw new Error("Need at least 6 coal to smelt 6 raw_iron.");
  }
  const furnace = bot.findBlock({
    matching: mcData.blocksByName["furnace"].id,
    maxDistance: 32
  });
  if (!furnace) {
    throw new Error("Need a nearby furnace to smelt raw_iron.");
  }
  await smeltItem(bot, "raw_iron", "coal", 6);
  const rawAfter = countItem(bot, "raw_iron");
  const ingotsAfter = countItem(bot, "iron_ingot");
  if (rawAfter > rawBefore - 6 || ingotsAfter < ingotsBefore + 6) {
    throw new Error("Failed to smelt 6 raw_iron into iron_ingots.");
  }
}