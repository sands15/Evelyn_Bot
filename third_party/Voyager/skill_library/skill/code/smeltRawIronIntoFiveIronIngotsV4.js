async function smeltRawIronIntoFiveIronIngots(bot) {
  if (!bot) throw new Error("BOT_MISSING");
  if (typeof mcData === "undefined" || !mcData) throw new Error("MCDATA_MISSING");
  const countItem = name => {
    const item = mcData.itemsByName[name];
    if (!item) return 0;
    return bot.inventory.count(item.id, null);
  };
  const targetIngots = 5;
  const rawName = "raw_iron";
  const ingotName = "iron_ingot";
  if (countItem(ingotName) >= targetIngots) {
    return {
      success: true
    };
  }
  let needed = targetIngots - countItem(ingotName);
  if (countItem(rawName) < needed) throw new Error("NOT_ENOUGH_RAW_IRON");
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    if (countItem("furnace") < 1) throw new Error("NO_FURNACE_AVAILABLE");
    const base = bot.entity.position.floored();
    const offsets = [new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1), new Vec3(2, 0, 0), new Vec3(0, 0, 2)];
    let placed = false;
    for (const offset of offsets) {
      const pos = base.plus(offset);
      const block = bot.blockAt(pos);
      const below = bot.blockAt(pos.offset(0, -1, 0));
      if (block && block.name === "air" && below && below.name !== "air") {
        await placeItem(bot, "furnace", pos);
        placed = true;
        break;
      }
    }
    if (!placed) throw new Error("NO_SAFE_FURNACE_PLACEMENT");
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32
    });
    if (!furnaceBlock) throw new Error("FURNACE_NOT_AVAILABLE_AFTER_PLACEMENT");
  }
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "oak_log", "birch_log", "cherry_log"];
  for (const fuelName of fuelCandidates) {
    if (needed <= 0) break;
    const fuelAvailable = countItem(fuelName);
    if (fuelAvailable <= 0) continue;
    const batch = Math.min(needed, fuelAvailable, countItem(rawName));
    if (batch <= 0) continue;
    await smeltItem(bot, rawName, fuelName, batch);
    if (countItem(ingotName) >= targetIngots) {
      return {
        success: true
      };
    }
    needed = targetIngots - countItem(ingotName);
  }
  if (countItem(ingotName) < targetIngots) {
    throw new Error("SMELTING_FAILED_TO_REACH_TARGET_INGOTS");
  }
  return {
    success: true
  };
}