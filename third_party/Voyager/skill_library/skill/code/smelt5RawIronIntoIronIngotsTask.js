async function smelt5RawIronIntoIronIngotsTask(bot) {
  if (!bot || !mcData) throw new Error("BOT_OR_MCDATA_MISSING");
  const countItem = name => {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  };
  const hasEnough = (rawName, ingotName, targetIngot) => {
    const ingots = countItem(ingotName);
    return ingots >= targetIngot;
  };
  const targetRawToSmelt = 5;

  // Inventory-first goal check (inventory-result contract)
  if (countItem("iron_ingot") >= 5) return {
    success: true
  };
  const rawIron = countItem("raw_iron");
  if (rawIron < targetRawToSmelt) throw new Error("NOT_ENOUGH_RAW_IRON");

  // Ensure furnace exists
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    if (countItem("furnace") < 1) throw new Error("NO_FURNACE_NEARBY_OR_AVAILABLE");
    await placeItem(bot, "furnace", bot.entity.position.offset(2, 0, 0));
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32
    });
    if (!furnaceBlock) throw new Error("FURNACE_NOT_AVAILABLE_AFTER_PLACEMENT");
  }

  // Pick fuel that covers exactly targetRawToSmelt if possible
  let fuelName = null;
  if (countItem("coal") >= targetRawToSmelt) fuelName = "coal";else if (countItem("oak_planks") >= targetRawToSmelt) fuelName = "oak_planks";else if (countItem("birch_planks") >= targetRawToSmelt) fuelName = "birch_planks";else if (countItem("oak_log") >= targetRawToSmelt) fuelName = "oak_log";else if (countItem("birch_log") >= targetRawToSmelt) fuelName = "birch_log";else if (countItem("cherry_log") >= targetRawToSmelt) fuelName = "cherry_log";else throw new Error("NOT_ENOUGH_FUEL");
  const ingotsBefore = countItem("iron_ingot");
  const rawBefore = countItem("raw_iron");

  // Use provided primitive smeltItem (must already have furnace placed)
  await smeltItem(bot, "raw_iron", fuelName, targetRawToSmelt);
  const ingotsAfter = countItem("iron_ingot");
  const rawAfter = countItem("raw_iron");
  if (ingotsAfter - ingotsBefore < targetRawToSmelt) {
    // Contract: success means at least 5 raw_iron into iron_ingots (i.e., +5 ingots).
    // If not enough ingots produced, fail cleanly.
    throw new Error("SMELTING_FAILED_TO_CONVERT_5_RAW_IRON");
  }

  // Additional sanity check: raw iron should decrease by 5 (or at least not remain)
  if (rawBefore - rawAfter < targetRawToSmelt) {
    // Not strictly necessary if furnace took/converted differently, but keep it strict for contract.
    throw new Error("SMELTING_DID_NOT_CONSUME_EXPECTED_RAW_IRON");
  }
  if (countItem("iron_ingot") >= 5) return {
    success: true
  };
  throw new Error("SMELTING_FINISHED_BUT_INVENTORY_CONTRACT_NOT_MET");
}