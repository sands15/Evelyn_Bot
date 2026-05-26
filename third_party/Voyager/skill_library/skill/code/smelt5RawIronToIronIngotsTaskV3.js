async function smelt5RawIronToIronIngotsTask(bot) {
  if (!bot) throw new Error("BOT_MISSING");
  if (!mcData) throw new Error("MCDATA_MISSING");
  const countItem = name => {
    const it = mcData.itemsByName[name];
    if (!it) return 0;
    return bot.inventory.count(it.id, null);
  };
  const rawName = "raw_iron";
  const ingotName = "iron_ingot";
  const targetIngots = 5;
  const ingotsHave = countItem(ingotName);
  if (ingotsHave >= targetIngots) return {
    success: true
  };
  const neededRaw = targetIngots - ingotsHave;
  const rawHave = countItem(rawName);
  if (rawHave < neededRaw) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Ensure furnace exists (place one if missing)
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

  // Choose fuel that we have at least `neededRaw` count of (smeltItem uses fuel per smelt iteration)
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
  await smeltItem(bot, rawName, fuelName, neededRaw);
  const ingotsAfter = countItem(ingotName);
  const rawAfter = countItem(rawName);
  if (ingotsAfter < targetIngots) throw new Error("SMELTING_FAILED_TO_REACH_TARGET_INGOTS");
  const produced = ingotsAfter - ingotsBefore;
  const consumedRaw = rawBefore - rawAfter;
  if (produced < neededRaw) throw new Error("SMELTING_FAILED_TO_PRODUCE_EXPECTED_AMOUNT");
  if (consumedRaw < neededRaw) throw new Error("SMELTING_DID_NOT_CONSUME_EXPECTED_RAW_IRON");
  return {
    success: true
  };
}