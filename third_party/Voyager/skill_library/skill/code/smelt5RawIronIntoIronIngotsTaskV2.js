async function smelt5RawIronIntoIronIngotsTask(bot) {
  // Preconditions / inputs
  if (!bot) throw new Error("BOT_MISSING");
  if (!mcData) throw new Error("MCDATA_MISSING");
  const getCount = itemName => {
    const item = mcData.itemsByName[itemName];
    if (!item) return 0;
    return bot.inventory.count(item.id, null);
  };

  // Inventory-first sufficiency check
  const targetIngots = 5;
  const ingots = getCount("iron_ingot");
  if (ingots >= targetIngots) return {
    success: true
  };
  const rawIronNeed = targetIngots - ingots;
  if (rawIronNeed <= 0) return {
    success: true
  };
  const rawIronHave = getCount("raw_iron");
  if (rawIronHave < rawIronNeed) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Ensure furnace exists; if not, place one
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    const furnaceItem = mcData.itemsByName.furnace;
    if (!furnaceItem) throw new Error("NO_FURNACE_ITEM_IN_MCDATA");
    if (getCount("furnace") < 1) throw new Error("NO_FURNACE_NEARBY_OR_AVAILABLE");

    // placeItem helper is assumed available from the provided toolkit
    await placeItem(bot, "furnace", bot.entity.position.offset(2, 0, 0));
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32
    });
    if (!furnaceBlock) throw new Error("FURNACE_NOT_AVAILABLE_AFTER_PLACEMENT");
  }

  // Pick fuel for exactly neededRaw smelts
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  let fuelName = null;
  for (const c of fuelCandidates) {
    if (getCount(c) >= rawIronNeed) {
      fuelName = c;
      break;
    }
  }
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL");
  const ingotsBefore = ingots;
  const rawBefore = rawIronHave;

  // smeltItem helper is assumed available from the provided toolkit
  // It consumes input count items and waits for smelt ticks per item.
  await smeltItem(bot, "raw_iron", fuelName, rawIronNeed);
  const ingotsAfter = getCount("iron_ingot");
  const rawAfter = getCount("raw_iron");

  // Inventory-result contract check
  if (ingotsAfter - ingotsBefore < rawIronNeed) {
    throw new Error("SMELTING_FAILED_TO_CONVERT_EXPECTED_AMOUNT");
  }
  if (rawBefore - rawAfter < rawIronNeed) {
    throw new Error("SMELTING_DID_NOT_CONSUME_EXPECTED_RAW_IRON");
  }
  if (ingotsAfter >= targetIngots) return {
    success: true
  };
  throw new Error("SMELTING_FINISHED_BUT_CONTRACT_NOT_MET");
}