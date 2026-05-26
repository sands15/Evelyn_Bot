async function have16RawIronIntoIronIngotsTask(bot) {
  if (!bot) throw new Error("BOT_MISSING");
  if (typeof mcData === "undefined" || !mcData) throw new Error("MCDATA_MISSING");
  const countItem = name => {
    const item = mcData.itemsByName[name];
    if (!item) return 0;
    return bot.inventory.count(item.id, null);
  };
  const targetIngots = 16;
  const rawName = "raw_iron";
  const ingotName = "iron_ingot";
  if (countItem(ingotName) >= targetIngots) {
    return {
      success: true
    };
  }
  const neededRaw = targetIngots - countItem(ingotName);
  if (countItem(rawName) < neededRaw) {
    throw new Error("NOT_ENOUGH_RAW_IRON");
  }
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    if (countItem("furnace") < 1) throw new Error("NO_FURNACE_AVAILABLE");
    const base = bot.entity.position.floored();
    const positions = [base.offset(1, 0, 0), base.offset(-1, 0, 0), base.offset(0, 0, 1), base.offset(0, 0, -1)];
    let placed = false;
    for (const pos of positions) {
      try {
        await placeItem(bot, "furnace", pos);
        placed = true;
        break;
      } catch (err) {
        // Try the next nearby position.
      }
    }
    if (!placed) throw new Error("FURNACE_PLACEMENT_FAILED");
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32
    });
    if (!furnaceBlock) throw new Error("FURNACE_NOT_AVAILABLE_AFTER_PLACEMENT");
  }
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  let remaining = neededRaw;
  for (const fuelName of fuelCandidates) {
    if (remaining <= 0) break;
    const fuelCount = countItem(fuelName);
    if (fuelCount <= 0) continue;
    const batch = Math.min(remaining, fuelCount);
    await smeltItem(bot, rawName, fuelName, batch);
    remaining -= batch;
    if (countItem(ingotName) >= targetIngots) {
      return {
        success: true
      };
    }
  }
  if (countItem(ingotName) >= targetIngots) {
    return {
      success: true
    };
  }
  throw new Error("NOT_ENOUGH_FUEL_TO_REACH_16_INGOTS");
}