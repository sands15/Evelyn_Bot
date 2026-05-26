async function smeltThreeRawIronIntoIronIngotsTask(bot) {
  if (!bot || !mcData) throw new Error("BOT_OR_MCDATA_MISSING");
  const itemByName = name => mcData.itemsByName[name];
  const invCount = itemName => {
    const item = itemByName(itemName);
    return item ? bot.inventory.count(item.id, null) : 0;
  };
  const haveIngot = invCount("iron_ingot");
  if (haveIngot >= 3) return {
    success: true
  };
  const needed = 3 - haveIngot;
  const haveRaw = invCount("raw_iron");
  if (haveRaw < needed) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Prefer coal; otherwise use planks/logs available
  let fuelName = null;
  if (invCount("coal") >= needed) fuelName = "coal";else if (invCount("oak_planks") >= needed) fuelName = "oak_planks";else if (invCount("birch_planks") >= needed) fuelName = "birch_planks";else if (invCount("oak_log") >= needed) fuelName = "oak_log";else if (invCount("birch_log") >= needed) fuelName = "birch_log";else throw new Error("NOT_ENOUGH_FUEL");

  // Ensure furnace exists nearby; if not, try to place one from inventory using helper
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    if (invCount("furnace") < 1) throw new Error("NO_FURNACE_BLOCK_OR_ITEM_AVAILABLE");
    const placePos = bot.entity.position.offset(2, 0, 0);
    await placeItem(bot, "furnace", placePos);
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32
    });
    if (!furnaceBlock) throw new Error("FURNACE_NOT_AVAILABLE_AFTER_PLACEMENT");
  }

  // Smelt using provided helper (it will open/close furnace and wait ticks)
  await smeltItem(bot, "raw_iron", fuelName, needed);
  if (invCount("iron_ingot") < 3) throw new Error("SMELTING_FAILED_TO_PRODUCE_3_INGOTS");
  return {
    success: true
  };
}