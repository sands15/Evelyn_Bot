async function mineEightLapisOre(bot) {
  function countItem(name) {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  }
  async function equipBestPickaxe() {
    const names = ["netherite_pickaxe", "diamond_pickaxe", "iron_pickaxe", "stone_pickaxe"];
    for (const name of names) {
      const item = mcData.itemsByName[name];
      if (!item) continue;
      const pickaxe = bot.inventory.findInventoryItem(item.id);
      if (pickaxe) {
        await bot.equip(pickaxe, "hand");
        return;
      }
    }
    throw new Error("Need a pickaxe to mine lapis ore.");
  }
  async function mineNearbyLapis() {
    for (let i = 0; i < 8; i++) {
      if (countItem("lapis_lazuli") >= 8) return true;
      const lapis = bot.findBlock({
        matching: block => block.name === "lapis_ore" || block.name === "deepslate_lapis_ore",
        maxDistance: 32
      });
      if (!lapis) return false;
      await equipBestPickaxe();
      await mineBlock(bot, lapis.name, 1);
    }
    return countItem("lapis_lazuli") >= 8;
  }
  if (countItem("lapis_lazuli") >= 8) return;
  await equipBestPickaxe();
  if (await mineNearbyLapis()) return;
  const foundLapis = await exploreUntil(bot, new Vec3(0, -1, 0), 60, () => {
    return bot.findBlock({
      matching: block => block.name === "lapis_ore" || block.name === "deepslate_lapis_ore",
      maxDistance: 32
    });
  });
  if (!foundLapis) {
    throw new Error("Could not find lapis ore nearby or underground.");
  }
  await mineNearbyLapis();
  if (countItem("lapis_lazuli") < 8) {
    throw new Error("Failed to mine enough lapis.");
  }
}