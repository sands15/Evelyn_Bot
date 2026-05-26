function countItem(bot, name) {
  const item = mcData.itemsByName[name];
  return item ? bot.inventory.count(item.id, null) : 0;
}

async function smeltSevenRawIronIntoIronIngots(bot) {
  if (countItem(bot, "iron_ingot") >= 7) return;
  const needed = 7 - countItem(bot, "iron_ingot");
  if (countItem(bot, "raw_iron") < needed) {
    throw new Error("Need enough raw_iron to smelt 7 iron_ingots.");
  }
  let fuelName = null;
  if (countItem(bot, "coal") >= needed) {
    fuelName = "coal";
  } else if (countItem(bot, "oak_planks") >= needed) {
    fuelName = "oak_planks";
  } else if (countItem(bot, "birch_planks") >= needed) {
    fuelName = "birch_planks";
  } else if (countItem(bot, "oak_log") >= needed) {
    fuelName = "oak_log";
  } else if (countItem(bot, "birch_log") >= needed) {
    fuelName = "birch_log";
  }
  if (!fuelName) {
    throw new Error("Need enough fuel to smelt 7 raw_iron.");
  }
  const furnace = bot.findBlock({
    matching: mcData.blocksByName["furnace"].id,
    maxDistance: 32
  });
  if (!furnace) {
    throw new Error("Need a nearby furnace to smelt raw_iron.");
  }
  await smeltItem(bot, "raw_iron", fuelName, needed);
  if (countItem(bot, "iron_ingot") < 7) {
    throw new Error("Failed to smelt 7 raw_iron into iron_ingots.");
  }
}