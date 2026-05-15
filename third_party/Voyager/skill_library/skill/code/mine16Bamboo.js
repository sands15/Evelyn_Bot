function countItem(bot, name) {
  const item = mcData.itemsByName[name];
  return item ? bot.inventory.count(item.id, null) : 0;
}

function findNearbyBamboo(bot) {
  return bot.findBlock({
    matching: mcData.blocksByName["bamboo"].id,
    maxDistance: 32
  });
}

async function mine16Bamboo(bot) {
  if (countItem(bot, "bamboo") >= 16) return;
  let remaining = 16 - countItem(bot, "bamboo");
  let bamboo = findNearbyBamboo(bot);
  if (bamboo) {
    await mineBlock(bot, "bamboo", remaining);
    if (countItem(bot, "bamboo") >= 16) return;
  }
  const directions = [new Vec3(1, 0, 1), new Vec3(-1, 0, 1)];
  for (const direction of directions) {
    remaining = 16 - countItem(bot, "bamboo");
    if (remaining <= 0) return;
    bamboo = await exploreUntil(bot, direction, 15, () => {
      return findNearbyBamboo(bot);
    });
    if (bamboo) {
      await mineBlock(bot, "bamboo", remaining);
      if (countItem(bot, "bamboo") >= 16) return;
    }
  }
  throw new Error("LOCAL_SEARCH_EXHAUSTED: bamboo was not found nearby after two short surface probes; current biome or terrain is inefficient.");
}