async function craftStonePickaxe(bot) {
  const stonePickaxe = mcData.itemsByName["stone_pickaxe"];
  const cobblestone = mcData.itemsByName["cobblestone"];
  const stick = mcData.itemsByName["stick"];
  if (bot.inventory.count(stonePickaxe.id, null) >= 1) return;
  if (bot.inventory.count(cobblestone.id, null) < 3) {
    throw new Error("Need 3 cobblestone to craft a stone_pickaxe.");
  }
  if (bot.inventory.count(stick.id, null) < 2) {
    throw new Error("Need 2 sticks to craft a stone_pickaxe.");
  }
  let craftingTable = bot.findBlock({
    matching: mcData.blocksByName["crafting_table"].id,
    maxDistance: 32
  });
  if (!craftingTable) {
    craftingTable = await exploreUntil(bot, new Vec3(1, 0, 1), 60, () => {
      return bot.findBlock({
        matching: mcData.blocksByName["crafting_table"].id,
        maxDistance: 32
      });
    });
  }
  if (!craftingTable) {
    throw new Error("Need a nearby crafting_table to craft a stone_pickaxe.");
  }
  await bot.pathfinder.goto(new GoalNear(craftingTable.position.x, craftingTable.position.y, craftingTable.position.z, 2));
  if (bot.inventory.count(stonePickaxe.id, null) >= 1) return;
  await craftItem(bot, "stone_pickaxe", 1);
  if (bot.inventory.count(stonePickaxe.id, null) < 1) {
    throw new Error("Failed to craft stone_pickaxe.");
  }
}