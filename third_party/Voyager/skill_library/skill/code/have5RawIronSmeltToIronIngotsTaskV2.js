async function have5RawIronSmeltToIronIngotsTask(bot) {
  if (!bot || !mcData) throw new Error("BOT_OR_MCDATA_MISSING");
  const countItem = (name) => {
    const it = mcData.itemsByName[name];
    if (!it) return 0;
    return bot.inventory.count(it.id, null);
  };

  const rawName = "raw_iron";
  const ingotName = "iron_ingot";
  const targetIngots = 5;

  const ingotsHave = countItem(ingotName);
  if (ingotsHave >= targetIngots) return { success: true };

  const rawHave = countItem(rawName);
  const neededRawTotal = targetIngots - ingotsHave;
  if (rawHave < neededRawTotal) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Ensure furnace exists
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32,
  });
  if (!furnaceBlock) {
    if (countItem("furnace") < 1) throw new Error("NO_FURNACE_AVAILABLE");
    await placeItem(bot, "furnace", bot.entity.position.offset(2, 0, 0));
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32,
    });
    if (!furnaceBlock) throw new Error("FURNACE_NOT_AVAILABLE_AFTER_PLACEMENT");
  }

  // Choose fuel we have at least neededRawTotal of
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  let fuelName = null;
  for (const c of fuelCandidates) {
    if (countItem(c) >= neededRawTotal) {
      fuelName = c;
      break;
    }
  }
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL_FOR_NEEDED_SMELTS");

  const ingotsBefore = countItem(ingotName);
  const rawBefore = countItem(rawName);

  await smeltItem(bot, rawName, fuelName, neededRawTotal);

  const ingotsAfter = countItem(ingotName);
  const rawAfter = countItem(rawName);

  if (ingotsAfter < targetIngots) throw new Error("SMELTING_FAILED_TO_REACH_5_INGOTS");

  const consumed = rawBefore - rawAfter;
  if (consumed < neededRawTotal) throw new Error("SMELTING_DID_NOT_CONSUME_EXPECTED_RAW_IRON");

  return {
    success: true,
    producedFromThisRun: ingotsAfter - ingotsBefore,
  };
}

async function have5RawIronSmeltToIronIngotsTask(bot) {
  if (!bot || !mcData) throw new Error("BOT_OR_MCDATA_MISSING");
  const countItem = (name) => {
    const it = mcData.itemsByName[name];
    if (!it) return 0;
    return bot.inventory.count(it.id, null);
  };

  const ingotName = "iron_ingot";
  const rawName = "raw_iron";
  const targetIngots = 5;

  // already complete?
  if (countItem(ingotName) >= targetIngots) return { success: true };

  return await have5RawIronSmeltToIronIngotsTask(bot);
}