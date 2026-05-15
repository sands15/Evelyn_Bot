async function obtainEightRawCopper(bot) {
  const rawCopper = mcData.itemsByName["raw_copper"];
  const rawCopperCount = () => bot.inventory.count(rawCopper.id, null);
  if (rawCopperCount() >= 8) return;
  const pickaxe = bot.inventory.findInventoryItem(mcData.itemsByName["iron_pickaxe"]?.id) || bot.inventory.findInventoryItem(mcData.itemsByName["stone_pickaxe"]?.id) || bot.inventory.findInventoryItem(mcData.itemsByName["copper_pickaxe"]?.id) || bot.inventory.findInventoryItem(mcData.itemsByName["wooden_pickaxe"]?.id);
  if (!pickaxe) {
    throw new Error("Need a pickaxe to mine copper_ore.");
  }
  await bot.equip(pickaxe, "hand");
  let needed = 8 - rawCopperCount();
  let nearbyCopper = bot.findBlocks({
    matching: block => block.name === "copper_ore",
    maxDistance: 32,
    count: needed
  });
  if (nearbyCopper.length === 0) {
    const foundCopper = await exploreUntil(bot, new Vec3(0, -1, 0), 60, () => {
      return bot.findBlock({
        matching: mcData.blocksByName["copper_ore"].id,
        maxDistance: 32
      });
    });
    if (!foundCopper) {
      throw new Error("Could not find copper_ore.");
    }
  }
  needed = 8 - rawCopperCount();
  if (needed > 0) {
    await mineBlock(bot, "copper_ore", needed);
  }
  if (rawCopperCount() < 8) {
    throw new Error("Failed to obtain 8 raw_copper.");
  }
}