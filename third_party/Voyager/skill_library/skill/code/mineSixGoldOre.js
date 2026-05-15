async function mineSixGoldOre(bot) {
  function countItem(name) {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  }
  async function equipIronPickaxe() {
    const pickaxe = bot.inventory.findInventoryItem(mcData.itemsByName["iron_pickaxe"].id);
    if (!pickaxe) {
      throw new Error("Need an iron_pickaxe to mine gold_ore.");
    }
    await bot.equip(pickaxe, "hand");
  }
  if (countItem("raw_gold") >= 6) return;
  await equipIronPickaxe();
  let needed = 6 - countItem("raw_gold");
  let nearbyGold = bot.findBlocks({
    matching: block => block.name === "gold_ore",
    maxDistance: 32,
    count: needed
  });
  if (nearbyGold.length > 0) {
    await mineBlock(bot, "gold_ore", Math.min(nearbyGold.length, needed));
    if (countItem("raw_gold") >= 6) return;
    await equipIronPickaxe();
  }
  needed = 6 - countItem("raw_gold");
  let nearbyDeepslateGold = bot.findBlocks({
    matching: block => block.name === "deepslate_gold_ore",
    maxDistance: 32,
    count: needed
  });
  if (nearbyDeepslateGold.length > 0) {
    await mineBlock(bot, "deepslate_gold_ore", Math.min(nearbyDeepslateGold.length, needed));
    if (countItem("raw_gold") >= 6) return;
    await equipIronPickaxe();
  }
  const foundGold = await exploreUntil(bot, new Vec3(0, -1, 0), 60, () => {
    return bot.findBlock({
      matching: block => block.name === "gold_ore" || block.name === "deepslate_gold_ore",
      maxDistance: 32
    });
  });
  if (!foundGold) {
    throw new Error("Could not find gold_ore.");
  }
  await equipIronPickaxe();
  needed = 6 - countItem("raw_gold");
  if (foundGold.name === "deepslate_gold_ore") {
    await mineBlock(bot, "deepslate_gold_ore", needed);
  } else {
    await mineBlock(bot, "gold_ore", needed);
  }
  if (countItem("raw_gold") < 6) {
    throw new Error("Failed to mine 6 gold_ore.");
  }
}