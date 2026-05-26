async function haveOneRawIronSmeltedIntoIronIngot(bot) {
  if (!bot) throw new Error("BOT_MISSING");
  if (typeof mcData === "undefined" || !mcData) throw new Error("MCDATA_MISSING");
  const countItem = name => {
    const item = mcData.itemsByName[name];
    if (!item) return 0;
    return bot.inventory.count(item.id, null);
  };
  const targetIngots = 1;
  if (countItem("iron_ingot") >= targetIngots) {
    return {
      success: true
    };
  }
  if (countItem("raw_iron") < 1) {
    throw new Error("NO_RAW_IRON_TO_SMELT");
  }
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    if (countItem("furnace") < 1) throw new Error("NO_FURNACE_AVAILABLE");
    const base = bot.entity.position.floored();
    const positions = [base.offset(1, 0, 0), base.offset(-1, 0, 0), base.offset(0, 0, 1), base.offset(0, 0, -1)];
    for (const pos of positions) {
      const existing = bot.blockAt(pos);
      const below = bot.blockAt(pos.offset(0, -1, 0));
      if (existing && existing.name === "air" && below && below.name !== "air") {
        try {
          await placeItem(bot, "furnace", pos);
          furnaceBlock = bot.findBlock({
            matching: mcData.blocksByName.furnace.id,
            maxDistance: 32
          });
          if (furnaceBlock) break;
        } catch (err) {
          // Try another nearby safe placement position.
        }
      }
    }
    if (!furnaceBlock) throw new Error("FURNACE_NOT_FOUND_AFTER_PLACEMENT");
  }
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log"];
  let fuelName = null;
  for (const candidate of fuelCandidates) {
    if (countItem(candidate) >= 1) {
      fuelName = candidate;
      break;
    }
  }
  if (!fuelName) throw new Error("NO_FUEL_AVAILABLE");
  await smeltItem(bot, "raw_iron", fuelName, 1);
  if (countItem("iron_ingot") < targetIngots) {
    throw new Error("SMELTING_FAILED_TO_PRODUCE_IRON_INGOT");
  }
  return {
    success: true
  };
}