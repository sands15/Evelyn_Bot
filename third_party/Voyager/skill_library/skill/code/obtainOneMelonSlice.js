function countItem(bot, name) {
  const item = mcData.itemsByName[name];
  return item ? bot.inventory.count(item.id, null) : 0;
}

async function obtainOneMelonSlice(bot) {
  if (countItem(bot, "melon_slice") >= 1) return;
  let melonBlock = bot.findBlock({
    matching: mcData.blocksByName["melon"].id,
    maxDistance: 32
  });
  if (!melonBlock) {
    melonBlock = await exploreUntil(bot, new Vec3(1, 0, 1), 60, () => {
      return bot.findBlock({
        matching: mcData.blocksByName["melon"].id,
        maxDistance: 32
      });
    });
  }
  if (countItem(bot, "melon_slice") >= 1) return;
  if (!melonBlock) {
    melonBlock = await exploreUntil(bot, new Vec3(-1, 0, 1), 60, () => {
      return bot.findBlock({
        matching: mcData.blocksByName["melon"].id,
        maxDistance: 32
      });
    });
  }
  if (countItem(bot, "melon_slice") >= 1) return;
  melonBlock = bot.findBlock({
    matching: mcData.blocksByName["melon"].id,
    maxDistance: 32
  });
  if (!melonBlock) {
    throw new Error("Could not find a melon block after two surface jungle searches.");
  }
  await mineBlock(bot, "melon", 1);
  if (countItem(bot, "melon_slice") < 1) {
    throw new Error("Failed to obtain 1 melon_slice from the melon block.");
  }
}