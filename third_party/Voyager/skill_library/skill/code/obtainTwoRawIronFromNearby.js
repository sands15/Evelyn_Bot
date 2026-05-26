async function obtainTwoRawIronFromNearby(bot) {
  const rawIron = mcData.itemsByName["raw_iron"];
  const stonePickaxe = mcData.itemsByName["stone_pickaxe"];
  const ironPickaxe = mcData.itemsByName["iron_pickaxe"];
  const copperPickaxe = mcData.itemsByName["copper_pickaxe"];
  function countItemByName(name) {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  }
  if (!rawIron) throw new Error("Missing mcData for raw_iron.");
  if (countItemByName("raw_iron") >= 2) return;

  // Prefer a pickaxe we can mine with
  const pickaxe = bot.inventory.findInventoryItem(stonePickaxe?.id) || bot.inventory.findInventoryItem(ironPickaxe?.id) || bot.inventory.findInventoryItem(copperPickaxe?.id);
  if (!pickaxe) throw new Error("Need a stone/copper/iron pickaxe to mine iron ore.");
  await bot.equip(pickaxe, "hand");
  let remaining = 2 - countItemByName("raw_iron");

  // First: mine any nearby visible iron
  const ironOreNearby = bot.findBlocks({
    matching: block => block.name === "iron_ore" || block.name === "deepslate_iron_ore",
    maxDistance: 32,
    count: remaining
  });
  if (ironOreNearby.length > 0) {
    // Mine in closest-first order for efficiency
    ironOreNearby.sort((a, b) => a.distanceTo(bot.entity.position) - b.distanceTo(bot.entity.position));
    for (let i = 0; i < Math.min(ironOreNearby.length, remaining); i++) {
      const oreBlock = bot.blockAt(ironOreNearby[i]);
      const oreName = oreBlock?.name;
      if (!oreName || oreName !== "iron_ore" && oreName !== "deepslate_iron_ore") continue;

      // Go near the block then mine one at a time
      await bot.pathfinder.goto(new GoalNear(oreBlock.position.x, oreBlock.position.y, oreBlock.position.z, 2));
      await mineBlock(bot, oreName, 1);
      remaining = 2 - countItemByName("raw_iron");
      if (remaining <= 0) return;
    }
  }

  // Second: one short, bounded intent search (do not over-wander)
  remaining = 2 - countItemByName("raw_iron");
  if (remaining <= 0) return;
  const searchResult = await searchForOre(bot, {
    oreName: "iron_ore",
    quantity: remaining,
    maxSearchBudgetSec: 14
  });
  if (!searchResult || searchResult.success === false) {
    throw new Error(searchResult?.reason || "LOCAL_SEARCH_EXHAUSTED: iron_ore was not nearby.");
  }

  // Mine what became reachable/visible after the search
  const ironOreNow = bot.findBlocks({
    matching: block => block.name === "iron_ore" || block.name === "deepslate_iron_ore",
    maxDistance: 32,
    count: remaining
  });
  if (ironOreNow.length === 0) {
    throw new Error("LOCAL_SEARCH_EXHAUSTED: search ended without nearby iron_ore.");
  }
  ironOreNow.sort((a, b) => a.distanceTo(bot.entity.position) - b.distanceTo(bot.entity.position));
  const toMine = Math.min(ironOreNow.length, remaining);
  for (let i = 0; i < toMine; i++) {
    const oreBlock = bot.blockAt(ironOreNow[i]);
    const oreName = oreBlock?.name;
    if (!oreName || oreName !== "iron_ore" && oreName !== "deepslate_iron_ore") continue;
    await bot.pathfinder.goto(new GoalNear(oreBlock.position.x, oreBlock.position.y, oreBlock.position.z, 2));
    await mineBlock(bot, oreName, 1);
    if (countItemByName("raw_iron") >= 2) return;
  }
  if (countItemByName("raw_iron") < 2) {
    throw new Error("FAILED_TO_OBTAIN_2_RAW_IRON: not enough iron ore reachable within limits.");
  }
}