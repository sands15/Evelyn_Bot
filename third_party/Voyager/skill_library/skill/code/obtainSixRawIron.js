async function obtainSixRawIron(bot) {
  const rawIron = mcData.itemsByName["raw_iron"];
  function rawIronCount() {
    return bot.inventory.count(rawIron.id, null);
  }
  if (rawIronCount() >= 6) return;
  let pickaxe = bot.inventory.findInventoryItem(mcData.itemsByName["stone_pickaxe"].id) || bot.inventory.findInventoryItem(mcData.itemsByName["copper_pickaxe"]?.id) || bot.inventory.findInventoryItem(mcData.itemsByName["iron_pickaxe"]?.id);
  if (!pickaxe) {
    throw new Error("Need a stone, copper, or iron pickaxe to mine iron ore.");
  }
  await bot.equip(pickaxe, "hand");
  let needed = 6 - rawIronCount();
  let nearbyIron = bot.findBlocks({
    matching: block => block.name === "iron_ore",
    maxDistance: 32,
    count: needed
  });
  if (nearbyIron.length > 0) {
    await mineBlock(bot, "iron_ore", Math.min(nearbyIron.length, needed));
    if (rawIronCount() >= 6) return;
  }
  needed = 6 - rawIronCount();
  let nearbyDeepslateIron = bot.findBlocks({
    matching: block => block.name === "deepslate_iron_ore",
    maxDistance: 32,
    count: needed
  });
  if (nearbyDeepslateIron.length > 0) {
    await mineBlock(bot, "deepslate_iron_ore", Math.min(nearbyDeepslateIron.length, needed));
    if (rawIronCount() >= 6) return;
  }
  const foundIron = await exploreUntil(bot, new Vec3(0, -1, 0), 60, () => {
    return bot.findBlock({
      matching: block => block.name === "iron_ore" || block.name === "deepslate_iron_ore",
      maxDistance: 32
    });
  });
  if (!foundIron) {
    throw new Error("Could not find iron ore.");
  }
  pickaxe = bot.inventory.findInventoryItem(mcData.itemsByName["stone_pickaxe"].id) || bot.inventory.findInventoryItem(mcData.itemsByName["copper_pickaxe"]?.id) || bot.inventory.findInventoryItem(mcData.itemsByName["iron_pickaxe"]?.id);
  if (!pickaxe) {
    throw new Error("Pickaxe missing before mining iron ore.");
  }
  await bot.equip(pickaxe, "hand");
  needed = 6 - rawIronCount();
  if (foundIron.name === "deepslate_iron_ore") {
    await mineBlock(bot, "deepslate_iron_ore", needed);
  } else {
    await mineBlock(bot, "iron_ore", needed);
  }
  if (rawIronCount() < 6) {
    throw new Error("Failed to obtain 6 raw_iron.");
  }
}