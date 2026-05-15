async function obtainSixRawCopper(bot) {
  const rawCopperItem = mcData.itemsByName["raw_copper"];
  const rawCopperCount = () => bot.inventory.count(rawCopperItem.id, null);
  if (rawCopperCount() >= 6) return;
  let pickaxe = bot.inventory.findInventoryItem(mcData.itemsByName["stone_pickaxe"]?.id) || bot.inventory.findInventoryItem(mcData.itemsByName["iron_pickaxe"]?.id) || bot.inventory.findInventoryItem(mcData.itemsByName["copper_pickaxe"]?.id) || bot.inventory.findInventoryItem(mcData.itemsByName["wooden_pickaxe"]?.id);
  if (!pickaxe) {
    throw new Error("Need a pickaxe to mine copper_ore.");
  }
  await bot.equip(pickaxe, "hand");
  let needed = 6 - rawCopperCount();
  let nearbyCopper = bot.findBlocks({
    matching: block => block.name === "copper_ore" || block.name === "deepslate_copper_ore",
    maxDistance: 32,
    count: needed
  });
  if (nearbyCopper.length > 0) {
    for (const pos of nearbyCopper.slice(0, needed)) {
      if (rawCopperCount() >= 6) return;
      const block = bot.blockAt(pos);
      if (block) {
        await mineBlock(bot, block.name, 1);
      }
    }
  }
  if (rawCopperCount() >= 6) return;
  const foundCopper = await exploreUntil(bot, new Vec3(0, -1, 0), 60, () => {
    return bot.findBlock({
      matching: block => block.name === "copper_ore" || block.name === "deepslate_copper_ore",
      maxDistance: 32
    });
  });
  if (!foundCopper) {
    throw new Error("Could not find copper_ore.");
  }
  pickaxe = bot.inventory.findInventoryItem(mcData.itemsByName["stone_pickaxe"]?.id) || bot.inventory.findInventoryItem(mcData.itemsByName["iron_pickaxe"]?.id) || bot.inventory.findInventoryItem(mcData.itemsByName["copper_pickaxe"]?.id) || bot.inventory.findInventoryItem(mcData.itemsByName["wooden_pickaxe"]?.id);
  if (!pickaxe) {
    throw new Error("Pickaxe missing before mining copper_ore.");
  }
  await bot.equip(pickaxe, "hand");
  needed = 6 - rawCopperCount();
  await mineBlock(bot, foundCopper.name, needed);
  if (rawCopperCount() < 6) {
    throw new Error("Failed to obtain 6 raw_copper.");
  }
}