async function obtainCoal4(bot) {
  const coalItem = mcData.itemsByName["coal"];
  if (!coalItem) throw new Error("MISSING mcData for coal item.");
  const stonePickaxe = mcData.itemsByName["stone_pickaxe"];
  const ironPickaxe = mcData.itemsByName["iron_pickaxe"];
  const copperPickaxe = mcData.itemsByName["copper_pickaxe"];
  const goldPickaxe = mcData.itemsByName["golden_pickaxe"];
  const pickaxeItem = bot.inventory.findInventoryItem(stonePickaxe?.id) || bot.inventory.findInventoryItem(ironPickaxe?.id) || bot.inventory.findInventoryItem(copperPickaxe?.id) || bot.inventory.findInventoryItem(goldPickaxe?.id);
  function countCoal() {
    return bot.inventory.count(coalItem.id, null);
  }
  const startCoal = countCoal();
  if (startCoal >= 4) {
    return {
      success: true,
      obtained: 0,
      coal: startCoal
    };
  }
  const remainingNeed = () => 4 - countCoal();

  // Ensure we have at least one pickaxe; if not, fail cleanly so higher-level planner can provide it.
  if (!pickaxeItem) throw new Error("Need a pickaxe to mine coal_ore.");

  // Prefer mining visible coal_ore only; keep movements short and bounded.
  await bot.equip(pickaxeItem, "hand");
  let rem = remainingNeed();
  const nearbyCoalOre = bot.findBlocks({
    matching: b => b.name === "coal_ore",
    maxDistance: 32,
    count: rem
  });
  if (nearbyCoalOre.length > 0) {
    nearbyCoalOre.sort((a, b) => a.distanceTo(bot.entity.position) - b.distanceTo(bot.entity.position));
    const toMine = Math.min(nearbyCoalOre.length, rem);
    for (let i = 0; i < toMine; i++) {
      if (remainingNeed() <= 0) break;
      const oreVec = nearbyCoalOre[i];
      const block = bot.blockAt(oreVec);
      if (!block || block.name !== "coal_ore") continue;

      // Use a small GoalNear radius; if pathfinding fails, try the next block.
      try {
        await bot.pathfinder.goto(new GoalNear(block.position.x, block.position.y, block.position.z, 2));
        await mineBlock(bot, "coal_ore", 1);
      } catch (e) {
        // Continue with next candidate; don't burn time.
      }
    }
  }
  const afterDirect = countCoal();
  if (afterDirect >= 4) {
    return {
      success: true,
      obtained: afterDirect - startCoal,
      coal: afterDirect
    };
  }

  // If still short, do one intent-level ore search to find additional coal_ore nearby.
  rem = remainingNeed();
  if (rem > 0) {
    const searchResult = await searchForOre(bot, {
      oreName: "coal_ore",
      quantity: rem,
      maxSearchBudgetSec: 14
    });
    if (!searchResult || searchResult.success === false) {
      throw new Error(searchResult?.reason || "LOCAL_SEARCH_EXHAUSTED: coal_ore not nearby.");
    }
    const coalOreNow = bot.findBlocks({
      matching: b => b.name === "coal_ore",
      maxDistance: 32,
      count: rem
    });
    if (coalOreNow.length === 0) {
      throw new Error("LOCAL_SEARCH_EXHAUSTED: search ended without nearby coal_ore.");
    }
    coalOreNow.sort((a, b) => a.distanceTo(bot.entity.position) - b.distanceTo(bot.entity.position));
    const toMine = Math.min(coalOreNow.length, rem);
    for (let i = 0; i < toMine; i++) {
      if (remainingNeed() <= 0) break;
      const oreVec = coalOreNow[i];
      const block = bot.blockAt(oreVec);
      if (!block || block.name !== "coal_ore") continue;
      try {
        await bot.pathfinder.goto(new GoalNear(block.position.x, block.position.y, block.position.z, 2));
        await mineBlock(bot, "coal_ore", 1);
      } catch (e) {
        // Try other ore blocks; keep it bounded.
      }
    }
  }
  const finalCoal = countCoal();
  if (finalCoal >= 4) {
    return {
      success: true,
      obtained: finalCoal - startCoal,
      coal: finalCoal
    };
  }
  throw new Error(`FAILED_TO_OBTAIN_COAL: have ${finalCoal}, need 4.`);
}