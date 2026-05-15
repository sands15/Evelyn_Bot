function countItem(bot, name) {
  const item = mcData.itemsByName[name];
  return item ? bot.inventory.count(item.id, null) : 0;
}

async function obtainEightRawIron(bot) {
  if (countItem(bot, "raw_iron") >= 8) return;
  let pickaxe = bot.inventory.findInventoryItem(mcData.itemsByName["iron_pickaxe"]?.id) || bot.inventory.findInventoryItem(mcData.itemsByName["stone_pickaxe"]?.id) || bot.inventory.findInventoryItem(mcData.itemsByName["copper_pickaxe"]?.id);
  if (!pickaxe) {
    throw new Error("Need a stone, copper, or iron pickaxe to mine iron ore.");
  }
  await bot.equip(pickaxe, "hand");
  const mineNearbyIron = async () => {
    if (countItem(bot, "raw_iron") >= 8) return;
    let needed = 8 - countItem(bot, "raw_iron");
    const ironOres = bot.findBlocks({
      matching: block => block.name === "iron_ore",
      maxDistance: 32,
      count: needed
    });
    if (ironOres.length > 0) {
      await mineBlock(bot, "iron_ore", Math.min(ironOres.length, needed));
    }
    if (countItem(bot, "raw_iron") >= 8) return;
    needed = 8 - countItem(bot, "raw_iron");
    const deepslateIronOres = bot.findBlocks({
      matching: block => block.name === "deepslate_iron_ore",
      maxDistance: 32,
      count: needed
    });
    if (deepslateIronOres.length > 0) {
      await mineBlock(bot, "deepslate_iron_ore", Math.min(deepslateIronOres.length, needed));
    }
  };
  await mineNearbyIron();
  if (countItem(bot, "raw_iron") >= 8) return;
  for (let i = 0; i < 2; i++) {
    const foundOre = await exploreUntil(bot, new Vec3(0, -1, 0), 15, () => {
      return bot.findBlock({
        matching: block => block.name === "iron_ore" || block.name === "deepslate_iron_ore",
        maxDistance: 32
      });
    });
    if (!foundOre) continue;
    pickaxe = bot.inventory.findInventoryItem(mcData.itemsByName["iron_pickaxe"]?.id) || bot.inventory.findInventoryItem(mcData.itemsByName["stone_pickaxe"]?.id) || bot.inventory.findInventoryItem(mcData.itemsByName["copper_pickaxe"]?.id);
    if (!pickaxe) {
      throw new Error("Pickaxe missing before mining iron ore.");
    }
    await bot.equip(pickaxe, "hand");
    await mineNearbyIron();
    if (countItem(bot, "raw_iron") >= 8) return;
  }
  throw new Error("LOCAL_SEARCH_EXHAUSTED: iron ore was not nearby or this terrain is inefficient for finding raw_iron.");
}