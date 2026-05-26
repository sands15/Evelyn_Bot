async function have5RawIronToIronIngotsTask(bot) {
  if (!bot || !mcData) throw new Error("BOT_OR_MCDATA_MISSING");
  const rawName = "raw_iron";
  const ingotName = "iron_ingot";
  const targetIngots = 5;
  const countItem = name => {
    const it = mcData.itemsByName[name];
    if (!it) return 0;
    return bot.inventory.count(it.id, null);
  };
  const ingotsHave = countItem(ingotName);
  if (ingotsHave >= targetIngots) return {
    success: true
  };

  // Inventory-first: task contract says we should have >= 5 raw_iron into iron_ingots
  // so ensure we can smelt up to what's needed.
  const neededIngots = targetIngots - ingotsHave;
  const rawHave = countItem(rawName);
  if (rawHave < neededIngots) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Choose a fuel we can afford for exactly the needed smelts.
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  let fuelName = null;
  for (const c of fuelCandidates) {
    if (countItem(c) >= neededIngots) {
      fuelName = c;
      break;
    }
  }
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL");

  // Ensure furnace exists. If not, place one.
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

  // Inventory-result: smelt exactly neededIngots to reach at least 5 ingots.
  // smeltItem helper is expected to handle furnace interaction.
  const ingotsBefore = countItem(ingotName);
  await smeltItem(bot, rawName, fuelName, neededIngots);
  const ingotsAfter = countItem(ingotName);
  if (ingotsAfter < targetIngots) throw new Error("SMELTING_FAILED_TO_REACH_TARGET_INGOTS");

  // Ensure we consumed raw iron (at least expected amount).
  const rawAfter = countItem(rawName);
  const consumedRaw = rawHave - rawAfter;
  if (consumedRaw < neededIngots) throw new Error("SMELTING_DID_NOT_CONSUME_EXPECTED_RAW_IRON");

  // Recheck completion.
  if (countItem(ingotName) >= targetIngots) return {
    success: true
  };
  throw new Error("CONTRACT_CHECK_FAILED");
}