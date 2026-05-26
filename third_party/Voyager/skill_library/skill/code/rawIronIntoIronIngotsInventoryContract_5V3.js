async function smelt5RawIronToIronIngotsTask(bot) {
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
  const ingotsHave = countItem(ingotName);
  if (ingotsHave >= target) return {
    success: true
  };
  const neededRaw = target - ingotsHave;
  const rawHave = countItem(rawName);
  if (rawHave < neededRaw) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Ensure furnace exists near
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

  // Choose fuel with enough quantity for the needed number of smelts.
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  let fuelName = null;
  for (const c of fuelCandidates) {
    if (countItem(c) >= neededRaw) {
      fuelName = c;
      break;
    }
  }
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL_FOR_NEEDED_SMELTS");

  // Smelt enough to reach >= target ingots (inventory-result contract)
  await smeltItem(bot, rawName, fuelName, neededRaw);
  const ingotsAfter = countItem(ingotName);
  const rawAfter = countItem(rawName);
  if (ingotsAfter < target) throw new Error("SMELTING_FAILED_TO_REACH_TARGET_INGOTS");

  // Basic sanity: raw iron should have been consumed at least the needed amount,
  // assuming the helper smelt loop consumed one raw item per iteration.
  const consumedRaw = rawHave - rawAfter;
  if (consumedRaw < neededRaw) throw new Error("SMELTING_DID_NOT_CONSUME_EXPECTED_RAW_IRON");
  return {
    success: true
  };
}

// main

// main
async function rawIronIntoIronIngotsInventoryContract_5(bot) {
  // Contract: "Have 5 raw_iron into iron_ingots"
  // Inventory-result shorthand: success means we reach at least 5 iron_ingot.
  return smelt5RawIronToIronIngotsTask(bot);
}