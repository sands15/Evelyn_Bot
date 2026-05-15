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
        if (block && block.name === "air" && below && below.name !== "air") {
          return pos;
        }
      }
    }
  }
  return null;
}

async function smeltSevenRawGold(bot) {
  if (countItem(bot, "gold_ingot") >= 7) return;
  if (countItem(bot, "raw_gold") < 7) {
    throw new Error("Need 7 raw_gold to smelt.");
  }
  if (countItem(bot, "coal") < 7) {
    throw new Error("Need at least 7 coal to smelt 7 raw_gold.");
  }
  let craftingTable = bot.findBlock({
    matching: mcData.blocksByName["crafting_table"].id,
    maxDistance: 32
  });
  if (!craftingTable && countItem(bot, "crafting_table") < 1) {
    if (countItem(bot, "jungle_planks") < 4) {
      if (countItem(bot, "jungle_log") < 1) {
        throw new Error("Need wood to craft a crafting_table.");
      }
      const plankRecipe = bot.recipesFor(mcData.itemsByName["jungle_planks"].id, null, 1, null)[0];
      await bot.craft(plankRecipe, 1, null);
    }
    const tableRecipe = bot.recipesFor(mcData.itemsByName["crafting_table"].id, null, 1, null)[0];
    await bot.craft(tableRecipe, 1, null);
  }
  if (!craftingTable) {
    const tablePos = findNearbyPlacePosition(bot);
    if (!tablePos) throw new Error("Could not find a place for the crafting_table.");
    await placeItem(bot, "crafting_table", tablePos);
    craftingTable = bot.findBlock({
      matching: mcData.blocksByName["crafting_table"].id,
      maxDistance: 32
    });
  }
  let furnace = bot.findBlock({
    matching: mcData.blocksByName["furnace"].id,
    maxDistance: 32
  });
  if (!furnace && countItem(bot, "furnace") < 1) {
    if (countItem(bot, "cobblestone") < 8) {
      throw new Error("Need 8 cobblestone to craft a furnace.");
    }
    await craftItem(bot, "furnace", 1);
  }
  if (!furnace) {
    const furnacePos = findNearbyPlacePosition(bot);
    if (!furnacePos) throw new Error("Could not find a place for the furnace.");
    await placeItem(bot, "furnace", furnacePos);
  }
  await smeltItem(bot, "raw_gold", "coal", 7);
  if (countItem(bot, "gold_ingot") < 7) {
    throw new Error("Failed to smelt 7 raw_gold.");
  }
}