async function mineTwoIronOreNearby(bot) {
  // ---- Local helpers (keep within function; no reliance on previous rounds) ----
  function countItem(name) {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  }
  function findPickaxeForMiningIron() {
    const picks = ["iron_pickaxe", "stone_pickaxe", "copper_pickaxe", "diamond_pickaxe", "netherite_pickaxe"].map(n => mcData.itemsByName[n]).filter(Boolean);
    for (const p of picks) {
      const it = bot.inventory.findInventoryItem(p.id);
      if (it) return it;
    }
    return null;
  }

  // ---- Task goal check (we are mining blocks, so track via ore blocks mined by count of raw smelted not available here)
  // We'll stop when we have at least 2 additional ore-blocks mined or at least 2 more raw_iron gained is not guaranteed.
  // Since the task explicitly is "Mine 2 iron_ore", we stop after mining 2 iron_ore blocks.
  // ----

  const ironOreTargets = bot.findBlocks({
    matching: block => block.name === "iron_ore" || block.name === "deepslate_iron_ore",
    maxDistance: 32,
    count: 2
  });

  // If there are deepslate iron ores nearby, they still fulfill "iron ore" in practice; but task says iron_ore.
  // Prefer exact "iron_ore" first.
  let exactIronOre = bot.findBlocks({
    matching: block => block.name === "iron_ore",
    maxDistance: 32,
    count: 2
  });
  const pickaxe = findPickaxeForMiningIron();
  if (!pickaxe) throw new Error("Need a pickaxe to mine iron_ore.");
  await bot.equip(pickaxe, "hand");

  // Mine up to 2 exact "iron_ore" first; then fall back to deepslate_iron_ore only if needed.
  let mined = 0;
  async function mineFromList(list) {
    for (let i = 0; i < list.length && mined < 2; i++) {
      const pos = list[i];
      if (!pos) continue;
      const block = bot.blockAt(pos);
      if (!block) continue;

      // Re-check name because blocks may change while moving
      const name = block.name;
      if (name !== "iron_ore" && name !== "deepslate_iron_ore") continue;

      // Go close then mine exactly one
      await bot.pathfinder.goto(new GoalNear(block.position.x, block.position.y, block.position.z, 2));
      await mineBlock(bot, name, 1);
      mined += 1;

      // Minor re-check: if inventory has changed, still continue until mined == 2
      if (mined >= 2) return;
    }
  }
  if (exactIronOre.length > 0) {
    // Prefer those
    await mineFromList(exactIronOre);
  }
  if (mined < 2) {
    // Fallback: mine deepslate_iron_ore for remaining
    const remaining = 2 - mined;
    const fallback = bot.findBlocks({
      matching: block => block.name === "deepslate_iron_ore",
      maxDistance: 32,
      count: remaining
    });
    if (fallback.length === 0 && ironOreTargets.length > 0) {
      // last chance: if any matched targets include iron_ore/deepslate, try those
      await mineFromList(ironOreTargets.slice(0, remaining));
    } else {
      await mineFromList(fallback);
    }
  }
  if (mined < 2) {
    throw new Error("FAILED_TO_MINE_2_IRON_ORE: Not enough iron_ore blocks were nearby within 32 blocks.");
  }
}

async function task(bot) {
  return mineTwoIronOreNearby(bot);
}