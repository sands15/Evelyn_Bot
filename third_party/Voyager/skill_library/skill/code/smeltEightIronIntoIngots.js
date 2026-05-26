async function smeltEightIronIntoIngots(bot) {
  if (!bot || typeof mcData === "undefined" || !mcData) {
    throw new Error("BOT_OR_MCDATA_MISSING");
  }
  const countItem = name => {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  };
  const findPlacePos = () => {
    const base = bot.entity.position.floored();
    const offsets = [new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1), new Vec3(1, 0, 1), new Vec3(-1, 0, -1)];
    for (const offset of offsets) {
      const pos = base.plus(offset);
      const block = bot.blockAt(pos);
      const below = bot.blockAt(pos.offset(0, -1, 0));
      if (block?.name === "air" && below && below.name !== "air") return pos;
    }
    return base.offset(1, 0, 0);
  };
  const target = 8;
  if (countItem("iron_ingot") >= target) return {
    success: true
  };
  let needed = target - countItem("iron_ingot");
  if (countItem("iron_ore") < needed && countItem("raw_iron") < needed) {
    await mineBlock(bot, "iron_ore", needed - Math.max(countItem("iron_ore"), countItem("raw_iron")));
  }
  let inputName = null;
  if (countItem("iron_ore") >= needed) inputName = "iron_ore";else if (countItem("raw_iron") >= needed) inputName = "raw_iron";
  if (!inputName) {
    const result = await searchForOre(bot, {
      goalType: "iron_ore",
      quantity: needed,
      maxSearchBudgetSec: 20
    });
    if (!result?.success && countItem("iron_ore") < needed && countItem("raw_iron") < needed) {
      throw new Error(result?.reason || "NOT_ENOUGH_IRON_INPUT");
    }
    inputName = countItem("iron_ore") >= needed ? "iron_ore" : "raw_iron";
  }
  let furnaceBlock = bot.findBlock({
    matching: mcData.blocksByName.furnace.id,
    maxDistance: 32
  });
  if (!furnaceBlock) {
    if (countItem("furnace") < 1) throw new Error("NO_FURNACE_AVAILABLE");
    await placeItem(bot, "furnace", findPlacePos());
    furnaceBlock = bot.findBlock({
      matching: mcData.blocksByName.furnace.id,
      maxDistance: 32
    });
    if (!furnaceBlock) throw new Error("FURNACE_NOT_FOUND_AFTER_PLACEMENT");
  }
  let fuelName = null;
  const fuelCandidates = ["coal", "charcoal", "oak_planks", "birch_planks", "spruce_planks", "oak_log", "birch_log", "spruce_log"];
  for (const fuel of fuelCandidates) {
    if (countItem(fuel) >= needed) {
      fuelName = fuel;
      break;
    }
  }
  if (!fuelName && countItem("oak_log") > 0) {
    await craftItem(bot, "oak_planks", countItem("oak_log"));
    if (countItem("oak_planks") >= needed) fuelName = "oak_planks";
  }
  if (!fuelName) throw new Error("NOT_ENOUGH_FUEL_FOR_SMELTING");
  if (countItem(inputName) < needed) throw new Error("NOT_ENOUGH_IRON_INPUT_FOR_SMELTING");
  await smeltItem(bot, inputName, fuelName, needed);
  if (countItem("iron_ingot") < target) {
    throw new Error("FAILED_TO_SMELT_8_IRON_INGOTS");
  }
  return {
    success: true
  };
}