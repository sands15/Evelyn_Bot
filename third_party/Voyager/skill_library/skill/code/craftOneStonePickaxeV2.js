function countItem(bot, name) {
  const item = mcData.itemsByName[name];
  return item ? bot.inventory.count(item.id, null) : 0;
}

async function craftOneStonePickaxe(bot) {
  if (countItem(bot, "stone_pickaxe") >= 1) return;
  if (countItem(bot, "cobblestone") < 3) {
    throw new Error("Need 3 cobblestone to craft a stone_pickaxe.");
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
  await bot.pathfinder.goto(new GoalNear(table.position.x, table.position.y, table.position.z, 2));
  if (countItem(bot, "stone_pickaxe") >= 1) return;
  await craftItem(bot, "stone_pickaxe", 1);
  if (countItem(bot, "stone_pickaxe") < 1) {
    throw new Error("Failed to craft stone_pickaxe.");
  }
}