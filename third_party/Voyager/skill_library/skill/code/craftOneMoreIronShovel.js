async function craftOneMoreIronShovel(bot) {
  function count(name) {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  }
  const startingShovels = count("iron_shovel");
  const targetShovels = startingShovels + 1;
  if (count("iron_shovel") >= targetShovels) return;
  if (count("stick") < 2) {
    if (count("oak_planks") < 2 && count("birch_planks") < 2) {
      if (count("oak_log") > 0) {
        await craftItem(bot, "oak_planks", 1);
      } else if (count("birch_log") > 0) {
        await craftItem(bot, "birch_planks", 1);
      } else {
        throw new Error("Need planks or logs to craft sticks for an iron_shovel.");
      }
    }
    await craftItem(bot, "stick", 1);
  }
  if (count("iron_shovel") >= targetShovels) return;
  if (count("iron_ingot") < 1) {
    if (count("raw_iron") < 1) {
      throw new Error("Need 1 iron_ingot or 1 raw_iron to craft an iron_shovel.");
    }
    if (count("coal") < 1) {
      throw new Error("Need coal fuel to smelt raw_iron.");
    }
    const furnace = bot.findBlock({
      matching: mcData.blocksByName["furnace"].id,
      maxDistance: 32
    });
    if (!furnace) {
      throw new Error("Need a nearby furnace to smelt raw_iron.");
    }
    await smeltItem(bot, "raw_iron", "coal", 1);
  }
  if (count("stick") < 2) throw new Error("Need 2 sticks to craft an iron_shovel.");
  if (count("iron_ingot") < 1) throw new Error("Need 1 iron_ingot to craft an iron_shovel.");
  await craftItem(bot, "iron_shovel", 1);
  if (count("iron_shovel") < targetShovels) {
    throw new Error("Failed to craft 1 additional iron_shovel.");
  }
}