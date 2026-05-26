async function rawIronToIronIngots_atLeast5ContractTask(bot) {
  if (!bot || !mcData) throw new Error("BOT_OR_MCDATA_MISSING");
  const countItem = name => {
    const it = mcData.itemsByName?.[name];
    if (!it) return 0;
    return bot.inventory.count(it.id, null);
  };
  const rawName = "raw_iron";
  const ingotName = "iron_ingot";
  const ingotsHave = countItem(ingotName);
  const rawHave = countItem(rawName);

  // Already satisfied
  if (ingotsHave >= 5) return {
    success: true
  };

  // Contract: reach at least 5 ingots, consuming as much raw as needed.
  const neededIngotProduction = 5 - ingotsHave;
  if (rawHave < neededIngotProduction) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Ensure furnace exists (smelting helper expects it placed beforehand; verify here)
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

  // Pick fuel: must have at least neededIngotProduction units
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  let fuelName = null;
  for (const c of fuelCandidates) {
    if (countItem(c) >= neededIngotProduction) {
      fuelName = c;
      break;
    }
  }
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL_FOR_NEEDED_SMELTS");
  const rawBefore = countItem(rawName);
  const ingotsBefore = countItem(ingotName);

  // Inventory-result contract: smelt exactly neededRaw into ingots.
  await smeltItem(bot, rawName, fuelName, neededIngotProduction);
  const rawAfter = countItem(rawName);
  const ingotsAfter = countItem(ingotName);
  if (ingotsAfter < 5) throw new Error("SMELTING_FAILED_TO_REACH_TARGET_INGOTS");
  const consumedRaw = rawBefore - rawAfter;
  if (consumedRaw < neededIngotProduction) throw new Error("SMELTING_DID_NOT_CONSUME_EXPECTED_RAW_IRON");

  // If we smelt successfully, contract is satisfied.
  return {
    success: true
  };
}