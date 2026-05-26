async function smelt5RawIronIntoIronIngotsTask(bot) {
  if (!bot || !mcData) throw new Error("BOT_OR_MCDATA_MISSING");
  const countItem = name => {
    const it = mcData.itemsByName[name];
    if (!it) return 0;
    return bot.inventory.count(it.id, null);
  };
  const targetIngots = 5;
  const ingotName = "iron_ingot";
  const rawName = "raw_iron";

  // If already complete (inventory-result contract)
  if (countItem(ingotName) >= targetIngots) return {
    success: true
  };
  const neededRaw = targetIngots - countItem(ingotName);
  if (countItem(rawName) < neededRaw) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Furnace check (smeltItem assumes furnace exists; place if missing)
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    if (countItem("furnace") < 1) throw new Error("NO_FURNACE_AVAILABLE");
    // Place locally (short prerequisite)
    await placeItem(bot, "furnace", bot.entity.position.offset(2, 0, 0));
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32
    });
    if (!furnaceBlock) throw new Error("FURNACE_NOT_AVAILABLE_AFTER_PLACEMENT");
  }

  // Choose fuel that we have in at least neededRaw quantity.
  // (smeltItem waits per smelt; provide a valid fuel type and sufficient quantity)
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  let fuelName = null;
  for (const c of fuelCandidates) {
    if (countItem(c) >= neededRaw) {
      fuelName = c;
      break;
    }
  }
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL_FOR_NEEDED_SMELTS");
  const ingotsBefore = countItem(ingotName);
  const rawBefore = countItem(rawName);

  // Smelt exactly neededRaw raws into ingots
  await smeltItem(bot, rawName, fuelName, neededRaw);
  const ingotsAfter = countItem(ingotName);
  const rawAfter = countItem(rawName);
  if (ingotsAfter < targetIngots) throw new Error("SMELTING_FAILED_TO_REACH_TARGET_INGOTS");

  // Inventory-result contract interpretation: ensure at least needed amount consumed.
  const produced = ingotsAfter - ingotsBefore;
  const consumed = rawBefore - rawAfter;
  if (produced < neededRaw) throw new Error("SMELTING_FAILED_TO_PRODUCE_EXPECTED_AMOUNT");
  if (consumed < neededRaw) throw new Error("SMELTING_DID_NOT_CONSUME_EXPECTED_RAW_IRON");
  return {
    success: true
  };
}