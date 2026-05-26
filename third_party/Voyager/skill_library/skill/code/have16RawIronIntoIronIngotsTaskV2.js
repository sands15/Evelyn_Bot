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
  let neededSmelts = targetIngots - countItem(ingotName);
  if (countItem(rawName) < neededSmelts) {
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
      if (placed) break;
      const targetBlock = bot.blockAt(pos);
      const belowBlock = bot.blockAt(pos.offset(0, -1, 0));
      if (!targetBlock || targetBlock.name !== "air") continue;
      if (!belowBlock || belowBlock.name === "air") continue;
      try {
        await placeItem(bot, "furnace", pos);
      } catch (err) {
        // Try another nearby valid spot; placement can time out even in reachable areas.
      }
      furnaceBlock = bot.findBlock({
        matching: mcData.blocksByName.furnace.id,
        maxDistance: 32
      });
      if (furnaceBlock) placed = true;
    }
    if (!furnaceBlock) throw new Error("FURNACE_NOT_AVAILABLE_AFTER_PLACEMENT");
  }
  const ingotsBefore = countItem(ingotName);
  let remaining = targetIngots - ingotsBefore;
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  for (const fuelName of fuelCandidates) {
    if (remaining <= 0) break;
    const fuelAvailable = countItem(fuelName);
    if (fuelAvailable <= 0) continue;
    const batch = Math.min(remaining, fuelAvailable);
    await smeltItem(bot, rawName, fuelName, batch);
    remaining = targetIngots - countItem(ingotName);
  }
  if (countItem(ingotName) < targetIngots) {
    throw new Error("NOT_ENOUGH_FUEL_OR_SMELTING_FAILED_TO_REACH_16_INGOTS");
  }
  return {
    success: true
  };
}