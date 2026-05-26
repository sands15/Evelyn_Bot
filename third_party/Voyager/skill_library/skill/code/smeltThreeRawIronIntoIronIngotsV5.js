function countItem(bot, name) {
  const item = mcData.itemsByName[name];
  return item ? bot.inventory.count(item.id, null) : 0;
}

function findSupportedPlacePosition(bot) {
  const base = bot.entity.position.floored();
  const offsets = [new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1), new Vec3(2, 0, 0), new Vec3(-2, 0, 0), new Vec3(0, 0, 2), new Vec3(0, 0, -2)];
  for (const offset of offsets) {
    const pos = base.plus(offset);
    const target = bot.blockAt(pos);
    const below = bot.blockAt(pos.offset(0, -1, 0));
    if (target && target.name === "air" && below && below.name !== "air") {
      return pos;
    }
  }
  return null;
}

async function smeltThreeRawIronIntoIronIngots(bot) {
  const startIngots = countItem(bot, "iron_ingot");
  if (countItem(bot, "raw_iron") < 3) {
    throw new Error("NOT_ENOUGH_RAW_IRON");
  }
  let fuelName = null;
  if (countItem(bot, "coal") >= 3) fuelName = "coal";else if (countItem(bot, "oak_planks") >= 3) fuelName = "oak_planks";else if (countItem(bot, "birch_planks") >= 3) fuelName = "birch_planks";else if (countItem(bot, "oak_log") >= 3) fuelName = "oak_log";else if (countItem(bot, "birch_log") >= 3) fuelName = "birch_log";else if (countItem(bot, "cherry_log") >= 3) fuelName = "cherry_log";else throw new Error("NOT_ENOUGH_FUEL");
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    if (countItem(bot, "furnace") < 1) {
      throw new Error("NO_FURNACE_AVAILABLE");
    }
    const placePos = findSupportedPlacePosition(bot);
    if (!placePos) {
      throw new Error("NO_SUPPORTED_POSITION_FOR_FURNACE");
    }
    await placeItem(bot, "furnace", placePos);
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32
    });
    if (!furnaceBlock) {
      throw new Error("FURNACE_NOT_FOUND_AFTER_PLACEMENT");
    }
  }
  await smeltItem(bot, "raw_iron", fuelName, 3);
  if (countItem(bot, "iron_ingot") >= startIngots + 3) {
    return {
      success: true
    };
  }
  throw new Error("FAILED_TO_SMELT_THREE_RAW_IRON");
}