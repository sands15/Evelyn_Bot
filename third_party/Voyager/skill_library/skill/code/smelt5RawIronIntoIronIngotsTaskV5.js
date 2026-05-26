async function smelt5RawIronIntoIronIngotsTask(bot) {
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
  const rawHave = countItem("raw_iron");
  const neededRaw = targetIngots - ingotsHave;
  if (neededRaw <= 0) return {
    success: true
  };
  if (rawHave < neededRaw) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Ensure furnace exists (prereq step)
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

  // Choose any fuel we have in sufficient quantity for our exact smelt count
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  let fuelName = null;
  for (const c of fuelCandidates) {
    if (countItem(c) >= neededRaw) {
      fuelName = c;
      break;
    }
  }
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL_FOR_NEEDED_SMELTS");
  const ingotsBefore = countItem("iron_ingot");
  const rawBefore = countItem("raw_iron");

  // Smelt exactly neededRaw
  await smeltItem(bot, "raw_iron", fuelName, neededRaw);
  const ingotsAfter = countItem("iron_ingot");
  const rawAfter = countItem("raw_iron");
  const produced = ingotsAfter - ingotsBefore;
  const consumed = rawBefore - rawAfter;
  if (ingotsAfter < targetIngots) throw new Error("SMELTING_FAILED_TO_REACH_TARGET_INGOTS");
  if (consumed < neededRaw) throw new Error("SMELTING_DID_NOT_CONSUME_EXPECTED_RAW_IRON");
  if (produced < neededRaw) throw new Error("SMELTING_FAILED_TO_PRODUCE_EXPECTED_AMOUNT");
  return {
    success: true
  };
}