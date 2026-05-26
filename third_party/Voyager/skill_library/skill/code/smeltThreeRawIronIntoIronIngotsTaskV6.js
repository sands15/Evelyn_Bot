async function smeltThreeRawIronIntoIronIngotsTask(bot) {
  // Preconditions / completion check
  if (!bot || !mcData) throw new Error("BOT_OR_MCDATA_MISSING");
  const itemByName = name => mcData.itemsByName[name];
  const invCount = name => {
    const it = itemByName(name);
    if (!it) return 0;
    return bot.inventory.count(it.id, null);
  };
  const targetIngotCount = 3;
  const currentIngot = invCount("iron_ingot");
  if (currentIngot >= targetIngotCount) return {
    success: true
  };
  const rawNeeded = Math.max(0, Math.min(targetIngotCount - currentIngot, 3));
  if (invCount("raw_iron") < rawNeeded) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Ensure we have a furnace to use (place only if furnace item exists)
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    if (invCount("furnace") < 1) throw new Error("NO_FURNACE_NEARBY");
    await placeItem(bot, "furnace", bot.entity.position.offset(2, 0, 0));
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32
    });
    if (!furnaceBlock) throw new Error("FURNACE_NOT_AVAILABLE_AFTER_PLACEMENT");
  }

  // Choose fuel: prefer coal, then planks, then logs (quantity must cover needed smelts)
  let fuelName = null;
  if (invCount("coal") >= rawNeeded) fuelName = "coal";else if (invCount("oak_planks") >= rawNeeded) fuelName = "oak_planks";else if (invCount("birch_planks") >= rawNeeded) fuelName = "birch_planks";else if (invCount("oak_log") >= rawNeeded) fuelName = "oak_log";else if (invCount("birch_log") >= rawNeeded) fuelName = "birch_log";else throw new Error("NOT_ENOUGH_FUEL");

  // Smelt
  await smeltItem(bot, "raw_iron", fuelName, rawNeeded);

  // Completion re-check
  const finalIngot = invCount("iron_ingot");
  if (finalIngot >= targetIngotCount) return {
    success: true
  };
  throw new Error("SMELTING_FAILED_TO_PRODUCE_3_INGOTS");
}