async function mineEightLapisOre(bot) {
  function countItem(name) {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  }
  async function equipIronPickaxe() {
    const pickaxeItem = mcData.itemsByName["iron_pickaxe"];
    const pickaxe = bot.inventory.findInventoryItem(pickaxeItem.id);
    if (!pickaxe) throw new Error("NEED_IRON_PICKAXE");
    await bot.equip(pickaxe, "hand");
  }
  function findNearbyLapisBlocks(limit) {
    const positions = bot.findBlocks({
      matching: block => block.name === "lapis_ore" || block.name === "deepslate_lapis_ore",
      maxDistance: 32,
      count: limit
    });
    return positions.map(pos => bot.blockAt(pos)).filter(block => block && (block.name === "lapis_ore" || block.name === "deepslate_lapis_ore")).sort((a, b) => a.position.distanceTo(bot.entity.position) - b.position.distanceTo(bot.entity.position));
  }
  async function mineVisibleLapis(maxBlocks) {
    for (let i = 0; i < maxBlocks; i++) {
      if (countItem("lapis_lazuli") >= 8) return true;
      const lapisBlocks = findNearbyLapisBlocks(6);
      if (lapisBlocks.length === 0) return false;
      let minedOne = false;
      for (const lapis of lapisBlocks.slice(0, 3)) {
        if (countItem("lapis_lazuli") >= 8) return true;
        try {
          await bot.pathfinder.goto(new GoalGetToBlock(lapis.position.x, lapis.position.y, lapis.position.z));
          await equipIronPickaxe();
          await mineBlock(bot, lapis.name, 1);
          minedOne = true;
          break;
        } catch (err) {
          continue;
        }
      }
      if (!minedOne) return false;
    }
    return countItem("lapis_lazuli") >= 8;
  }
  if (countItem("lapis_lazuli") >= 8) return;
  await equipIronPickaxe();
  if (await mineVisibleLapis(4)) return;
  const search = await searchForOre(bot, {
    goalType: "lapis",
    oreNames: ["lapis_ore", "deepslate_lapis_ore"],
    maxSearchBudgetSec: 12
  });
  if (search && search.success === false) {
    throw new Error(search.reason || "LOCAL_SEARCH_EXHAUSTED: lapis ore was not reachable nearby.");
  }
  if (await mineVisibleLapis(4)) return;
  throw new Error("LOCAL_SEARCH_EXHAUSTED: nearby lapis ore could not be mined successfully.");
}