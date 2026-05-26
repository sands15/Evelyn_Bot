async function smelt12RawIronIntoIronIngotsTask(bot) {
  if (!bot) throw new Error("BOT_MISSING");
  if (typeof mcData === "undefined" || !mcData) throw new Error("MCDATA_MISSING");
  const countItem = name => {
    const item = mcData.itemsByName[name];
    if (!item) return 0;
    return bot.inventory.count(item.id, null);
  };
  const targetSmelts = 12;
  const rawName = "raw_iron";
  const ingotName = "iron_ingot";
  if (countItem(rawName) < targetSmelts) {
    throw new Error("NOT_ENOUGH_RAW_IRON");
  }
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    if (countItem("furnace") < 1) throw new Error("NO_FURNACE_AVAILABLE");
    await placeItem(bot, "furnace", bot.entity.position.offset(1, 0, 0));
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32
    });
    if (!furnaceBlock) throw new Error("FURNACE_NOT_AVAILABLE_AFTER_PLACEMENT");
  }
  const ingotsBefore = countItem(ingotName);
  const rawBefore = countItem(rawName);
  let remaining = targetSmelts;
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  for (const fuelName of fuelCandidates) {
    if (remaining <= 0) break;
    const fuelAvailable = countItem(fuelName);
    if (fuelAvailable <= 0) continue;
    const batch = Math.min(remaining, fuelAvailable);
    await smeltItem(bot, rawName, fuelName, batch);
    remaining -= batch;
  }
  if (remaining > 0) throw new Error("NOT_ENOUGH_FUEL_FOR_12_SMELTS");
  const ingotsAfter = countItem(ingotName);
  const rawAfter = countItem(rawName);
  if (rawBefore - rawAfter < targetSmelts) throw new Error("RAW_IRON_NOT_CONSUMED_AS_EXPECTED");
  if (ingotsAfter - ingotsBefore < targetSmelts) throw new Error("SMELTING_FAILED_TO_PRODUCE_12_INGOTS");
  return {
    success: true
  };
}