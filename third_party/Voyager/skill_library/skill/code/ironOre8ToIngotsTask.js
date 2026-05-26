async function ironOre8ToIngotsTask(bot) {
  if (!bot || typeof mcData === "undefined" || !mcData) throw new Error("BOT_OR_MCDATA_MISSING");
  const itemIdByName = name => mcData.itemsByName?.[name]?.id;
  const countItemByName = name => {
    const id = itemIdByName(name);
    if (!id) return 0;
    return bot.inventory.count(id, null);
  };
  const rawOreName = "iron_ore";
  const ingotName = "iron_ingot";
  const targetIngots = 8;

  // Global sufficiency check: success means inventory reaches >= 8 iron_ore consumed
  // and >= 8 iron_ingot present after smelting.
  const ingotsHave = countItemByName(ingotName);
  if (ingotsHave >= targetIngots) return {
    success: true
  };
  const neededIngots = targetIngots - ingotsHave;
  let oreHave = countItemByName(rawOreName);
  if (oreHave < neededIngots) {
    const stillNeedOre = neededIngots - oreHave;

    // Prefer nearby ore first (we already have a furnace nearby per context; also "coal_ore" nearby).
    // Try direct local collection within 32 blocks.
    const oreBlocks = bot.findBlocks({
      matching: block => block?.name === rawOreName,
      maxDistance: 32,
      count: stillNeedOre
    });
    const collected = [];
    const cap = Math.min(oreBlocks.length, stillNeedOre);
    for (let i = 0; i < cap; i++) collected.push(bot.blockAt(oreBlocks[i]));
    if (collected.length > 0) {
      await bot.collectBlock.collect(collected, {
        ignoreNoPath: true
      });
    } else {
      // If no ore nearby, do an intent-level underground ore search.
      // (searchForOre is available in prompt; use it rather than wandering.)
      await searchForOre(bot, {
        goalType: "iron_ore",
        quantity: stillNeedOre,
        maxSearchBudgetSec: 30
      });
      oreHave = countItemByName(rawOreName);
      if (oreHave < neededIngots) throw new Error("NOT_ENOUGH_IRON_ORE_NEARBY");
    }
  }

  // Ensure furnace exists.
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    // Contract says a furnace may be available; if missing, place one if we have.
    if (countItemByName("furnace") < 1) throw new Error("NO_FURNACE_AVAILABLE");
    await placeItem(bot, "furnace", bot.entity.position.offset(2, 0, 0));
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32
    });
    if (!furnaceBlock) throw new Error("FURNACE_NOT_FOUND_AFTER_PLACEMENT");
  }

  // Choose fuel we have at least for the number of smelts.
  // smeltItem consumes fuel one per smelt iteration (helper uses putFuel(..., 1)).
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  let fuelName = null;
  for (const f of fuelCandidates) {
    if (countItemByName(f) >= neededIngots) {
      fuelName = f;
      break;
    }
  }
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL_FOR_SMELTING");

  // Smelt exactly the missing amount using helper (inventory-result contract).
  const ingotsBefore = countItemByName(ingotName);
  const oreBefore = countItemByName(rawOreName);

  // Ensure we don't rely on oreBefore having exactly neededIngots; smeltItem will be called with neededIngots.
  if (oreBefore < neededIngots) throw new Error("NOT_ENOUGH_IRON_ORE_FOR_SMELTING");

  // Make sure we have the furnace ready for helper.
  await smeltItem(bot, rawOreName, fuelName, neededIngots);
  const ingotsAfter = countItemByName(ingotName);
  if (ingotsAfter < targetIngots) throw new Error("SMELTING_FAILED_TO_REACH_8_INGOTS");

  // Optional contract strengthening: at least neededIngots ore consumed.
  const oreAfter = countItemByName(rawOreName);
  const oreConsumed = oreBefore - oreAfter;
  if (oreConsumed < neededIngots) throw new Error("SMELTING_DID_NOT_CONSUME_EXPECTED_ORE");
  return {
    success: true
  };
}