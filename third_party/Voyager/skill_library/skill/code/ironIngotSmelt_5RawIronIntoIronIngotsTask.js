async function ironIngotSmelt_5RawIronIntoIronIngotsTask(bot) {
  if (!bot || !mcData) throw new Error("BOT_OR_MCDATA_MISSING");
  const countItem = name => {
    const it = mcData.itemsByName[name];
    if (!it) return 0;
    return bot.inventory.count(it.id, null);
  };
  const rawName = "raw_iron";
  const ingotName = "iron_ingot";
  const targetIngots = 5;
  const ingotsHave = countItem(ingotName);
  if (ingotsHave >= targetIngots) return {
    success: true
  };
  const neededRaw = targetIngots - ingotsHave; // interpret as: smelt just enough to reach >= 5 ingots
  const rawHave = countItem(rawName);
  if (rawHave < neededRaw) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Ensure furnace exists (inventory-first; contract says it should exist)
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

  // Pick any fuel we have enough items for.
  // (Uses helper smeltItem; helper smelts once per iteration, consuming one unit fuel per iteration.)
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  let fuelName = null;
  for (const c of fuelCandidates) {
    if (countItem(c) >= neededRaw) {
      fuelName = c;
      break;
    }
  }
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL_FOR_NEEDED_SMELTS");
  const ingotsBefore = countItem(ingotName);
  const rawBefore = countItem(rawName);

  // Smelt using provided primitive (inventory-result contract).
  // If helper is implemented as in the prompt, it will smelt `count` times and consume matching raw input.
  await smeltItem(bot, rawName, fuelName, neededRaw);
  const ingotsAfter = countItem(ingotName);
  const rawAfter = countItem(rawName);
  if (ingotsAfter < targetIngots) throw new Error("SMELTING_FAILED_TO_REACH_TARGET_INGOTS");
  const consumedRaw = rawBefore - rawAfter;
  if (consumedRaw < neededRaw) throw new Error("SMELTING_DID_NOT_CONSUME_EXPECTED_RAW_IRON");

  // Success: inventory now has >= 5 iron_ingot obtained from raw_iron smelts.
  if (ingotsAfter < ingotsBefore + neededRaw) {
    // If it produced fewer than neededRaw ingots, still fail the contract.
    // (This guards against partial/failed smelt behavior.)
    throw new Error("SMELTING_OUTPUT_INSUFFICIENT");
  }
  return {
    success: true
  };
}