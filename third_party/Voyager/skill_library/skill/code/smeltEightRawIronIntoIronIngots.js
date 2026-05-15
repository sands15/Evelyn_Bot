function countItem(bot, name) {
  const item = mcData.itemsByName[name];
  return item ? bot.inventory.count(item.id, null) : 0;
}

function findNearbyPlacePosition(bot) {
  const base = bot.entity.position.floored();
  for (let r = 1; r <= 3; r++) {
    for (let dx = -r; dx <= r; dx++) {
      for (let dz = -r; dz <= r; dz++) {
        if (Math.abs(dx) !== r && Math.abs(dz) !== r) continue;
        const pos = base.offset(dx, 0, dz);
        const block = bot.blockAt(pos);
        const below = bot.blockAt(pos.offset(0, -1, 0));
        if (block && below && block.name === "air" && below.name !== "air") {
          return pos;
        }
      }
    }
  }
  return null;
}

async function smeltEightRawIronIntoIronIngots(bot) {
  if (countItem(bot, "iron_ingot") >= 8) return;
  const needed = 8 - countItem(bot, "iron_ingot");
  if (countItem(bot, "raw_iron") < needed) {
    throw new Error("Need 8 raw_iron to smelt 8 iron_ingots.");
  }
  let fuelName = null;
  if (countItem(bot, "jungle_planks") >= needed) fuelName = "jungle_planks";else if (countItem(bot, "oak_planks") >= needed) fuelName = "oak_planks";else if (countItem(bot, "oak_log") >= needed) fuelName = "oak_log";else if (countItem(bot, "coal") >= needed) fuelName = "coal";
  if (!fuelName) {
    throw new Error("Need enough fuel to smelt 8 raw_iron.");
  }
  let furnace = bot.findBlock({
    matching: mcData.blocksByName["furnace"].id,
    maxDistance: 32
  });
  if (!furnace && countItem(bot, "furnace") < 1) {
    if (countItem(bot, "cobblestone") < 8) {
      throw new Error("Need 8 cobblestone to craft a furnace.");
    }
    const craftingTable = bot.findBlock({
      matching: mcData.blocksByName["crafting_table"].id,
      maxDistance: 32
    });
    if (!craftingTable) {
      throw new Error("Need a nearby crafting_table to craft a furnace.");
    }
    await craftItem(bot, "furnace", 1);
  }
  furnace = bot.findBlock({
    matching: mcData.blocksByName["furnace"].id,
    maxDistance: 32
  });
  if (!furnace) {
    const furnacePos = findNearbyPlacePosition(bot);
    if (!furnacePos) {
      throw new Error("Could not find a valid nearby position to place the furnace.");
    }
    await placeItem(bot, "furnace", furnacePos);
  }
  await smeltItem(bot, "raw_iron", fuelName, needed);
  if (countItem(bot, "iron_ingot") < 8) {
    throw new Error("Failed to smelt 8 iron_ingots.");
  }
}