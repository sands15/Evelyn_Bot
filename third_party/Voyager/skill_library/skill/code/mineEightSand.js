async function mineEightSand(bot) {
  const sandItem = mcData.itemsByName["sand"];
  function sandCount() {
    return bot.inventory.count(sandItem.id, null);
  }
  async function mineNearbyNeededSand() {
    const needed = 8 - sandCount();
    if (needed <= 0) return true;
    const sandBlocks = bot.findBlocks({
      matching: block => block.name === "sand",
      maxDistance: 32,
      count: needed
    });
    if (sandBlocks.length === 0) return false;
    await mineBlock(bot, "sand", Math.min(needed, sandBlocks.length));
    return sandCount() >= 8;
  }
  if (sandCount() >= 8) return;
  if (await mineNearbyNeededSand()) return;
  const directions = [new Vec3(1, 0, 1), new Vec3(-1, 0, -1)];
  for (const direction of directions) {
    if (sandCount() >= 8) return;
    const foundSand = await exploreUntil(bot, direction, 15, () => {
      return bot.findBlock({
        matching: block => block.name === "sand",
        maxDistance: 32
      });
    });
    if (foundSand && (await mineNearbyNeededSand())) return;
  }
  if (sandCount() < 8) {
    throw new Error("LOCAL_SEARCH_EXHAUSTED: sand was not nearby and short surface probes did not find enough sand.");
  }
}