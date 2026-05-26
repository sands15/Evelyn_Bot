async function smelt5RawIronToIronIngotsTask(bot) {
  if (!bot) throw new Error("BOT_MISSING");
  if (!mcData) throw new Error("MCDATA_MISSING");
  const countItem = name => {
    const it = mcData.itemsByName[name];
    if (!it) return 0;
    return bot.inventory.count(it.id, null);
  };
  const targetIngots = 5;
  const rawName = "raw_iron";
  const ingotName = "iron_ingot";
  const ingotsHave = countItem(ingotName);
  if (ingotsHave >= targetIngots) return {
    success: true
  };
  const neededIngotProduction = targetIngots - ingotsHave;
  const rawHave = countItem(rawName);
  if (rawHave < neededIngotProduction) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Ensure furnace exists (furnace should already exist, but verify per contract)
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    // Place one if missing
    if (countItem("furnace") < 1) throw new Error("NO_FURNACE_AVAILABLE");
    await placeItem(bot, "furnace", bot.entity.position.offset(2, 0, 0));
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32
    });
    if (!furnaceBlock) throw new Error("FURNACE_NOT_AVAILABLE_AFTER_PLACEMENT");
  }

  // Choose fuel: smeltItem consumes one fuel item per smelt iteration in helper
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  let fuelName = null;
  for (const c of fuelCandidates) {
    if (countItem(c) >= neededIngotProduction) {
      fuelName = c;
      break;
    }
  }
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL_FOR_NEEDED_SMELTS");
  const ingotsBefore = countItem(ingotName);
  const rawBefore = countItem(rawName);

  // Inventory-result contract: smelt exactly the needed amount.
  await smeltItem(bot, rawName, fuelName, neededIngotProduction);
  const ingotsAfter = countItem(ingotName);
  const rawAfter = countItem(rawName);
  if (ingotsAfter < targetIngots) throw new Error("SMELTING_FAILED_TO_REACH_5_INGOTS");
  const consumedRaw = rawBefore - rawAfter;
  if (consumedRaw < neededIngotProduction) throw new Error("SMELTING_DID_NOT_CONSUME_EXPECTED_RAW_IRON");

  // If more than enough ingots somehow happened, still treat success as contract satisfied.
  return {
    success: true
  };
}