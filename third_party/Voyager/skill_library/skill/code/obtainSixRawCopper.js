function countItem(bot, name) {
  const item = mcData.itemsByName[name];
  return item ? bot.inventory.count(item.id, null) : 0;
}

async function obtainSixRawCopper(bot) {
  const required = 6;
  if (countItem(bot, "raw_copper") >= required) return;
  if (countItem(bot, "stone_pickaxe") < 1) {
    if (countItem(bot, "cobblestone") < 3) {
      const woodenPickaxe = bot.inventory.findInventoryItem(mcData.itemsByName["wooden_pickaxe"].id);
      if (!woodenPickaxe) {
        throw new Error("Need a wooden_pickaxe to mine stone for a stone_pickaxe.");
      }
      await bot.equip(woodenPickaxe, "hand");
      await mineBlock(bot, "stone", 3 - countItem(bot, "cobblestone"));
    }
    if (countItem(bot, "stick") < 2) {
      throw new Error("Need 2 sticks to craft a stone_pickaxe.");
    }
    const table = bot.findBlock({
      matching: mcData.blocksByName["crafting_table"].id,
      maxDistance: 32
    });
    if (!table) {
      throw new Error("Need a nearby crafting_table to craft a stone_pickaxe.");
    }
    await craftItem(bot, "stone_pickaxe", 1);
  }
  if (countItem(bot, "raw_copper") >= required) return;
  const stonePickaxe = bot.inventory.findInventoryItem(mcData.itemsByName["stone_pickaxe"].id);
  if (!stonePickaxe) {
    throw new Error("Failed to obtain a stone_pickaxe.");
  }
  await bot.equip(stonePickaxe, "hand");
  let explored = false;
  for (let i = 0; i < required; i++) {
    if (countItem(bot, "raw_copper") >= required) return;
    let copperOre = bot.findBlock({
      matching: mcData.blocksByName["copper_ore"].id,
      maxDistance: 32
    });
    if (!copperOre && !explored) {
      explored = true;
      copperOre = await exploreUntil(bot, new Vec3(0, -1, 0), 60, () => {
        return bot.findBlock({
          matching: mcData.blocksByName["copper_ore"].id,
          maxDistance: 32
        });
      });
    }
    if (!copperOre) {
      throw new Error("Could not find copper_ore.");
    }
    await bot.equip(stonePickaxe, "hand");
    await mineBlock(bot, "copper_ore", 1);
  }
  if (countItem(bot, "raw_copper") < required) {
    throw new Error("Failed to obtain 6 raw_copper.");
  }
}