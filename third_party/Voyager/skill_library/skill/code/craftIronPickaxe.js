function countItem(bot, name) {
  const item = mcData.itemsByName[name];
  return item ? bot.inventory.count(item.id, null) : 0;
}

async function craftIronPickaxe(bot) {
  if (countItem(bot, "iron_pickaxe") >= 1) return;
  const craftingTable = bot.findBlock({
    matching: mcData.blocksByName["crafting_table"].id,
    maxDistance: 32
  });
  if (!craftingTable) {
    throw new Error("Need a nearby crafting_table to craft an iron_pickaxe.");
  }
  if (countItem(bot, "stick") < 2) {
    if (countItem(bot, "oak_planks") < 2) {
      if (countItem(bot, "oak_log") < 1) {
        throw new Error("Need oak_log or planks to craft sticks.");
      }
      await craftItem(bot, "oak_planks", 1);
    }
    if (countItem(bot, "stick") < 2) {
      await craftItem(bot, "stick", 1);
    }
  }
  if (countItem(bot, "iron_pickaxe") >= 1) return;
  if (countItem(bot, "iron_ingot") < 3) {
    const missingIngots = 3 - countItem(bot, "iron_ingot");
    if (countItem(bot, "stone_pickaxe") < 1 && countItem(bot, "iron_pickaxe") < 1) {
      if (countItem(bot, "cobblestone") < 3) {
        await mineBlock(bot, "stone", 3 - countItem(bot, "cobblestone"));
      }
      if (countItem(bot, "stick") < 2) {
        await craftItem(bot, "stick", 1);
      }
      await craftItem(bot, "stone_pickaxe", 1);
    }
    await mineBlock(bot, "iron_ore", missingIngots);
    const furnace = bot.findBlock({
      matching: mcData.blocksByName["furnace"].id,
      maxDistance: 32
    });
    if (!furnace) {
      throw new Error("Need a nearby furnace to smelt raw_iron.");
    }
    if (countItem(bot, "oak_log") < missingIngots) {
      throw new Error("Need oak_log fuel to smelt raw_iron.");
    }
    await smeltItem(bot, "raw_iron", "oak_log", missingIngots);
  }
  if (countItem(bot, "iron_ingot") < 3) {
    throw new Error("Failed to obtain enough iron_ingot for iron_pickaxe.");
  }
  if (countItem(bot, "stick") < 2) {
    throw new Error("Failed to obtain enough sticks for iron_pickaxe.");
  }
  await craftItem(bot, "iron_pickaxe", 1);
  if (countItem(bot, "iron_pickaxe") < 1) {
    throw new Error("Failed to craft iron_pickaxe.");
  }
}