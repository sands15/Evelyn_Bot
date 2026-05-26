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
  const neededSmelts = targetIngots - countItem(ingotName);
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
    const placementOffsets = [new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1)];
    let placed = false;
    for (const offset of placementOffsets) {
      const pos = base.offset(offset.x, offset.y, offset.z);
      const block = bot.blockAt(pos);
      const below = bot.blockAt(pos.offset(0, -1, 0));
      if (block && block.name === "air" && below && below.name !== "air") {
        try {
          await placeItem(bot, "furnace", pos);
          placed = true;
          break;
        } catch (err) {
          // Try the next nearby safe placement spot.
        }
      }
    }
    if (!placed) throw new Error("FURNACE_PLACEMENT_FAILED");
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32
    });
    if (!furnaceBlock) throw new Error("FURNACE_NOT_AVAILABLE_AFTER_PLACEMENT");
  }
  let remaining = neededSmelts;
  const fuelCandidates = ["coal", "oak_planks", "birch_planks", "spruce_planks", "jungle_planks", "acacia_planks", "dark_oak_planks", "cherry_planks", "oak_log", "birch_log"];
  for (const fuelName of fuelCandidates) {
    if (remaining <= 0) break;
    const availableFuel = countItem(fuelName);
    if (availableFuel <= 0) continue;
    const batch = Math.min(remaining, availableFuel);
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
  throw new Error("NOT_ENOUGH_FUEL_OR_SMELTING_FAILED");
}