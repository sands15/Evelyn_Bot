async function smeltThreeRawIronIntoIronIngotsTask(bot) {
  if (!bot || !mcData) throw new Error("BOT_OR_MCDATA_MISSING");
  const item = name => mcData.itemsByName[name];
  if (!item("raw_iron") || !item("iron_ingot") || !item("coal")) throw new Error("MISSING_MCITEMS");
  const countItem = name => {
    const it = mcData.itemsByName[name];
    return it ? bot.inventory.count(it.id, null) : 0;
  };

  // Already complete?
  if (countItem("iron_ingot") >= 3) return {
    success: true
  };
  const haveRaw = countItem("raw_iron");
  const needed = 3 - countItem("iron_ingot");
  if (haveRaw < needed) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Prefer coal; otherwise use planks/logs available.
  let fuelName = null;
  if (countItem("coal") >= needed) fuelName = "coal";else if (countItem("oak_planks") >= needed) fuelName = "oak_planks";else if (countItem("birch_planks") >= needed) fuelName = "birch_planks";else if (countItem("oak_log") >= needed) fuelName = "oak_log";else if (countItem("birch_log") >= needed) fuelName = "birch_log";
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL");

  // Ensure furnace exists nearby; if not, cannot proceed.
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) throw new Error("NO_FURNACE_NEARBY");
  const furnace = await bot.openFurnace(furnaceBlock);
  try {
    for (let i = 0; i < needed; i++) {
      await furnace.putFuel(mcData.itemsByName[fuelName].id, null, 1);
      await furnace.putInput(mcData.itemsByName["raw_iron"].id, null, 1);
      await bot.waitForTicks(12 * 20);
      await furnace.takeOutput();
    }
  } finally {
    await furnace.close();
  }
  if (countItem("iron_ingot") < 3) throw new Error("SMELTING_FAILED_TO_PRODUCE_3_INGOTS");
  return {
    success: true
  };
}