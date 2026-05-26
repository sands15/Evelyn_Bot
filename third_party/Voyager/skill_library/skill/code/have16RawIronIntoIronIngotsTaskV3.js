async function have16RawIronIntoIronIngotsTask(bot) {
  if (!bot) throw new Error("BOT_MISSING");
  if (typeof mcData === "undefined" || !mcData) throw new Error("MCDATA_MISSING");
  const countItem = name => {
    const item = mcData.itemsByName[name];
    if (!item) return 0;
    return bot.inventory.count(item.id, null);
  };
  const targetIngots = 16;
  const ingotName = "iron_ingot";
  const rawName = "raw_iron";
  if (countItem(ingotName) >= targetIngots) {
    return {
      success: true
    };
  }
  const neededSmelts = targetIngots - countItem(ingotName);
  if (countItem(rawName) < neededSmelts) {
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
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  let remaining = neededSmelts;
  for (const fuelName of fuelCandidates) {
    if (remaining <= 0) break;
    const availableFuel = countItem(fuelName);
    if (availableFuel <= 0) continue;
    const batch = Math.min(remaining, availableFuel);
    await smeltItem(bot, rawName, fuelName, batch);
    remaining -= batch;
    if (countItem(ingotName) >= targetIngots) {
      return {
        success: true
      };
    }
  }
  if (remaining > 0) throw new Error("NOT_ENOUGH_FUEL_FOR_16_INGOTS");
  if (countItem(ingotName) < targetIngots) {
    throw new Error("SMELTING_FAILED_TO_REACH_16_INGOTS");
  }
  return {
    success: true
  };
}