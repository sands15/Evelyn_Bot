async function obtainSixRawCopper(bot) {
  const rawCopper = mcData.itemsByName["raw_copper"];
  if (bot.inventory.count(rawCopper.id, null) >= 6) return;
  const stonePickaxe = bot.inventory.findInventoryItem(mcData.itemsByName["stone_pickaxe"].id);
  const woodenPickaxe = bot.inventory.findInventoryItem(mcData.itemsByName["wooden_pickaxe"].id);
  const pickaxe = stonePickaxe || woodenPickaxe;
  if (!pickaxe) {
    throw new Error("Need a pickaxe to mine copper_ore.");
  }
  await bot.equip(pickaxe, "hand");
  let needed = 6 - bot.inventory.count(rawCopper.id, null);
  const nearbyCopper = bot.findBlocks({
    matching: block => block.name === "copper_ore",
    maxDistance: 32,
    count: needed
  });
  if (nearbyCopper.length > 0) {
    await mineBlock(bot, "copper_ore", Math.min(nearbyCopper.length, needed));
    if (bot.inventory.count(rawCopper.id, null) >= 6) return;
  }
  const foundCopper = await exploreUntil(bot, new Vec3(0, -1, 0), 60, () => {
    return bot.findBlock({
      matching: mcData.blocksByName["copper_ore"].id,
      maxDistance: 32
    });
  });
  if (!foundCopper) {
    throw new Error("Could not find copper_ore.");
  }
  await bot.equip(pickaxe, "hand");
  needed = 6 - bot.inventory.count(rawCopper.id, null);
  await mineBlock(bot, "copper_ore", needed);
  if (bot.inventory.count(rawCopper.id, null) < 6) {
    throw new Error("Failed to obtain 6 raw_copper.");
  }
}