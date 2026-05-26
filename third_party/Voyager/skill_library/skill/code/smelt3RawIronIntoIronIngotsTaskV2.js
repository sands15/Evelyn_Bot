async function smelt3RawIronIntoIronIngotsTask(bot) {
  // Preconditions / completion check
  if (!bot || !mcData) throw new Error("BOT_OR_MCDATA_MISSING");
  const countItem = name => {
    const it = mcData.itemsByName[name];
    if (!it) return 0;
    return bot.inventory.count(it.id, null);
  };
  const target = 3;
  const currentIngot = countItem("iron_ingot");
  if (currentIngot >= target) return {
    success: true
  };
  const neededRaw = target - currentIngot;
  if (countItem("raw_iron") < neededRaw) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Ensure furnace exists (place only if we have furnace item)
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

  // Choose fuel: prefer coal; otherwise any wood fuel we have.
  let fuelName = null;
  if (countItem("coal") >= neededRaw) fuelName = "coal";else if (countItem("oak_planks") >= neededRaw) fuelName = "oak_planks";else if (countItem("birch_planks") >= neededRaw) fuelName = "birch_planks";else if (countItem("oak_log") >= neededRaw) fuelName = "oak_log";else if (countItem("birch_log") >= neededRaw) fuelName = "birch_log";else if (countItem("cherry_log") >= neededRaw) fuelName = "cherry_log";else throw new Error("NOT_ENOUGH_FUEL");

  // Smelt exactly neededRaw
  await smeltItem(bot, "raw_iron", fuelName, neededRaw);

  // Completion re-check
  if (countItem("iron_ingot") >= target) return {
    success: true
  };
  throw new Error("SMELTING_FAILED_TO_PRODUCE_3_INGOTS");
}