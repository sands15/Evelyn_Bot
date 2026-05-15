function countItem(bot, name) {
  const item = mcData.itemsByName[name];
  return item ? bot.inventory.count(item.id, null) : 0;
}

function findIronCapablePickaxe(bot) {
  return bot.inventory.findInventoryItem(mcData.itemsByName["iron_pickaxe"]?.id) || bot.inventory.findInventoryItem(mcData.itemsByName["stone_pickaxe"]?.id) || bot.inventory.findInventoryItem(mcData.itemsByName["copper_pickaxe"]?.id);
}

async function mineNearbyIronOneAtATime(bot, targetCount) {
  for (let i = 0; i < targetCount; i++) {
    if (countItem(bot, "raw_iron") >= 8) return true;
    const ore = bot.findBlock({
      matching: block => block.name === "iron_ore" || block.name === "deepslate_iron_ore",
      maxDistance: 32
    });
    if (!ore) return false;
    const pickaxe = findIronCapablePickaxe(bot);
    if (!pickaxe) {
      throw new Error("Need a stone, copper, or iron pickaxe to mine iron ore.");
    }
    await bot.equip(pickaxe, "hand");
    await bot.pathfinder.goto(new GoalNear(ore.position.x, ore.position.y, ore.position.z, 2));
    await mineBlock(bot, ore.name, 1);
  }
  return countItem(bot, "raw_iron") >= 8;
}

async function obtainEightRawIron(bot) {
  if (countItem(bot, "raw_iron") >= 8) return;
  const pickaxe = findIronCapablePickaxe(bot);
  if (!pickaxe) {
    throw new Error("Need a stone, copper, or iron pickaxe to mine iron ore.");
  }
  await bot.equip(pickaxe, "hand");
  let needed = 8 - countItem(bot, "raw_iron");
  if (await mineNearbyIronOneAtATime(bot, needed)) return;
  for (let i = 0; i < 2; i++) {
    const foundOre = await exploreUntil(bot, new Vec3(0, -1, 0), 15, () => {
      return bot.findBlock({
        matching: block => block.name === "iron_ore" || block.name === "deepslate_iron_ore",
        maxDistance: 32
      });
    });
    if (!foundOre) continue;
    needed = 8 - countItem(bot, "raw_iron");
    if (await mineNearbyIronOneAtATime(bot, needed)) return;
  }
  throw new Error("LOCAL_SEARCH_EXHAUSTED: reachable iron ore was not nearby or this terrain is inefficient for finding raw_iron.");
}