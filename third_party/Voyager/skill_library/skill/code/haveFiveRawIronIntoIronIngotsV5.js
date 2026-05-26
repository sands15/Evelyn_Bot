async function haveFiveRawIronIntoIronIngots(bot) {
  if (!bot) throw new Error("BOT_MISSING");
  if (typeof mcData === "undefined" || !mcData) throw new Error("MCDATA_MISSING");
  const countItem = name => {
    const item = mcData.itemsByName[name];
    if (!item) return 0;
    return bot.inventory.count(item.id, null);
  };
  const targetIngots = 5;
  const rawName = "raw_iron";
  const ingotName = "iron_ingot";
  if (countItem(ingotName) >= targetIngots) {
    return {
      success: true
    };
  }
  const neededSmelts = targetIngots - countItem(ingotName);
  if (countItem(rawName) < neededSmelts) {
    throw new Error("NOT_ENOUGH_RAW_IRON");
  }
  const fuelCandidates = ["coal", "charcoal", "oak_planks", "birch_planks", "spruce_planks", "jungle_planks", "acacia_planks", "dark_oak_planks", "mangrove_planks", "cherry_planks", "oak_log", "birch_log"];
  let fuelName = null;
  for (const name of fuelCandidates) {
    if (countItem(name) >= neededSmelts) {
      fuelName = name;
      break;
    }
  }
  if (!fuelName) {
    throw new Error("NOT_ENOUGH_FUEL_FOR_SMELTING");
  }
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    if (countItem("furnace") < 1) {
      throw new Error("NO_FURNACE_AVAILABLE");
    }
    await placeItem(bot, "furnace", bot.entity.position.offset(1, 0, 0));
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32
    });
    if (!furnaceBlock) {
      throw new Error("FURNACE_NOT_FOUND_AFTER_PLACEMENT");
    }
  }
  await smeltItem(bot, rawName, fuelName, neededSmelts);
  if (countItem(ingotName) < targetIngots) {
    throw new Error("SMELTING_FAILED_TO_REACH_TARGET");
  }
  return {
    success: true
  };
}