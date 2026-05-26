async function smelt5RawIronIntoIronIngotsTask(bot) {
  if (!bot || !mcData) throw new Error("BOT_OR_MCDATA_MISSING");
  const countItem = name => {
    const it = mcData.itemsByName[name];
    if (!it) return 0;
    return bot.inventory.count(it.id, null);
  };
  const targetIngots = 5;
  const rawName = "raw_iron";
  const ingotName = "iron_ingot";
  const ingotsHave = countItem(ingotName);
  if (ingotsHave >= targetIngots) return {
    success: true
  };
  const neededRaw = targetIngots - ingotsHave;
  const rawHave = countItem(rawName);
  if (rawHave < neededRaw) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Ensure furnace exists; place one briefly if missing.
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

  // Ensure we have fuel for the number of smelts.
  // smeltItem waits per item placed; we must provide at least `neededRaw` fuel count.
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  let fuelName = null;
  for (const c of fuelCandidates) {
    if (countItem(c) >= neededRaw) {
      fuelName = c;
      break;
    }
  }
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL_FOR_NEEDED_SMELTS");

  // Inventory-result contract verification.
  const ingotsBefore = countItem(ingotName);
  const rawBefore = countItem(rawName);
  await smeltItem(bot, rawName, fuelName, neededRaw);
  const ingotsAfter = countItem(ingotName);
  const rawAfter = countItem(rawName);
  if (ingotsAfter < targetIngots) throw new Error("SMELTING_FAILED_TO_REACH_TARGET_INGOTS");
  const produced = ingotsAfter - ingotsBefore;
  const consumed = rawBefore - rawAfter;
  if (produced < neededRaw) throw new Error("SMELTING_FAILED_TO_PRODUCE_EXPECTED_AMOUNT");
  if (consumed < neededRaw) throw new Error("SMELTING_DID_NOT_CONSUME_EXPECTED_RAW_IRON");
  return {
    success: true
  };
}