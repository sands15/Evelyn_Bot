async function have5RawIronIntoIronIngots(bot) {
  if (!bot) throw new Error("BOT_MISSING");
  if (typeof mcData === "undefined" || !mcData) throw new Error("MCDATA_MISSING");
  const countItem = name => {
    const item = mcData.itemsByName[name];
    if (!item) return 0;
    return bot.inventory.count(item.id, null);
  };
  const targetIngots = 5;
  const ingotsHave = countItem("iron_ingot");
  if (ingotsHave >= targetIngots) {
    return {
      success: true
    };
  }
  const neededSmelts = targetIngots - ingotsHave;
  if (countItem("raw_iron") < neededSmelts) {
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
  const fuelCandidates = ["coal", "charcoal", "oak_planks", "birch_planks", "spruce_planks", "oak_log", "birch_log", "spruce_log"];
  let fuelName = null;
  for (const name of fuelCandidates) {
    if (countItem(name) >= neededSmelts) {
      fuelName = name;
      break;
    }
  }
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL_FOR_SMELTING");
  await smeltItem(bot, "raw_iron", fuelName, neededSmelts);
  if (countItem("iron_ingot") < targetIngots) {
    throw new Error("SMELTING_FAILED_TO_REACH_TARGET");
  }
  return {
    success: true
  };
}