async function mineEightClay(bot) {
  const clayBallItem = mcData.itemsByName["clay_ball"];
  function clayBallCount() {
    return clayBallItem ? bot.inventory.count(clayBallItem.id, null) : 0;
  }
  async function mineNearbyClayUntilEnough() {
    for (let i = 0; i < 2; i++) {
      if (clayBallCount() >= 8) return true;
      const clay = bot.findBlock({
        matching: block => block.name === "clay",
        maxDistance: 32
      });
      if (!clay) return false;
      await mineBlock(bot, "clay", 1);
    }
    return clayBallCount() >= 8;
  }
  if (clayBallCount() >= 8) return;
  if (await mineNearbyClayUntilEnough()) return;
  const directions = [new Vec3(1, 0, 1), new Vec3(-1, 0, -1)];
  for (const direction of directions) {
    if (clayBallCount() >= 8) return;
    const foundClay = await exploreUntil(bot, direction, 15, () => {
      return bot.findBlock({
        matching: block => block.name === "clay",
        maxDistance: 32
      });
    });
    if (foundClay && (await mineNearbyClayUntilEnough())) return;
  }
  if (clayBallCount() < 8) {
    throw new Error("LOCAL_SEARCH_EXHAUSTED: clay was not nearby, and short shallow-water probes did not find enough clay.");
  }
}