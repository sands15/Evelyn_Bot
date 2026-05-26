async function mineFourDiamonds(bot) {
  function countItem(name) {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  }
  async function equipIronPickaxe() {
    const pickaxeItem = mcData.itemsByName["iron_pickaxe"];
    const pickaxe = pickaxeItem ? bot.inventory.findInventoryItem(pickaxeItem.id) : null;
    if (!pickaxe) throw new Error("Need an iron_pickaxe to mine diamond ore.");
    await bot.equip(pickaxe, "hand");
  }
  function findNearbyDiamondOres(limit) {
    const positions = bot.findBlocks({
      matching: block => block.name === "diamond_ore" || block.name === "deepslate_diamond_ore",
      maxDistance: 32,
      count: limit
    });
    return positions.map(pos => bot.blockAt(pos)).filter(block => block && (block.name === "diamond_ore" || block.name === "deepslate_diamond_ore")).sort((a, b) => a.position.distanceTo(bot.entity.position) - b.position.distanceTo(bot.entity.position));
  }
  async function mineReachableNearbyDiamonds(maxAttempts) {
    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      if (countItem("diamond") >= 4) return true;
      const before = countItem("diamond");
      const ores = findNearbyDiamondOres(8);
      if (ores.length === 0) return false;
      let triedOne = false;
      for (const ore of ores.slice(0, 4)) {
        if (countItem("diamond") >= 4) return true;
        try {
          await bot.pathfinder.goto(new GoalGetToBlock(ore.position.x, ore.position.y, ore.position.z));
          await equipIronPickaxe();
          await mineBlock(bot, ore.name, 1);
          triedOne = true;
        } catch (err) {
          await bot.waitForTicks(20);
          triedOne = true;
        }
        if (countItem("diamond") > before || countItem("diamond") >= 4) break;
      }
      if (!triedOne) return false;
    }
    return countItem("diamond") >= 4;
  }
  if (countItem("diamond") >= 4) return;
  await equipIronPickaxe();
  if (await mineReachableNearbyDiamonds(6)) return;
  const needed = 4 - countItem("diamond");
  const search = await searchForOre(bot, {
    goalType: "diamond",
    oreNames: ["diamond_ore", "deepslate_diamond_ore"],
    quantity: needed,
    maxSearchBudgetSec: 18
  });
  if (search && search.success === false) {
    throw new Error(search.reason || "LOCAL_SEARCH_EXHAUSTED: diamond ore was not reachable nearby.");
  }
  await equipIronPickaxe();
  if (await mineReachableNearbyDiamonds(8)) return;
  throw new Error("LOCAL_SEARCH_EXHAUSTED: could not collect 4 diamonds from reachable nearby diamond ore.");
}