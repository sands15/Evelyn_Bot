async function smelt6RawIronIntoIronIngotsTask(bot) {
  if (!bot || !mcData) throw new Error("BOT_OR_MCDATA_MISSING");
  const countItem = name => {
    const it = mcData.itemsByName[name];
    return it ? bot.inventory.count(it.id, null) : 0;
  };
  const targetIngots = 6;
  const rawHave = countItem("raw_iron");
  const ingotsHave = countItem("iron_ingot");

  // If already satisfied, stop.
  if (ingotsHave >= targetIngots) {
    return {
      success: true
    };
  }
  const neededRawToSmelt = targetIngots - ingotsHave;
  if (neededRawToSmelt <= 0) return {
    success: true
  };
  if (rawHave < neededRawToSmelt) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Ensure we have a furnace; if not, place one with robust local placement attempts.
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  const canUseExistingFurnace = !!furnaceBlock;
  if (!canUseExistingFurnace) {
    const furnaceItem = mcData.itemsByName["furnace"];
    if (!furnaceItem) throw new Error("NO_FURNACE_ITEM_IN_MCDATA");

    // Try to place nearby on a list of candidate adjacent positions.
    const candidates = [bot.entity.position.offset(2, 0, 0), bot.entity.position.offset(-2, 0, 0), bot.entity.position.offset(0, 0, 2), bot.entity.position.offset(0, 0, -2), bot.entity.position.offset(1, 0, 1), bot.entity.position.offset(1, 0, -1), bot.entity.position.offset(-1, 0, 1), bot.entity.position.offset(-1, 0, -1)];
    const haveFurnaceItem = bot.inventory.findInventoryItem(furnaceItem.id);
    if (!haveFurnaceItem) throw new Error("NO_FURNACE_NEARBY");
    let placed = false;
    let lastErr = null;
    for (const pos of candidates) {
      try {
        await placeItem(bot, "furnace", pos);
        furnaceBlock = bot.findBlock({
          matching: mcData.blocksByName.furnace.id,
          maxDistance: 32
        });
        if (furnaceBlock) {
          placed = true;
          break;
        }
      } catch (e) {
        lastErr = e;
      }
    }
    if (!placed || !furnaceBlock) {
      throw new Error("FURNACE_NOT_AVAILABLE_AFTER_PLACEMENT" + (lastErr ? "" : ""));
    }
  }

  // Choose fuel for exactly neededRawToSmelt smelts.
  let fuelName = null;
  if (countItem("coal") >= neededRawToSmelt) fuelName = "coal";else if (countItem("oak_planks") >= neededRawToSmelt) fuelName = "oak_planks";else if (countItem("birch_planks") >= neededRawToSmelt) fuelName = "birch_planks";else if (countItem("oak_log") >= neededRawToSmelt) fuelName = "oak_log";else if (countItem("birch_log") >= neededRawToSmelt) fuelName = "birch_log";else if (countItem("cherry_log") >= neededRawToSmelt) fuelName = "cherry_log";else throw new Error("NOT_ENOUGH_FUEL");
  await smeltItem(bot, "raw_iron", fuelName, neededRawToSmelt);
  if (countItem("iron_ingot") >= targetIngots) return {
    success: true
  };
  throw new Error("SMELTING_FAILED_TO_PRODUCE_6_INGOTS");
}

async function yourMainFunctionName(bot) {
  return await smelt6RawIronIntoIronIngotsTask(bot);
}