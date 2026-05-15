async function obtainSixRawCopper(bot) {
  const rawCopper = mcData.itemsByName["raw_copper"];
  const stonePickaxe = mcData.itemsByName["stone_pickaxe"];
  const woodenPickaxe = mcData.itemsByName["wooden_pickaxe"];
  function rawCopperCount() {
    return bot.inventory.count(rawCopper.id, null);
  }
  if (rawCopperCount() >= 6) return;
  let pickaxe = bot.inventory.findInventoryItem(stonePickaxe.id) || bot.inventory.findInventoryItem(woodenPickaxe.id);
  if (!pickaxe) {
    throw new Error("Need a pickaxe to mine copper_ore.");
  }
  await bot.equip(pickaxe, "hand");
  let needed = 6 - rawCopperCount();
  let nearbyCopper = bot.findBlocks({
    matching: block => block.name === "copper_ore",
    maxDistance: 32,
    count: needed
  });
  if (nearbyCopper.length > 0) {
    await mineBlock(bot, "copper_ore", Math.min(nearbyCopper.length, needed));
    if (rawCopperCount() >= 6) return;
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
  pickaxe = bot.inventory.findInventoryItem(stonePickaxe.id) || bot.inventory.findInventoryItem(woodenPickaxe.id);
  if (!pickaxe) {
    throw new Error("Pickaxe missing before mining copper_ore.");
  }
  await bot.equip(pickaxe, "hand");
  needed = 6 - rawCopperCount();
  await mineBlock(bot, "copper_ore", needed);
  if (rawCopperCount() < 6) {
    throw new Error("Failed to obtain 6 raw_copper.");
  }
}