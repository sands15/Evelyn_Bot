async function have5RawIronSmeltedIntoIronIngotsTask(bot) {
  if (!bot || !mcData) throw new Error("BOT_OR_MCDATA_MISSING");
  const countItem = name => {
    const it = mcData.itemsByName[name];
    if (!it) return 0;
    return bot.inventory.count(it.id, null);
  };
  const targetIngots = 5;
  const ingotsHave = countItem("iron_ingot");
  if (ingotsHave >= targetIngots) return {
    success: true
  };
  const rawName = "raw_iron";
  const ingotsNeeded = targetIngots - ingotsHave;
  const rawHave = countItem(rawName);
  if (rawHave < ingotsNeeded) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Ensure furnace exists; place one if missing.
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
    if (!furnaceBlock) throw new Error("FURNACE_NOT_FOUND_AFTER_PLACEMENT");
  }

  // Choose fuel: must have at least `ingotsNeeded` items because smeltItem smelts exactly that count.
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  let fuelName = null;
  for (const c of fuelCandidates) {
    if (countItem(c) >= ingotsNeeded) {
      fuelName = c;
      break;
    }
  }
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL_FOR_NEEDED_SMELTS");
  const ingotsBefore = countItem("iron_ingot");
  const rawBefore = countItem("raw_iron");
  await smeltItem(bot, rawName, fuelName, ingotsNeeded);
  const ingotsAfter = countItem("iron_ingot");
  const rawAfter = countItem("raw_iron");
  if (ingotsAfter < targetIngots) throw new Error("SMELTING_FAILED_TO_REACH_TARGET_INGOTS");
  const consumed = rawBefore - rawAfter;
  const produced = ingotsAfter - ingotsBefore;
  if (consumed < ingotsNeeded) throw new Error("SMELTING_DID_NOT_CONSUME_EXPECTED_RAW_IRON");
  if (produced < ingotsNeeded) throw new Error("SMELTING_FAILED_TO_PRODUCE_EXPECTED_AMOUNT");
  return {
    success: true
  };
}