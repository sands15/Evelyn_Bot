async function smeltThreeRawIronIntoIronIngots(bot) {
  // Ensure mcData exists
  if (typeof mcData === "undefined" || !mcData) throw new Error("MC_DATA_NOT_AVAILABLE");
  const getCount = name => {
    const item = mcData.itemsByName[name];
    if (!item) return 0;
    return bot.inventory.count(item.id, null);
  };

  // Goal check
  if (getCount("iron_ingot") >= 3) return {
    success: true
  };
  const neededIngots = 3 - getCount("iron_ingot");
  if (getCount("raw_iron") < neededIngots) {
    throw new Error("NOT_ENOUGH_RAW_IRON");
  }

  // Choose fuel (prefer coal)
  let fuelName = null;
  if (getCount("coal") >= neededIngots) fuelName = "coal";else if (getCount("oak_planks") >= neededIngots) fuelName = "oak_planks";else if (getCount("birch_planks") >= neededIngots) fuelName = "birch_planks";else if (getCount("oak_log") >= neededIngots) fuelName = "oak_log";else if (getCount("birch_log") >= neededIngots) fuelName = "birch_log";else throw new Error("NOT_ENOUGH_FUEL");

  // Ensure furnace exists nearby; place one if not
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName["furnace"].id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    if (getCount("furnace") < 1) throw new Error("NO_FURNACE_BLOCK_OR_ITEM_AVAILABLE");
    const placePos = bot.entity.position.offset(2, 0, 0);
    await placeItem(bot, "furnace", placePos);
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName["furnace"].id,
      maxDistance: 32
    });
    if (!furnaceBlock) throw new Error("FURNACE_NOT_AVAILABLE_AFTER_PLACEMENT");
  }

  // Smelt using provided primitive
  await smeltItem(bot, "raw_iron", fuelName, neededIngots);
  if (getCount("iron_ingot") < 3) {
    throw new Error("SMELTING_FAILED_TO_PRODUCE_3_INGOTS");
  }
  return {
    success: true
  };
}