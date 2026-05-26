async function rawIronIntoIronIngotsInventoryContract_5(bot) {
  if (!bot) throw new Error("BOT_MISSING");
  if (!mcData) throw new Error("MCDATA_MISSING");
  const countItem = name => {
    const it = mcData.itemsByName?.[name];
    if (!it) return 0;
    return bot.inventory.count(it.id, null);
  };
  const rawName = "raw_iron";
  const ingotName = "iron_ingot";
  const target = 5;
  const ingotsBefore = countItem(ingotName);
  const rawHave = countItem(rawName);

  // Goal already satisfied if we have >=5 ingots.
  if (ingotsBefore >= target) return {
    success: true
  };
  const neededRaw = target - ingotsBefore;
  if (rawHave < neededRaw) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Choose fuel: must have at least neededRaw units (per helper contract).
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  let fuelName = null;
  for (const c of fuelCandidates) {
    if (countItem(c) >= neededRaw) {
      fuelName = c;
      break;
    }
  }
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL_FOR_NEEDED_SMELTS");

  // Ensure furnace exists; place if missing.
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    // Only place if we have a furnace item.
    if (countItem("furnace") < 1) throw new Error("NO_FURNACE_AVAILABLE");
    await placeItem(bot, "furnace", bot.entity.position.offset(2, 0, 0));
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32
    });
    if (!furnaceBlock) throw new Error("FURNACE_NOT_AVAILABLE_AFTER_PLACEMENT");
  }
  const rawBefore = rawHave;
  const ingotsBefore2 = ingotsBefore;

  // Inventory-result contract:
  // smelt exactly `neededRaw` inputs into outputs, success means we reach >=5 ingots.
  await smeltItem(bot, rawName, fuelName, neededRaw);
  const ingotsAfter = countItem(ingotName);
  const rawAfter = countItem(rawName);
  if (ingotsAfter < target) throw new Error("SMELTING_FAILED_TO_REACH_TARGET_INGOTS");

  // Basic conservation check: should consume at least some raw (ideally >= neededRaw).
  const consumedRaw = rawBefore - rawAfter;
  if (consumedRaw < neededRaw) throw new Error("SMELTING_DID_NOT_CONSUME_EXPECTED_RAW_IRON");

  // Ensure we increased ingots appropriately.
  if (ingotsAfter < ingotsBefore2 + neededRaw) {
    // Still fail to keep contract strict.
    throw new Error("SMELTING_OUTPUT_INSUFFICIENT");
  }
  return {
    success: true
  };
}