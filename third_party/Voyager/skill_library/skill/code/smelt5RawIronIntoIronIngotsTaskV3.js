async function smelt5RawIronIntoIronIngotsTask(bot) {
  if (!bot) throw new Error("BOT_MISSING");
  if (!mcData) throw new Error("MCDATA_MISSING");
  const getCount = itemName => {
    const item = mcData.itemsByName[itemName];
    if (!item) return 0;
    return bot.inventory.count(item.id, null);
  };
  const targetIngots = 5;

  // If already satisfied, stop immediately.
  const ingotsHave = getCount("iron_ingot");
  if (ingotsHave >= targetIngots) return {
    success: true
  };
  const rawIronNeed = targetIngots - ingotsHave;
  const rawHave = getCount("raw_iron");
  if (rawHave < rawIronNeed) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Ensure a furnace exists; if not, place one nearby (prereq 1 step).
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    const furnaceItem = mcData.itemsByName.furnace;
    if (!furnaceItem) throw new Error("NO_FURNACE_ITEM_IN_MCDATA");
    if (getCount("furnace") < 1) throw new Error("NO_FURNACE_AVAILABLE");
    // placeItem helper is assumed available from the provided toolkit
    await placeItem(bot, "furnace", bot.entity.position.offset(2, 0, 0));
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32
    });
    if (!furnaceBlock) throw new Error("FURNACE_NOT_AVAILABLE_AFTER_PLACEMENT");
  }

  // Choose fuel that can cover exactly the needed amount of smelts (pragmatic).
  // Use any available candidate; consume exactly rawIronNeed smelts.
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  let fuelName = null;
  for (const c of fuelCandidates) {
    if (getCount(c) >= rawIronNeed) {
      fuelName = c;
      break;
    }
  }
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL_FOR_NEEDED_SMELTS");
  const ingotsBefore = ingotsHave;
  const rawBefore = rawHave;

  // smeltItem helper is assumed available from the provided toolkit
  await smeltItem(bot, "raw_iron", fuelName, rawIronNeed);

  // Inventory-result contract check
  const ingotsAfter = getCount("iron_ingot");
  const rawAfter = getCount("raw_iron");
  const produced = ingotsAfter - ingotsBefore;
  const consumed = rawBefore - rawAfter;
  if (produced < rawIronNeed) throw new Error("SMELTING_FAILED_TO_CONVERT_EXPECTED_AMOUNT");
  if (consumed < rawIronNeed) throw new Error("SMELTING_DID_NOT_CONSUME_EXPECTED_RAW_IRON");
  if (ingotsAfter >= targetIngots) return {
    success: true
  };
  throw new Error("SMELTING_FINISHED_BUT_CONTRACT_NOT_MET");
}