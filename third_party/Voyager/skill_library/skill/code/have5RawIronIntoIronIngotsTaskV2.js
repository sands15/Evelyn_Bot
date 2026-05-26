async function have5RawIronIntoIronIngotsTask(bot) {
  if (!bot || !mcData) throw new Error("BOT_OR_MCDATA_MISSING");
  const countItem = name => {
    const it = mcData.itemsByName[name];
    if (!it) return 0;
    return bot.inventory.count(it.id, null);
  };
  const targetIngots = 5;
  const ingotName = "iron_ingot";
  const rawName = "raw_iron";

  // Already satisfied?
  if (countItem(ingotName) >= targetIngots) return {
    success: true
  };
  const neededRaw = targetIngots - countItem(ingotName);
  if (neededRaw <= 0) return {
    success: true
  };
  if (countItem(rawName) < neededRaw) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Ensure a furnace exists; place a local one only if missing.
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

  // Pick fuel for exactly neededRaw smelts.
  // NOTE: We assume smeltItem consumes one fuel unit per smelt call as implemented.
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  let fuelName = null;
  for (const c of fuelCandidates) {
    if (countItem(c) >= neededRaw) {
      fuelName = c;
      break;
    }
  }
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL_FOR_NEEDED_SMELTS");
  await smeltItem(bot, rawName, fuelName, neededRaw);
  if (countItem(ingotName) >= targetIngots) return {
    success: true
  };
  throw new Error("SMELTING_FAILED_TO_REACH_TARGET_INGOTS");
}