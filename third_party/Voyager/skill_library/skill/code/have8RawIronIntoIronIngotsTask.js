async function have8RawIronIntoIronIngotsTask(bot) {
  if (!bot || !mcData) throw new Error("BOT_OR_MCDATA_MISSING");
  const countItem = name => {
    const item = mcData.itemsByName[name];
    if (!item) return 0;
    return bot.inventory.count(item.id, null);
  };
  const targetIngots = 8;
  const rawName = "raw_iron";
  const ingotName = "iron_ingot";
  if (countItem(ingotName) >= targetIngots) {
    return {
      success: true
    };
  }
  let needed = targetIngots - countItem(ingotName);
  if (countItem(rawName) < needed) throw new Error("NOT_ENOUGH_RAW_IRON");
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
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log"];
  for (const fuelName of fuelCandidates) {
    if (countItem(ingotName) >= targetIngots) return {
      success: true
    };
    needed = targetIngots - countItem(ingotName);
    const fuelAvailable = countItem(fuelName);
    if (fuelAvailable <= 0) continue;
    const batch = Math.min(needed, fuelAvailable, countItem(rawName));
    if (batch > 0) {
      await smeltItem(bot, rawName, fuelName, batch);
    }
  }
  if (countItem(ingotName) >= targetIngots) {
    return {
      success: true
    };
  }
  throw new Error("SMELTING_FAILED_TO_REACH_8_IRON_INGOTS");
}