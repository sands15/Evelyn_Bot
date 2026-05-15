async function craftOneIronAxeTask(bot) {
  const ironAxe = mcData.itemsByName["iron_axe"];
  const ironIngot = mcData.itemsByName["iron_ingot"];
  const rawIron = mcData.itemsByName["raw_iron"];
  const stick = mcData.itemsByName["stick"];
  const coal = mcData.itemsByName["coal"];
  if (bot.inventory.count(ironAxe.id, null) >= 1) return;
  if (bot.inventory.count(stick.id, null) < 2) {
    throw new Error("Need 2 sticks to craft an iron_axe.");
  }
  let missingIngots = 3 - bot.inventory.count(ironIngot.id, null);
  if (missingIngots > 0) {
    if (bot.inventory.count(rawIron.id, null) < missingIngots) {
      let oreBlock = bot.findBlock({
        matching: block => block.name === "iron_ore" || block.name === "deepslate_iron_ore",
        maxDistance: 32
      });
      if (!oreBlock) {
        oreBlock = await exploreUntil(bot, new Vec3(0, -1, 0), 20, () => {
          return bot.findBlock({
            matching: block => block.name === "iron_ore" || block.name === "deepslate_iron_ore",
            maxDistance: 32
          });
        });
      }
      if (!oreBlock) {
        throw new Error("LOCAL_SEARCH_EXHAUSTED: iron ore was not nearby or reachable in this terrain.");
      }
      await mineBlock(bot, oreBlock.name, missingIngots);
    }
    if (bot.inventory.count(coal.id, null) < missingIngots) {
      throw new Error("Need coal fuel to smelt raw_iron.");
    }
    const furnace = bot.findBlock({
      matching: mcData.blocksByName["furnace"].id,
      maxDistance: 32
    });
    if (!furnace) {
      throw new Error("Need a nearby furnace to smelt raw_iron.");
    }
    await smeltItem(bot, "raw_iron", "coal", missingIngots);
  }
  if (bot.inventory.count(ironIngot.id, null) < 3) {
    throw new Error("Failed to obtain 3 iron_ingot.");
  }
  const craftingTable = bot.findBlock({
    matching: mcData.blocksByName["crafting_table"].id,
    maxDistance: 32
  });
  if (!craftingTable) {
    throw new Error("Need a nearby crafting_table to craft an iron_axe.");
  }
  await craftItem(bot, "iron_axe", 1);
  if (bot.inventory.count(ironAxe.id, null) < 1) {
    throw new Error("Failed to craft iron_axe.");
  }
}