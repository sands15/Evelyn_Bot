async function ironSmeltRawIronIntoIronIngotsTask(bot) {
  if (!bot || !mcData) throw new Error("BOT_OR_MCDATA_MISSING");
  const targetIngots = 5;
  const countItem = name => {
    const it = mcData.itemsByName[name];
    if (!it) return 0;
    return bot.inventory.count(it.id, null);
  };
  const rawName = "raw_iron";
  const ingotName = "iron_ingot";
  if (countItem(ingotName) >= targetIngots) return {
    success: true
  };
  const rawNeeded = targetIngots - countItem(ingotName);
  if (rawNeeded <= 0) return {
    success: true
  };
  if (countItem(rawName) < rawNeeded) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Ensure furnace exists; place one if needed
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

  // Choose fuel we have at least rawNeeded of
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  let fuelName = null;
  for (const c of fuelCandidates) {
    if (countItem(c) >= rawNeeded) {
      fuelName = c;
      break;
    }
  }
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL_FOR_NEEDED_SMELTS");

  // Smelt exactly rawNeeded
  const beforeRaw = countItem(rawName);
  const beforeIngots = countItem(ingotName);
  await smeltItem(bot, rawName, fuelName, rawNeeded);
  const afterRaw = countItem(rawName);
  const afterIngots = countItem(ingotName);
  if (afterIngots < targetIngots) throw new Error("SMELTING_FAILED_TO_REACH_TARGET_INGOTS");
  if (beforeRaw - afterRaw < rawNeeded) throw new Error("SMELTING_DID_NOT_CONSUME_EXPECTED_RAW_IRON");
  return {
    success: true
  };
}