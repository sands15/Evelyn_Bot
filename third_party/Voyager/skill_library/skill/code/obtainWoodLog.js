async function obtainWoodLog(bot) {
  const logNames = ["oak_log", "spruce_log", "birch_log", "jungle_log", "acacia_log", "dark_oak_log", "mangrove_log", "cherry_log"];
  function hasAnyLog() {
    return logNames.some(name => {
      const item = mcData.itemsByName[name];
      return item && bot.inventory.count(item.id, null) >= 1;
    });
  }
  function nearbyLogsByDistance() {
    const positions = bot.findBlocks({
      matching: block => logNames.includes(block.name),
      maxDistance: 32,
      count: 12
    });
    return positions.map(pos => bot.blockAt(pos)).filter(block => block && logNames.includes(block.name)).sort((a, b) => a.position.distanceTo(bot.entity.position) - b.position.distanceTo(bot.entity.position));
  }
  async function tryCollectNearbyLog() {
    const logs = nearbyLogsByDistance();
    const attempts = Math.min(logs.length, 4);
    for (let i = 0; i < attempts; i++) {
      if (hasAnyLog()) return true;
      const log = logs[i];
      try {
        await bot.pathfinder.goto(new GoalNear(log.position.x, log.position.y, log.position.z, 2));
        await mineBlock(bot, log.name, 1);
      } catch (err) {
        // Try a different nearby log candidate instead of repeating the same failed target.
      }
      if (hasAnyLog()) return true;
    }
    return hasAnyLog();
  }
  if (hasAnyLog()) return;
  if (await tryCollectNearbyLog()) return;
  await exploreUntil(bot, new Vec3(1, 0, 1), 15, () => {
    const logs = nearbyLogsByDistance();
    return logs.length > 0 ? logs[0] : null;
  });
  if (await tryCollectNearbyLog()) return;
  await exploreUntil(bot, new Vec3(-1, 0, -1), 15, () => {
    const logs = nearbyLogsByDistance();
    return logs.length > 0 ? logs[0] : null;
  });
  if (await tryCollectNearbyLog()) return;
  throw new Error("LOCAL_SEARCH_EXHAUSTED: no reachable wood log could be collected nearby after two short surface probes.");
}