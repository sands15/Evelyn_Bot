async function haveAtLeastIronIngots(bot, needed) {
  const item = mcData.itemsByName["iron_ingot"];
  if (!item) return false;
  return bot.inventory.count(item.id, null) >= needed;
}

async function haveAtLeastRawIron(bot, needed) {
  const item = mcData.itemsByName["raw_iron"];
  if (!item) return false;
  return bot.inventory.count(item.id, null) >= needed;
}

async function ironSmeltRawIronIntoIronIngotsTask(bot) {
  if (!bot || !mcData) throw new Error("BOT_OR_MCDATA_MISSING");
  const targetIngots = 5;

  // Re-check global sufficiency first
  if (await haveAtLeastIronIngots(bot, targetIngots)) return {
    success: true
  };
  const rawItem = mcData.itemsByName["raw_iron"];
  const ingotItem = mcData.itemsByName["iron_ingot"];
  if (!rawItem || !ingotItem) throw new Error("MISSING_ITEM_DEFS");
  const currentIngots = bot.inventory.count(ingotItem.id, null);
  const neededRaw = targetIngots - currentIngots;
  if (neededRaw <= 0) return {
    success: true
  };
  if (!(await haveAtLeastRawIron(bot, neededRaw))) {
    throw new Error("NOT_ENOUGH_RAW_IRON");
  }

  // Ensure furnace exists or place one
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    const furnaceItem = mcData.itemsByName["furnace"];
    if (!furnaceItem) throw new Error("MISSING_FURNACE_BLOCK_DEF");
    const furnaceInInv = bot.inventory.count(furnaceItem.id, null);
    if (furnaceInInv < 1) throw new Error("NO_FURNACE_AVAILABLE");
    await placeItem(bot, "furnace", bot.entity.position.offset(2, 0, 0));
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32
    });
    if (!furnaceBlock) throw new Error("FURNACE_NOT_AVAILABLE_AFTER_PLACEMENT");
  }

  // Pick a fuel we have (must be >= neededRaw for this contract)
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  let fuelName = null;
  for (const c of fuelCandidates) {
    const it = mcData.itemsByName[c];
    if (!it) continue;
    if (bot.inventory.count(it.id, null) >= neededRaw) {
      fuelName = c;
      break;
    }
  }
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL_FOR_NEEDED_SMELTS");

  // Snapshot inventory
  const rawBefore = bot.inventory.count(rawItem.id, null);
  const ingotsBefore = bot.inventory.count(ingotItem.id, null);

  // Smelt exactly neededRaw
  await smeltItem(bot, "raw_iron", fuelName, neededRaw);
  const rawAfter = bot.inventory.count(rawItem.id, null);
  const ingotsAfter = bot.inventory.count(ingotItem.id, null);
  if (ingotsAfter < targetIngots) throw new Error("SMELTING_FAILED_TO_REACH_TARGET_INGOTS");
  if (rawBefore - rawAfter < neededRaw) throw new Error("SMELTING_DID_NOT_CONSUME_EXPECTED_RAW_IRON");
  return {
    success: true
  };
}