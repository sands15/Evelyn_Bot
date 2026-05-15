function countItem(bot, name) {
  const item = mcData.itemsByName[name];
  return item ? bot.inventory.count(item.id, null) : 0;
}

async function craftIronSwordFromAvailableRawIron(bot) {
  if (countItem(bot, "iron_sword") >= 1) return;
  if (countItem(bot, "stick") < 1) {
    if (countItem(bot, "jungle_planks") >= 2) {
      await craftItem(bot, "stick", 1);
    }
  }
  if (countItem(bot, "stick") < 1) {
    throw new Error("Need 1 stick to craft an iron_sword.");
  }
  let ingotsNeeded = 2 - countItem(bot, "iron_ingot");
  if (ingotsNeeded > 0) {
    if (countItem(bot, "raw_iron") < ingotsNeeded) {
      throw new Error("Need enough raw_iron to smelt 2 iron_ingot.");
    }
    const furnace = bot.findBlock({
      matching: mcData.blocksByName["furnace"].id,
      maxDistance: 32
    });
    if (!furnace) {
      throw new Error("Need a nearby furnace to smelt raw_iron.");
    }
    if (ingotsNeeded > 0 && countItem(bot, "jungle_planks") > 0) {
      await smeltItem(bot, "raw_iron", "jungle_planks", 1);
      ingotsNeeded = 2 - countItem(bot, "iron_ingot");
      if (ingotsNeeded <= 0) return await craftItem(bot, "iron_sword", 1);
    }
    if (ingotsNeeded > 0 && countItem(bot, "stick") > 1) {
      const spareStickFuel = Math.min(ingotsNeeded, countItem(bot, "stick") - 1);
      await smeltItem(bot, "raw_iron", "stick", spareStickFuel);
      ingotsNeeded = 2 - countItem(bot, "iron_ingot");
    }
  }
  if (countItem(bot, "iron_ingot") < 2) {
    throw new Error("Need 2 iron_ingot to craft an iron_sword.");
  }
  if (countItem(bot, "stick") < 1) {
    throw new Error("Need 1 stick to craft an iron_sword.");
  }
  const craftingTable = bot.findBlock({
    matching: mcData.blocksByName["crafting_table"].id,
    maxDistance: 32
  });
  if (!craftingTable) {
    throw new Error("Need a nearby crafting_table to craft an iron_sword.");
  }
  await craftItem(bot, "iron_sword", 1);
  if (countItem(bot, "iron_sword") < 1) {
    throw new Error("Failed to craft iron_sword.");
  }
}