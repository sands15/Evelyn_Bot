async function haveEightIronOreSmeltedIntoIngots(bot) {
  if (!bot || typeof mcData === "undefined" || !mcData) {
    throw new Error("BOT_OR_MCDATA_MISSING");
  }
  const countItem = name => {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  };
  const targetIngots = 8;
  const oreName = "iron_ore";
  const ingotName = "iron_ingot";
  if (countItem(ingotName) >= targetIngots) {
    return {
      success: true
    };
  }
  const neededIngots = targetIngots - countItem(ingotName);
  if (countItem(oreName) < neededIngots) {
    const missingOre = neededIngots - countItem(oreName);
    await mineBlock(bot, oreName, missingOre);
    if (countItem(oreName) < neededIngots) {
      const result = await searchForOre(bot, {
        goalType: oreName,
        quantity: neededIngots - countItem(oreName),
        maxSearchBudgetSec: 30
      });
      if (!result?.success && countItem(oreName) < neededIngots) {
        throw new Error(result?.reason || "NOT_ENOUGH_IRON_ORE");
      }
    }
  }
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    if (countItem("furnace") < 1) {
      if (countItem("cobblestone") < 8) {
        await mineBlock(bot, "stone", 8 - countItem("cobblestone"));
      }
      await craftItem(bot, "furnace", 1);
    }
    await placeItem(bot, "furnace", bot.entity.position.offset(1, 0, 0));
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32
    });
    if (!furnaceBlock) throw new Error("FURNACE_NOT_FOUND");
  }
  const fuelCandidates = ["coal", "charcoal", "oak_planks", "birch_planks", "oak_log", "birch_log"];
  let fuelName = null;
  for (const fuel of fuelCandidates) {
    if (countItem(fuel) >= neededIngots) {
      fuelName = fuel;
      break;
    }
  }
  if (!fuelName) {
    const coalNeeded = neededIngots - countItem("coal");
    if (coalNeeded > 0) {
      await mineBlock(bot, "coal_ore", coalNeeded);
    }
    if (countItem("coal") >= neededIngots) {
      fuelName = "coal";
    }
  }
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL_FOR_SMELTING");
  if (countItem(oreName) < neededIngots) {
    throw new Error("NOT_ENOUGH_IRON_ORE_FOR_SMELTING");
  }
  await smeltItem(bot, oreName, fuelName, neededIngots);
  if (countItem(ingotName) < targetIngots) {
    throw new Error("FAILED_TO_SMELT_8_IRON_INGOTS");
  }
  return {
    success: true
  };
}