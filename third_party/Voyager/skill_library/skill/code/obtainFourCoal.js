async function obtainFourCoal(bot) {
  const coalItem = mcData.itemsByName["coal"];
  const pickaxeNames = ["netherite_pickaxe", "diamond_pickaxe", "iron_pickaxe", "stone_pickaxe", "wooden_pickaxe"];
  function coalCount() {
    return coalItem ? bot.inventory.count(coalItem.id, null) : 0;
  }
  function findPickaxe() {
    for (const name of pickaxeNames) {
      const item = mcData.itemsByName[name];
      if (!item) continue;
      const pickaxe = bot.inventory.findInventoryItem(item.id);
      if (pickaxe) return pickaxe;
    }
    return null;
  }
  function nearbyCoalOreCount(name, needed) {
    return bot.findBlocks({
      matching: block => block.name === name,
      maxDistance: 32,
      count: needed
    }).length;
  }
  async function mineNearbyCoalIfPresent() {
    if (coalCount() >= 4) return true;
    const pickaxe = findPickaxe();
    if (!pickaxe) {
      throw new Error("Need a pickaxe to mine coal_ore.");
    }
    await bot.equip(pickaxe, "hand");
    let needed = 4 - coalCount();
    const coalOreCount = nearbyCoalOreCount("coal_ore", needed);
    if (coalOreCount > 0) {
      await mineBlock(bot, "coal_ore", Math.min(coalOreCount, needed));
      if (coalCount() >= 4) return true;
    }
    needed = 4 - coalCount();
    const deepslateCoalOreCount = nearbyCoalOreCount("deepslate_coal_ore", needed);
    if (deepslateCoalOreCount > 0) {
      await mineBlock(bot, "deepslate_coal_ore", Math.min(deepslateCoalOreCount, needed));
    }
    return coalCount() >= 4;
  }
  if (coalCount() >= 4) return;
  if (await mineNearbyCoalIfPresent()) return;
  const firstFound = await exploreUntil(bot, new Vec3(1, 0, 1), 15, () => {
    return bot.findBlock({
      matching: block => block.name === "coal_ore" || block.name === "deepslate_coal_ore",
      maxDistance: 32
    });
  });
  if (firstFound && (await mineNearbyCoalIfPresent())) return;
  const secondFound = await exploreUntil(bot, new Vec3(-1, 0, -1), 15, () => {
    return bot.findBlock({
      matching: block => block.name === "coal_ore" || block.name === "deepslate_coal_ore",
      maxDistance: 32
    });
  });
  if (secondFound && (await mineNearbyCoalIfPresent())) return;
  if (coalCount() < 4) {
    throw new Error("LOCAL_SEARCH_EXHAUSTED: could not obtain 4 coal from nearby coal ore after two short probes.");
  }
}