async function smeltFiveRawIronIntoIronIngots(bot) {
  if (!bot) throw new Error("BOT_MISSING");
  if (typeof mcData === "undefined" || !mcData) throw new Error("MCDATA_MISSING");
  const countItem = name => {
    const item = mcData.itemsByName[name];
    if (!item) return 0;
    return bot.inventory.count(item.id, null);
  };
  const rawName = "raw_iron";
  const ingotName = "iron_ingot";
  const targetToSmelt = 5;
  const rawBefore = countItem(rawName);
  const ingotsBefore = countItem(ingotName);
  if (rawBefore < targetToSmelt) {
    if (ingotsBefore >= targetToSmelt) return {
      success: true
    };
    throw new Error("NOT_ENOUGH_RAW_IRON");
  }
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
  let remaining = targetToSmelt;
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  for (const fuelName of fuelCandidates) {
    if (remaining <= 0) break;
    const fuelCount = countItem(fuelName);
    if (fuelCount <= 0) continue;
    const smeltCount = Math.min(remaining, fuelCount);
    await smeltItem(bot, rawName, fuelName, smeltCount);
    remaining -= smeltCount;
  }
  if (remaining > 0) throw new Error("NOT_ENOUGH_FUEL_FOR_NEEDED_SMELTS");
  const rawAfter = countItem(rawName);
  const ingotsAfter = countItem(ingotName);
  if (rawBefore - rawAfter < targetToSmelt) throw new Error("SMELTING_DID_NOT_CONSUME_EXPECTED_RAW_IRON");
  if (ingotsAfter - ingotsBefore < targetToSmelt) throw new Error("SMELTING_FAILED_TO_PRODUCE_EXPECTED_AMOUNT");
  return {
    success: true
  };
}