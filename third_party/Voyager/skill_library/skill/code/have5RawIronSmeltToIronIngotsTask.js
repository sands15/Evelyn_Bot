async function have5RawIronSmeltToIronIngotsTask(bot) {
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
  const rawHave = countItem(rawName);
  if (rawHave < 5) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Ensure we have a furnace and then smelt exactly 5 raw_iron into ingots
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    if (countItem("furnace") < 1) throw new Error("NO_FURNACE_AVAILABLE");
    // placeItem handles pathfinding + equip/placement
    await placeItem(bot, "furnace", bot.entity.position.offset(2, 0, 0));
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32
    });
    if (!furnaceBlock) throw new Error("FURNACE_NOT_AVAILABLE_AFTER_PLACEMENT");
  }

  // Choose fuel we have at least 5 of (smeltItem consumes per smelt)
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  let fuelName = null;
  for (const c of fuelCandidates) {
    if (countItem(c) >= 5) {
      fuelName = c;
      break;
    }
  }
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL_FOR_5_SMELTS");
  const ingotsBefore = countItem(ingotName);
  const rawBefore = countItem(rawName);

  // Inventory-result contract: consume 5 raw_iron and produce 5 ingots (or at least reach 5 ingots total)
  await smeltItem(bot, rawName, fuelName, 5);
  const ingotsAfter = countItem(ingotName);
  const rawAfter = countItem(rawName);
  if (ingotsAfter < targetIngots) throw new Error("SMELTING_FAILED_TO_REACH_5_INGOTS");

  // Verify we actually consumed 5 raw_iron
  const consumed = rawBefore - rawAfter;
  if (consumed < 5) throw new Error("SMELTING_DID_NOT_CONSUME_5_RAW_IRON");
  return {
    success: true,
    producedFromThisRun: ingotsAfter - ingotsBefore
  };
}