async function haveEightIronOreIntoIronIngots(bot) {
  if (!bot || typeof mcData === "undefined" || !mcData) {
    throw new Error("BOT_OR_MCDATA_MISSING");
  }
  const countItem = name => {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  };
  const targetIngots = 8;
  const ingotName = "iron_ingot";
  const oreName = "iron_ore";
  if (countItem(ingotName) >= targetIngots) {
    return {
      success: true
    };
  }
  const neededIngots = targetIngots - countItem(ingotName);
  if (countItem(oreName) < neededIngots) {
    await mineBlock(bot, oreName, neededIngots - countItem(oreName));
    if (countItem(oreName) < neededIngots) {
      throw new Error("NOT_ENOUGH_IRON_ORE");
    }
  }
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    if (countItem("furnace") < 1) {
      throw new Error("NO_FURNACE_AVAILABLE");
    }
    await placeItem(bot, "furnace", bot.entity.position.offset(1, 0, 0));
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32
    });
    if (!furnaceBlock) {
      throw new Error("FURNACE_NOT_FOUND_AFTER_PLACEMENT");
    }
  }
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log"];
  let fuelName = null;
  for (const fuel of fuelCandidates) {
    if (countItem(fuel) >= neededIngots) {
      fuelName = fuel;
      break;
    }
  }
  if (!fuelName) {
    throw new Error("NOT_ENOUGH_FUEL_FOR_SMELTING");
  }
  await smeltItem(bot, oreName, fuelName, neededIngots);
  if (countItem(ingotName) < targetIngots) {
    throw new Error("FAILED_TO_SMELT_8_IRON_INGOTS");
  }
  return {
    success: true
  };
}