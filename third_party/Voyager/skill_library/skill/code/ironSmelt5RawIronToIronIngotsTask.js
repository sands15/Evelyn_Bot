async function ironSmelt5RawIronToIronIngotsTask(bot) {
  if (!bot || !mcData) throw new Error("BOT_OR_MCDATA_MISSING");
  const countItem = name => {
    const it = mcData.itemsByName[name];
    if (!it) return 0;
    return bot.inventory.count(it.id, null);
  };
  const rawName = "raw_iron";
  const ingotName = "iron_ingot";
  const targetIngots = 5;

  // If we already have enough ingots, we're done.
  if (countItem(ingotName) >= targetIngots) return {
    success: true
  };
  const ingotsHave = countItem(ingotName);
  const neededIngotProduction = targetIngots - ingotsHave;

  // Inventory-result contract: smelt exactly up to what we need, but never beyond available raw.
  const rawHave = countItem(rawName);
  if (rawHave < neededIngotProduction) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Ensure furnace exists; if not, place one locally using placeItem helper.
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

  // Choose fuel that we have at least neededIngotProduction quantity of.
  // smeltItem smelts once per (putFuel + putInput) iteration, so fuel count must match.
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  let fuelName = null;
  for (const c of fuelCandidates) {
    if (countItem(c) >= neededIngotProduction) {
      fuelName = c;
      break;
    }
  }
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL_FOR_SMELT");
  const ingotsBefore = countItem(ingotName);
  const rawBefore = countItem(rawName);

  // Smelt exactly what we need to reach 5 ingots.
  await smeltItem(bot, rawName, fuelName, neededIngotProduction);
  const ingotsAfter = countItem(ingotName);
  const rawAfter = countItem(rawName);

  // Contract checks: inventory should have at least 5 ingots now.
  if (ingotsAfter < targetIngots) throw new Error("SMELTING_FAILED_TO_REACH_5_INGOTS");

  // Also verify that we consumed at least the expected amount of raw_iron.
  const consumedRaw = rawBefore - rawAfter;
  if (consumedRaw < neededIngotProduction) throw new Error("SMELTING_DID_NOT_CONSUME_EXPECTED_RAW_IRON");
  return {
    success: true
  };
}