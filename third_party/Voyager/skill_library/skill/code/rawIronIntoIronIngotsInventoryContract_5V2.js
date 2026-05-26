async function rawIronIntoIronIngotsTask_5RawTo5IngotContract(bot) {
  if (!bot) throw new Error("BOT_MISSING");
  if (typeof mcData === "undefined" || !mcData) throw new Error("MCDATA_MISSING");
  const countItem = name => {
    const it = mcData.itemsByName?.[name];
    if (!it) return 0;
    return bot.inventory.count(it.id, null);
  };
  const rawName = "raw_iron";
  const ingotName = "iron_ingot";
  const target = 5;

  // Already satisfied by inventory.
  const ingotsHave = countItem(ingotName);
  if (ingotsHave >= target) return {
    success: true
  };
  const neededRaw = target - ingotsHave;
  const rawHave = countItem(rawName);
  if (rawHave < neededRaw) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Choose fuel we have in sufficient quantity.
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  let fuelName = null;
  for (const c of fuelCandidates) {
    if (countItem(c) >= neededRaw) {
      fuelName = c;
      break;
    }
  }
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL_FOR_NEEDED_SMELTS");

  // Ensure furnace exists (if missing, place one from inventory).
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    if (countItem("furnace") < 1) throw new Error("NO_FURNACE_AVAILABLE");
    // placeItem expects the item; place it near current position
    await placeItem(bot, "furnace", bot.entity.position.offset(2, 0, 0));
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32
    });
    if (!furnaceBlock) throw new Error("FURNACE_NOT_AVAILABLE_AFTER_PLACEMENT");
  }

  // Smelt exactly enough raw iron to satisfy the contract.
  const ingotsBefore = ingotsHave;
  const rawBefore = rawHave;
  await smeltItem(bot, rawName, fuelName, neededRaw);
  const ingotsAfter = countItem(ingotName);
  const rawAfter = countItem(rawName);
  if (ingotsAfter < target) throw new Error("SMELTING_FAILED_TO_REACH_TARGET_INGOTS");
  const consumedRaw = rawBefore - rawAfter;
  if (consumedRaw < neededRaw) throw new Error("SMELTING_DID_NOT_CONSUME_EXPECTED_RAW_IRON");
  if (ingotsAfter < ingotsBefore + neededRaw) throw new Error("SMELTING_OUTPUT_INSUFFICIENT");
  return {
    success: true
  };
}

// main

// main
async function rawIronIntoIronIngotsInventoryContract_5(bot) {
  return rawIronIntoIronIngotsTask_5RawTo5IngotContract(bot);
}