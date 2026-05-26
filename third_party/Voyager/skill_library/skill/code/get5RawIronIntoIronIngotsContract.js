async function get5RawIronIntoIronIngotsContract(bot) {
  if (!bot || !mcData) throw new Error("BOT_OR_MCDATA_MISSING");
  const countItem = name => {
    const it = mcData.itemsByName?.[name];
    if (!it) return 0;
    return bot.inventory.count(it.id, null);
  };
  const rawName = "raw_iron";
  const ingotName = "iron_ingot";
  const target = 5;
  const ingotsBefore = countItem(ingotName);
  if (ingotsBefore >= target) return {
    success: true
  };
  const neededRaw = target - ingotsBefore;
  const rawHave = countItem(rawName);
  if (rawHave < neededRaw) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Choose fuel that we have enough quantity for the needed smelts.
  // smeltItem will putFuel/putInput per iteration; we interpret helper contract as 1 fuel item per smelt iteration.
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  let fuelName = null;
  for (const c of fuelCandidates) {
    if (countItem(c) >= neededRaw) {
      fuelName = c;
      break;
    }
  }
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL_FOR_NEEDED_SMELTS");

  // Ensure furnace exists; if missing, place one.
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
  const rawBefore = countItem(rawName);
  const ingotsBefore2 = ingotsBefore;

  // Inventory-result contract: success => inventory reaches at least 5 raw_iron into iron_ingots
  // Interpreted here as: after smelting, ingots increased enough to reach >= target, consuming the needed raw amount.
  await smeltItem(bot, rawName, fuelName, neededRaw);
  const ingotsAfter = countItem(ingotName);
  const rawAfter = countItem(rawName);
  if (ingotsAfter < target) throw new Error("SMELTING_FAILED_TO_REACH_TARGET_INGOTS");
  const consumedRaw = rawBefore - rawAfter;
  if (consumedRaw < neededRaw) throw new Error("SMELTING_DID_NOT_CONSUME_EXPECTED_RAW_IRON");
  if (ingotsAfter < ingotsBefore2 + neededRaw) {
    throw new Error("SMELTING_OUTPUT_INSUFFICIENT");
  }
  return {
    success: true
  };
}