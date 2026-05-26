function countItem(bot, name) {
  const item = mcData.itemsByName[name];
  return item ? bot.inventory.count(item.id, null) : 0;
}

function findNearbyPlacePosition(bot) {
  const base = bot.entity.position.floored();
  const offsets = [new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1), new Vec3(1, 0, 1), new Vec3(-1, 0, -1), new Vec3(1, 0, -1), new Vec3(-1, 0, 1)];
  for (const offset of offsets) {
    const pos = base.plus(offset);
    const block = bot.blockAt(pos);
    const below = bot.blockAt(pos.offset(0, -1, 0));
    if (block && below && block.name === "air" && below.name !== "air") {
      return pos;
    }
  }
  return null;
}

async function smeltSixteenRawCopperIntoCopperIngots(bot) {
  if (countItem(bot, "copper_ingot") >= 16) return;
  const needed = 16 - countItem(bot, "copper_ingot");
  if (countItem(bot, "raw_copper") < needed) {
    throw new Error("Need enough raw_copper to smelt 16 copper_ingots.");
  }
  if (countItem(bot, "coal") < needed) {
    throw new Error("Need enough coal to smelt the raw_copper.");
  }
  let furnace = bot.findBlock({
    matching: mcData.blocksByName["furnace"].id,
    maxDistance: 32
  });
  if (!furnace) {
    if (countItem(bot, "furnace") < 1) {
      if (countItem(bot, "cobblestone") < 8) {
        throw new Error("Need a furnace or 8 cobblestone to craft one.");
      }
      await craftItem(bot, "furnace", 1);
    }
    const placePos = findNearbyPlacePosition(bot);
    if (!placePos) {
      throw new Error("Could not find a valid nearby position to place the furnace.");
    }
    await placeItem(bot, "furnace", placePos);
    furnace = bot.findBlock({
      matching: mcData.blocksByName["furnace"].id,
      maxDistance: 32
    });
    if (!furnace) {
      throw new Error("Failed to place furnace.");
    }
  }
  await smeltItem(bot, "raw_copper", "coal", needed);
  if (countItem(bot, "copper_ingot") < 16) {
    throw new Error("Failed to smelt 16 copper_ingots.");
  }
}