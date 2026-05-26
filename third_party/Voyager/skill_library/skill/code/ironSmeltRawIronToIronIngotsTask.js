async function ironSmeltRawIronToIronIngotsTask(bot) {
  if (!bot) throw new Error("BOT_MISSING");
  if (!mcData) throw new Error("MCDATA_MISSING");
  if (typeof smeltItem !== "function") throw new Error("SMELT_HELPER_MISSING");
  const countItem = name => {
    const it = mcData.itemsByName[name];
    if (!it) return 0;
    return bot.inventory.count(it.id, null);
  };
  const rawName = "raw_iron";
  const ingotName = "iron_ingot";
  const targetIngots = 5;

  // Already satisfied?
  if (countItem(ingotName) >= targetIngots) return {
    success: true
  };

  // Inventory-result contract: smelt enough to reach at least 5 ingots.
  const ingotsHave = countItem(ingotName);
  const neededSmelts = targetIngots - ingotsHave;
  const rawHave = countItem(rawName);
  if (neededSmelts <= 0) return {
    success: true
  };
  if (rawHave < neededSmelts) throw new Error("NOT_ENOUGH_RAW_IRON_FOR_5_INGOTS");

  // Ensure furnace exists; if not, place one.
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    if (countItem("furnace") < 1) throw new Error("NO_FURNACE_AVAILABLE");
    if (typeof placeItem !== "function") throw new Error("PLACE_HELPER_MISSING");
    await placeItem(bot, "furnace", bot.entity.position.offset(2, 0, 0));
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32
    });
    if (!furnaceBlock) throw new Error("FURNACE_NOT_AVAILABLE_AFTER_PLACEMENT");
  }

  // Pick fuel that we have at least neededSmelts of.
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  let fuelName = null;
  for (const c of fuelCandidates) {
    if (countItem(c) >= neededSmelts) {
      fuelName = c;
      break;
    }
  }
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL_FOR_SMELTS");
  const ingotsBefore = countItem(ingotName);
  const rawBefore = countItem(rawName);
  await smeltItem(bot, rawName, fuelName, neededSmelts);
  const ingotsAfter = countItem(ingotName);
  const rawAfter = countItem(rawName);
  if (ingotsAfter < targetIngots) throw new Error("SMELTING_FAILED_TO_REACH_5_INGOTS");
  const consumedRaw = rawBefore - rawAfter;
  if (consumedRaw < neededSmelts) throw new Error("SMELTING_DID_NOT_CONSUME_EXPECTED_RAW_IRON");

  // If extra ingots somehow produced, still treat as success (inventory-result contract).
  if (ingotsAfter < ingotsBefore + neededSmelts) {
    // Allow success to be based on reaching target, but guard if clearly didn't smelt enough.
    throw new Error("SMELTING_INSUFFICIENT_PROGRESS");
  }
  return {
    success: true
  };
}