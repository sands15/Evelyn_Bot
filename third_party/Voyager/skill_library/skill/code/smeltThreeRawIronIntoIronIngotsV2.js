async function smeltThreeRawIronIntoIronIngots(bot) {
  if (!bot || !bot.inventory || !mcData) throw new Error("BOT_OR_MCDATA_MISSING");
  const getCount = name => {
    const item = mcData.itemsByName[name];
    if (!item) return 0;
    return bot.inventory.count(item.id, null);
  };
  const ironIngotCount = getCount("iron_ingot");
  if (ironIngotCount >= 3) return {
    success: true
  };
  const needed = 3 - ironIngotCount;
  if (getCount("raw_iron") < needed) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Prefer coal; otherwise use planks/logs available
  let fuelName = null;
  if (getCount("coal") >= needed) fuelName = "coal";else if (getCount("oak_planks") >= needed) fuelName = "oak_planks";else if (getCount("birch_planks") >= needed) fuelName = "birch_planks";else if (getCount("oak_log") >= needed) fuelName = "oak_log";else if (getCount("birch_log") >= needed) fuelName = "birch_log";else throw new Error("NOT_ENOUGH_FUEL");

  // Ensure furnace exists nearby; if not, place one using placeItem
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    if (getCount("furnace") < 1) throw new Error("NO_FURNACE_BLOCK_OR_ITEM_AVAILABLE");
    const placePos = bot.entity.position.offset(2, 0, 0);
    await placeItem(bot, "furnace", placePos);
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32
    });
    if (!furnaceBlock) throw new Error("FURNACE_NOT_AVAILABLE_AFTER_PLACEMENT");
  }

  // Smelt the needed amount
  await smeltItem(bot, "raw_iron", fuelName, needed);

  // Verify
  if (getCount("iron_ingot") < 3) throw new Error("SMELTING_FAILED_TO_PRODUCE_3_INGOTS");
  return {
    success: true
  };
}