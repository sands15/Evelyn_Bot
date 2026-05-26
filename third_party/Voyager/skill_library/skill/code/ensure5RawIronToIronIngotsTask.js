async function ensure5RawIronToIronIngotsTask(bot) {
  if (!bot || !mcData) throw new Error("BOT_OR_MCDATA_MISSING");
  const countItem = name => {
    const it = mcData.itemsByName[name];
    if (!it) return 0;
    return bot.inventory.count(it.id, null);
  };
  const rawName = "raw_iron";
  const ingotName = "iron_ingot";
  const targetIngots = 5;

  // If already satisfied, finish immediately.
  if (countItem(ingotName) >= targetIngots) return {
    success: true
  };
  const ingotsHave = countItem(ingotName);
  const neededIngotProduction = targetIngots - ingotsHave; // how many ingots we must produce this function

  // Inventory-result contract: we must convert raw_iron -> iron_ingot up to needed amount.
  const rawHave = countItem(rawName);
  if (rawHave < neededIngotProduction) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Ensure furnace exists; if missing, place one locally.
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

  // Choose a fuel item we have in sufficient quantity.
  // smeltItem smelts once per iteration (fuel+input loop), so fuel count must cover neededIngotProduction.
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

  // Smelt exactly what we need (inventory-result contract).
  await smeltItem(bot, rawName, fuelName, neededIngotProduction);
  const ingotsAfter = countItem(ingotName);
  const rawAfter = countItem(rawName);

  // Validate result.
  if (ingotsAfter < targetIngots) throw new Error("SMELTING_FAILED_TO_REACH_TARGET_INGOTS");
  const produced = ingotsAfter - ingotsBefore;
  const consumedRaw = rawBefore - rawAfter;
  if (consumedRaw < neededIngotProduction) throw new Error("SMELTING_DID_NOT_CONSUME_EXPECTED_RAW_IRON");
  if (produced < neededIngotProduction) throw new Error("SMELTING_FAILED_TO_PRODUCE_EXPECTED_AMOUNT");
  return {
    success: true
  };
}