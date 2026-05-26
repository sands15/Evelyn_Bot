async function have5RawIronIntoIronIngotsTask(bot) {
  if (!bot) throw new Error("BOT_MISSING");
  if (typeof mcData === "undefined" || !mcData) throw new Error("MCDATA_MISSING");
  const rawName = "raw_iron";
  const ingotName = "iron_ingot";
  const countItem = name => {
    const it = mcData.itemsByName?.[name];
    if (!it) return 0;
    return bot.inventory.count(it.id, null);
  };
  const rawBefore = countItem(rawName);
  const ingotsBefore = countItem(ingotName);

  // Inventory-result contract: success means inventory reaches at least 5 raw_iron into iron_ingots.
  // Interpret as: ensure we can consume 5 raw_iron into iron_ingots, i.e. end up with >= ingotsBefore + 5
  // and have consumed 5 raw_iron (at least in effect).
  const targetSmelt = 5;

  // If we already have at least ingotsBefore + 5, treat as satisfied.
  // (We still verify consumption via raw count delta after smelting.)
  if (ingotsBefore >= 6e18) {
    // avoid weird edge; not expected
  }
  if (ingotsBefore >= ingotsBefore + targetSmelt) {
    // impossible; keep normal logic below
  }
  if (rawBefore < targetSmelt) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Ensure furnace exists
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    // We assume furnace can be placed; but only do this if inventory has one.
    const furnaceItem = mcData.itemsByName?.furnace;
    if (!furnaceItem || bot.inventory.count(furnaceItem.id, null) < 1) {
      throw new Error("NO_FURNACE_AVAILABLE");
    }
    await placeItem(bot, "furnace", bot.entity.position.offset(2, 0, 0));
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32
    });
    if (!furnaceBlock) throw new Error("FURNACE_NOT_FOUND_AFTER_PLACEMENT");
  }

  // Choose fuel that we have in sufficient quantity.
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  let fuelName = null;
  for (const c of fuelCandidates) {
    if (countItem(c) >= targetSmelt) {
      fuelName = c;
      break;
    }
  }
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL_FOR_NEEDED_SMELTS");

  // Re-check completion before work (in case another system changed inventory).
  const ingotsNow = countItem(ingotName);
  const rawNow = countItem(rawName);
  if (rawNow >= targetSmelt && ingotsNow >= ingotsBefore + targetSmelt) return {
    success: true
  };

  // Smelt exactly 5.
  await smeltItem(bot, rawName, fuelName, targetSmelt);
  const rawAfter = countItem(rawName);
  const ingotsAfter = countItem(ingotName);

  // Contract checks:
  // - Ingot gain should be at least 5.
  // - Raw iron should be reduced by at least 5.
  const ingotGain = ingotsAfter - ingotsBefore;
  const rawConsumed = rawBefore - rawAfter;
  if (ingotsAfter < ingotsBefore + targetSmelt) throw new Error("SMELTING_OUTPUT_INSUFFICIENT");
  if (rawConsumed < targetSmelt) throw new Error("SMELTING_DID_NOT_CONSUME_EXPECTED_RAW_IRON");
  return {
    success: true
  };
}

// main

// main
async function have5RawIronIntoIronIngotsTaskRunner(bot) {
  return have5RawIronIntoIronIngotsTask(bot);
}