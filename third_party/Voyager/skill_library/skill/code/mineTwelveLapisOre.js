async function mineTwelveLapisOre(bot) {
  function countItem(name) {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  }
  async function equipIronPickaxe() {
    const pickaxe = bot.inventory.findInventoryItem(mcData.itemsByName["iron_pickaxe"].id);
    if (!pickaxe) {
      throw new Error("Need an iron_pickaxe to mine lapis ore.");
    }
    await bot.equip(pickaxe, "hand");
  }
  if (countItem("lapis_lazuli") >= 12) return;
  await equipIronPickaxe();
  for (let i = 0; i < 12; i++) {
    if (countItem("lapis_lazuli") >= 12) return;
    const lapis = bot.findBlock({
      matching: block => block.name === "lapis_ore" || block.name === "deepslate_lapis_ore",
      maxDistance: 32
    });
    if (!lapis) break;
    await equipIronPickaxe();
    await mineBlock(bot, lapis.name, 1);
  }
  if (countItem("lapis_lazuli") >= 12) return;
  const foundLapis = await exploreUntil(bot, new Vec3(0, -1, 0), 60, () => {
    return bot.findBlock({
      matching: block => block.name === "lapis_ore" || block.name === "deepslate_lapis_ore",
      maxDistance: 32
    });
  });
  if (!foundLapis) {
    throw new Error("Could not find lapis ore.");
  }
  for (let i = 0; i < 12; i++) {
    if (countItem("lapis_lazuli") >= 12) return;
    const lapis = bot.findBlock({
      matching: block => block.name === "lapis_ore" || block.name === "deepslate_lapis_ore",
      maxDistance: 32
    });
    if (!lapis) break;
    await equipIronPickaxe();
    await mineBlock(bot, lapis.name, 1);
  }
  if (countItem("lapis_lazuli") < 12) {
    throw new Error("Failed to mine enough lapis.");
  }
}