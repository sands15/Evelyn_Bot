function countItem(bot, name) {
  const item = mcData.itemsByName[name];
  return item ? bot.inventory.count(item.id, null) : 0;
}

async function craftOneIronShovel(bot) {
  if (countItem(bot, "iron_shovel") >= 1) return;
  if (countItem(bot, "stick") < 2) {
    if (countItem(bot, "oak_planks") >= 2) {
      await craftItem(bot, "stick", 1);
    } else {
      throw new Error("Need 2 sticks to craft an iron_shovel.");
    }
  }
  if (countItem(bot, "iron_shovel") >= 1) return;
  if (countItem(bot, "iron_ingot") < 1) {
    if (countItem(bot, "raw_iron") < 1) {
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
        throw new Error("LOCAL_SEARCH_EXHAUSTED: iron ore was not nearby in this terrain.");
      }
      const pickaxe = bot.inventory.findInventoryItem(mcData.itemsByName["iron_pickaxe"].id) || bot.inventory.findInventoryItem(mcData.itemsByName["stone_pickaxe"].id);
      if (!pickaxe) throw new Error("Need a pickaxe to mine iron ore.");
      await bot.equip(pickaxe, "hand");
      await mineBlock(bot, oreBlock.name, 1);
    }
    const furnace = bot.findBlock({
      matching: mcData.blocksByName["furnace"].id,
      maxDistance: 32
    });
    if (!furnace) throw new Error("Need a nearby furnace to smelt raw_iron.");
    if (countItem(bot, "coal") < 1) throw new Error("Need coal fuel to smelt raw_iron.");
    await smeltItem(bot, "raw_iron", "coal", 1);
  }
  if (countItem(bot, "iron_ingot") < 1) {
    throw new Error("Failed to obtain 1 iron_ingot.");
  }
  if (countItem(bot, "stick") < 2) {
    throw new Error("Failed to obtain 2 sticks.");
  }
  await craftItem(bot, "iron_shovel", 1);
  if (countItem(bot, "iron_shovel") < 1) {
    throw new Error("Failed to craft iron_shovel.");
  }
}