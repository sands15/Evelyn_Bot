async function ironIngotsFromRawIronTask(bot) {
  if (!bot) throw new Error("BOT_MISSING");
  if (!mcData) throw new Error("MCDATA_MISSING");
  const countItem = name => {
    const it = mcData.itemsByName[name];
    if (!it) return 0;
    return bot.inventory.count(it.id, null);
  };
  const target = 5;
  const ingotName = "iron_ingot";
  const rawName = "raw_iron";

  // Already satisfied
  if (countItem(ingotName) >= target) return {
    success: true
  };

  // Inventory-result contract: need exactly enough raw to reach target ingots
  const ingotsHave = countItem(ingotName);
  const neededRaw = target - ingotsHave;
  if (neededRaw <= 0) return {
    success: true
  };
  if (countItem(rawName) < neededRaw) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Ensure furnace exists; furnace may be missing in some runs
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

  // Choose fuel: smeltItem smelts count times, consuming 1 fuel item per iteration.
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  let fuelName = null;
  for (const c of fuelCandidates) {
    if (countItem(c) >= neededRaw) {
      fuelName = c;
      break;
    }
  }
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL_FOR_SMELT");

  // Use provided smeltItem primitive (inventory-result contract: don't smelt extra)
  await smeltItem(bot, rawName, fuelName, neededRaw);
  if (countItem(ingotName) < target) throw new Error("SMELTING_FAILED_TO_REACH_TARGET_INGOTS");
  return {
    success: true
  };
}