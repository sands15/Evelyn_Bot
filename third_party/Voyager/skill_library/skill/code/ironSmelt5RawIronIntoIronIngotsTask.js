async function ironSmelt5RawIronIntoIronIngotsTask(bot) {
  if (!bot || !mcData) throw new Error("BOT_OR_MCDATA_MISSING");
  const countItem = name => {
    const it = mcData.itemsByName[name];
    if (!it) return 0;
    return bot.inventory.count(it.id, null);
  };
  const rawName = "raw_iron";
  const ingotName = "iron_ingot";
  const targetIngots = 5;
  const ingotsHave = countItem(ingotName);
  const rawHave = countItem(rawName);

  // Already satisfied by inventory.
  if (ingotsHave >= targetIngots) return {
    success: true
  };

  // Need to smelt at most what's required to reach 5 ingots.
  const neededIngotProduction = targetIngots - ingotsHave;
  if (neededIngotProduction <= 0) return {
    success: true
  };
  if (rawHave < neededIngotProduction) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Fuel candidates must be present in inventory in sufficient quantity.
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  let fuelName = null;
  for (const c of fuelCandidates) {
    if (countItem(c) >= neededIngotProduction) {
      fuelName = c;
      break;
    }
  }
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL_FOR_NEEDED_SMELTS");

  // Ensure furnace exists (context says it is available, but verify).
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    if (countItem("furnace") < 1) throw new Error("NO_FURNACE_AVAILABLE");
    await placeItem(bot, "furnace", bot.entity.position.offset(2, 0, 0));
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32
    });
    if (!furnaceBlock) throw new Error("FURNACE_NOT_AVAILABLE_AFTER_PLACEMENT");
  }
  const ingotsBefore = countItem(ingotName);
  const rawBefore = countItem(rawName);

  // Inventory-result contract:
  // smeltItem will smelt exactly `neededIngotProduction` items if inputs are available.
  await smeltItem(bot, rawName, fuelName, neededIngotProduction);
  const ingotsAfter = countItem(ingotName);
  const rawAfter = countItem(rawName);
  if (ingotsAfter < targetIngots) throw new Error("SMELTING_FAILED_TO_REACH_TARGET_INGOTS");
  const consumedRaw = rawBefore - rawAfter;
  if (consumedRaw < neededIngotProduction) throw new Error("SMELTING_DID_NOT_CONSUME_EXPECTED_RAW_IRON");

  // Success = inventory reaches at least 5 raw_iron into iron_ingots (interpreted as reaching >= 5 iron_ingots)
  // and raw iron has been consumed accordingly.
  if (ingotsAfter < ingotsBefore + neededIngotProduction) {
    // Guard against partial/failed smelt without throwing, still fail contract.
    throw new Error("SMELTING_OUTPUT_INSUFFICIENT");
  }
  return {
    success: true
  };
}