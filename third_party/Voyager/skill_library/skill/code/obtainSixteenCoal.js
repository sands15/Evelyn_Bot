async function obtainSixteenCoal(bot) {
  function countItem(name) {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  }
  function findPickaxe() {
    const pickaxeNames = ["netherite_pickaxe", "diamond_pickaxe", "iron_pickaxe", "stone_pickaxe", "wooden_pickaxe"];
    for (const name of pickaxeNames) {
      const item = mcData.itemsByName[name];
      if (!item) continue;
      const pickaxe = bot.inventory.findInventoryItem(item.id);
      if (pickaxe) return pickaxe;
    }
    return null;
  }
  async function mineNearbyCoal() {
    if (countItem("coal") >= 16) return;
    const pickaxe = findPickaxe();
    if (!pickaxe) throw new Error("Need a pickaxe to mine coal_ore.");
    await bot.equip(pickaxe, "hand");
    let needed = 16 - countItem("coal");
    const coalOreBlocks = bot.findBlocks({
      matching: block => block.name === "coal_ore",
      maxDistance: 32,
      count: needed
    });
    if (coalOreBlocks.length > 0) {
      await mineBlock(bot, "coal_ore", Math.min(coalOreBlocks.length, needed));
      if (countItem("coal") >= 16) return;
    }
    needed = 16 - countItem("coal");
    const deepslateCoalOreBlocks = bot.findBlocks({
      matching: block => block.name === "deepslate_coal_ore",
      maxDistance: 32,
      count: needed
    });
    if (deepslateCoalOreBlocks.length > 0) {
      await mineBlock(bot, "deepslate_coal_ore", Math.min(deepslateCoalOreBlocks.length, needed));
    }
  }
  if (countItem("coal") >= 16) return;
  await mineNearbyCoal();
  if (countItem("coal") >= 16) return;
  const probeDirections = [new Vec3(1, 0, 1), new Vec3(-1, 0, -1)];
  for (const direction of probeDirections) {
    const foundCoal = await exploreUntil(bot, direction, 12, () => {
      return bot.findBlock({
        matching: block => block.name === "coal_ore" || block.name === "deepslate_coal_ore",
        maxDistance: 32
      });
    });
    if (foundCoal) {
      await mineNearbyCoal();
      if (countItem("coal") >= 16) return;
    }
  }
  throw new Error("LOCAL_SEARCH_EXHAUSTED: could not obtain 16 coal from nearby coal ore after two short local probes.");
}