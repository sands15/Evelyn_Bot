async function smelt3RawIronIntoIronIngotsTask(bot) {
  if (!bot || !mcData) throw new Error("BOT_OR_MCDATA_MISSING");
  const countItem = name => {
    const it = mcData.itemsByName[name];
    if (!it) return 0;
    return bot.inventory.count(it.id, null);
  };
  const targetIngots = 3;
  if (countItem("iron_ingot") >= targetIngots) return {
    success: true
  };
  const neededRaw = targetIngots - countItem("iron_ingot");
  if (neededRaw <= 0) return {
    success: true
  };
  if (countItem("raw_iron") < neededRaw) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Ensure we have a furnace to use; if not, place one locally.
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    if (countItem("furnace") < 1) throw new Error("NO_FURNACE_NEARBY");
    await placeItem(bot, "furnace", bot.entity.position.offset(2, 0, 0));
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32
    });
    if (!furnaceBlock) throw new Error("FURNACE_NOT_AVAILABLE_AFTER_PLACEMENT");
  }

  // Choose fuel for exactly neededRaw smelts.
  let fuelName = null;
  if (countItem("coal") >= neededRaw) fuelName = "coal";else if (countItem("oak_planks") >= neededRaw) fuelName = "oak_planks";else if (countItem("birch_planks") >= neededRaw) fuelName = "birch_planks";else if (countItem("oak_log") >= neededRaw) fuelName = "oak_log";else if (countItem("birch_log") >= neededRaw) fuelName = "birch_log";else if (countItem("cherry_log") >= neededRaw) fuelName = "cherry_log";else throw new Error("NOT_ENOUGH_FUEL");

  // Smelt exactly what we need.
  await smeltItem(bot, "raw_iron", fuelName, neededRaw);
  if (countItem("iron_ingot") >= targetIngots) return {
    success: true
  };
  throw new Error("SMELTING_FAILED_TO_PRODUCE_3_INGOTS");
}