async function smeltThreeRawIronIntoIronIngotsTask(bot) {
  if (!bot || !mcData) throw new Error("BOT_OR_MCDATA_MISSING");
  const countItem = name => {
    const it = mcData.itemsByName[name];
    return it ? bot.inventory.count(it.id, null) : 0;
  };
  const targetIngotCount = 3;
  if (countItem("iron_ingot") >= targetIngotCount) return {
    success: true
  };
  const rawCount = countItem("raw_iron");
  const neededRaw = Math.max(0, Math.min(targetIngotCount - countItem("iron_ingot"), 3));
  if (rawCount < neededRaw) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Pick fuel preferring coal, else wood-like fuels if available
  let fuelName = null;
  if (countItem("coal") >= neededRaw) fuelName = "coal";else if (countItem("oak_planks") >= neededRaw) fuelName = "oak_planks";else if (countItem("birch_planks") >= neededRaw) fuelName = "birch_planks";else if (countItem("oak_log") >= neededRaw) fuelName = "oak_log";else if (countItem("birch_log") >= neededRaw) fuelName = "birch_log";else throw new Error("NOT_ENOUGH_FUEL");

  // Ensure furnace exists nearby; if not, attempt placement only if furnace item is available
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    if (countItem("furnace") < 1) throw new Error("NO_FURNACE_NEARBY");
    // place near self
    const pos = bot.entity.position.offset(2, 0, 0);
    await placeItem(bot, "furnace", pos);
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32
    });
    if (!furnaceBlock) throw new Error("FURNACE_NOT_AVAILABLE_AFTER_PLACEMENT");
  }
  await smeltItem(bot, "raw_iron", fuelName, neededRaw);
  if (countItem("iron_ingot") < targetIngotCount) {
    throw new Error("SMELTING_FAILED_TO_PRODUCE_3_INGOTS");
  }
  return {
    success: true
  };
}