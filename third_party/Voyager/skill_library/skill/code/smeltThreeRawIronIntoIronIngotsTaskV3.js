async function smeltThreeRawIronIntoIronIngotsTask(bot) {
  if (!bot || !mcData) throw new Error("BOT_OR_MCDATA_MISSING");
  const countItem = name => {
    const it = mcData.itemsByName[name];
    return it ? bot.inventory.count(it.id, null) : 0;
  };
  const rawNeed = 3;
  if (countItem("iron_ingot") >= rawNeed) return {
    success: true
  };
  const rawHave = countItem("raw_iron");
  const needed = rawNeed - countItem("iron_ingot");
  if (rawHave < needed) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Prefer coal; otherwise use planks/logs available.
  let fuelName = null;
  if (countItem("coal") >= needed) fuelName = "coal";else if (countItem("oak_planks") >= needed) fuelName = "oak_planks";else if (countItem("birch_planks") >= needed) fuelName = "birch_planks";else if (countItem("oak_log") >= needed) fuelName = "oak_log";else if (countItem("birch_log") >= needed) fuelName = "birch_log";
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL");

  // Ensure we have a furnace nearby; if not, abort (no long survival chain here).
  const furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) throw new Error("NO_FURNACE_NEARBY");

  // Use provided helper which expects furnace exists nearby (already ensured).
  await smeltItem(bot, "raw_iron", fuelName, needed);
  if (countItem("iron_ingot") < rawNeed) {
    throw new Error("SMELTING_FAILED_TO_PRODUCE_3_INGOTS");
  }
  return {
    success: true
  };
}