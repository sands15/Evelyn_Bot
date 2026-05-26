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
  if (countItem(rawName) < neededRaw) throw new Error("NOT_ENOUGH_RAW_IRON");
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    if (countItem("furnace") < 1) throw new Error("NO_FURNACE_AVAILABLE");
    const offsets = [new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1), new Vec3(2, 0, 0), new Vec3(0, 0, 2)];
    for (const offset of offsets) {
      const pos = bot.entity.position.floored().offset(offset.x, offset.y, offset.z);
      const targetBlock = bot.blockAt(pos);
      const belowBlock = bot.blockAt(pos.offset(0, -1, 0));
      if (targetBlock?.name === "air" && belowBlock?.name !== "air") {
        try {
          await placeItem(bot, "furnace", pos);
          furnaceBlock = bot.findBlock({
            matching: mcData.blocksByName.furnace.id,
            maxDistance: 32
          });
          if (furnaceBlock) break;
        } catch (err) {
          // Try another nearby safe placement position.
        }
      }
    }
    if (!furnaceBlock) throw new Error("FURNACE_NOT_AVAILABLE_AFTER_PLACEMENT");
  }
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  let remaining = neededRaw;
  for (const fuelName of fuelCandidates) {
    if (remaining <= 0) break;
    const fuelAvailable = countItem(fuelName);
    if (fuelAvailable <= 0) continue;
    const batch = Math.min(remaining, fuelAvailable);
    await smeltItem(bot, rawName, fuelName, batch);
    remaining -= batch;
    if (countItem(ingotName) >= targetIngots) {
      return {
        success: true
      };
    }
  }
  if (remaining > 0) throw new Error("NOT_ENOUGH_FUEL_FOR_NEEDED_SMELTS");
  if (countItem(ingotName) < targetIngots) throw new Error("SMELTING_FAILED_TO_REACH_16_INGOTS");
  return {
    success: true
  };
}