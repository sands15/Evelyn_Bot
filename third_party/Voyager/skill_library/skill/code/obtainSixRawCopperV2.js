async function obtainSixRawCopper(bot) {
  const rawCopper = mcData.itemsByName["raw_copper"];
  const stonePickaxe = mcData.itemsByName["stone_pickaxe"];
  function rawCopperCount() {
    return bot.inventory.count(rawCopper.id, null);
  }
  if (rawCopperCount() >= 6) return;
  const pickaxe = bot.inventory.findInventoryItem(stonePickaxe.id);
  if (!pickaxe) {
    throw new Error("Need a stone_pickaxe to mine copper_ore.");
  }
  await bot.equip(pickaxe, "hand");
  let remaining = 6 - rawCopperCount();
  let nearbyCopper = bot.findBlocks({
    matching: block => block.name === "copper_ore",
    maxDistance: 32,
    count: remaining
  });
  if (nearbyCopper.length > 0) {
    await mineBlock(bot, "copper_ore", Math.min(nearbyCopper.length, remaining));
  }
  if (rawCopperCount() >= 6) return;
  const foundCopper = await exploreUntil(bot, new Vec3(0, -1, 0), 60, () => {
    return bot.findBlock({
      matching: mcData.blocksByName["copper_ore"].id,
      maxDistance: 32
    });
  });
  if (!foundCopper) {
    throw new Error("Could not find copper_ore.");
  }
  remaining = 6 - rawCopperCount();
  await bot.equip(pickaxe, "hand");
  await mineBlock(bot, "copper_ore", remaining);
  if (rawCopperCount() < 6) {
    throw new Error("Failed to obtain 6 raw_copper.");
  }
}