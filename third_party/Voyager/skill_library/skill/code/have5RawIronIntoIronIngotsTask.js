async function smelt5RawIronIntoIronIngotsTask(bot) {
  if (!bot || !mcData) throw new Error("BOT_OR_MCDATA_MISSING");
  const countItem = name => {
    const it = mcData.itemsByName[name];
    if (!it) return 0;
    return bot.inventory.count(it.id, null);
  };
  const target = 5;
  const rawName = "raw_iron";
  const ingotName = "iron_ingot";

  // Already complete?
  if (countItem(ingotName) >= target) return {
    success: true
  };

  // Need raw iron?
  const neededRaw = target - countItem(ingotName);
  if (neededRaw <= 0) return {
    success: true
  };
  if (countItem(rawName) < neededRaw) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Ensure furnace exists or place one (short prerequisite)
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

  // Pick fuel we have in sufficient quantity
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  let fuelName = null;
  for (const c of fuelCandidates) {
    if (countItem(c) >= neededRaw) {
      fuelName = c;
      break;
    }
  }
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL_FOR_NEEDED_SMELTS");

  // Smelt (inventory-result contract)
  await smeltItem(bot, rawName, fuelName, neededRaw);

  // Verify contract
  if (countItem(ingotName) >= target) return {
    success: true
  };
  throw new Error("SMELTING_FAILED_TO_REACH_TARGET_INGOTS");
}

async function have5RawIronIntoIronIngotsTask(bot) {
  return smelt5RawIronIntoIronIngotsTask(bot);
}