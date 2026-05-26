async function smeltEightIronOreIntoIngots(bot) {
  if (!bot || typeof mcData === "undefined" || !mcData) {
    throw new Error("BOT_OR_MCDATA_MISSING");
  }
  const countItem = name => {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  };
  const findSafePlacePosition = () => {
    const base = bot.entity.position.floored();
    const offsets = [new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1), new Vec3(1, 0, 1), new Vec3(-1, 0, -1)];
    for (const offset of offsets) {
      const pos = base.plus(offset);
      const block = bot.blockAt(pos);
      const below = bot.blockAt(pos.offset(0, -1, 0));
      if (block?.name === "air" && below && below.name !== "air") {
        return pos;
      }
    }
    return base.offset(1, 0, 0);
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
    await mineBlock(bot, oreName, neededIngots - countItem(oreName));
    if (countItem(oreName) < neededIngots) {
      const result = await searchForOre(bot, {
        goalType: "iron_ore",
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
    await placeItem(bot, "furnace", findSafePlacePosition());
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32
    });
    if (!furnaceBlock) {
      throw new Error("FURNACE_NOT_FOUND");
    }
  }
  let fuelName = null;
  const fuelCandidates = ["coal", "charcoal", "oak_planks", "birch_planks", "oak_log", "birch_log"];
  for (const fuel of fuelCandidates) {
    if (countItem(fuel) >= neededIngots) {
      fuelName = fuel;
      break;
    }
  }
  if (!fuelName && countItem("oak_log") > 0) {
    await craftItem(bot, "oak_planks", countItem("oak_log"));
    if (countItem("oak_planks") >= neededIngots) {
      fuelName = "oak_planks";
    }
  }
  if (!fuelName && countItem("coal") < neededIngots) {
    await mineBlock(bot, "coal_ore", neededIngots - countItem("coal"));
    if (countItem("coal") >= neededIngots) {
      fuelName = "coal";
    }
  }
  if (!fuelName) {
    throw new Error("NOT_ENOUGH_FUEL_FOR_SMELTING");
  }
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