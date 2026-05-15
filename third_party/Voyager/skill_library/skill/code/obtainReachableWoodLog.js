async function obtainReachableWoodLog(bot) {
  const logNames = ["oak_log", "spruce_log", "birch_log", "jungle_log", "acacia_log", "dark_oak_log", "mangrove_log", "cherry_log"];
  function hasAnyLog() {
    return logNames.some(name => {
      const item = mcData.itemsByName[name];
      return item && bot.inventory.count(item.id, null) >= 1;
    });
  }
  function findNearbyLogBlocks() {
    const positions = bot.findBlocks({
      matching: block => logNames.includes(block.name),
      maxDistance: 32,
      count: 8
    });
    return positions.map(pos => bot.blockAt(pos)).filter(block => block && logNames.includes(block.name)).sort((a, b) => {
      return a.position.distanceTo(bot.entity.position) - b.position.distanceTo(bot.entity.position);
    });
  }
  if (hasAnyLog()) return;
  let logs = findNearbyLogBlocks();
  if (logs.length === 0) {
    await exploreUntil(bot, new Vec3(1, 0, 1), 15, () => {
      const found = findNearbyLogBlocks();
      return found.length > 0 ? found[0] : null;
    });
    if (hasAnyLog()) return;
    logs = findNearbyLogBlocks();
  }
  if (logs.length === 0) {
    await exploreUntil(bot, new Vec3(-1, 0, -1), 15, () => {
      const found = findNearbyLogBlocks();
      return found.length > 0 ? found[0] : null;
    });
    if (hasAnyLog()) return;
    logs = findNearbyLogBlocks();
  }
  if (logs.length === 0) {
    throw new Error("LOCAL_SEARCH_EXHAUSTED: no wood log was found nearby after two short surface probes.");
  }
  const attempts = Math.min(logs.length, 4);
  let lastError = null;
  for (let i = 0; i < attempts; i++) {
    if (hasAnyLog()) return;
    try {
      await mineBlock(bot, logs[i].name, 1);
    } catch (err) {
      lastError = err;
    }
  }
  if (!hasAnyLog()) {
    throw new Error(`LOCAL_SEARCH_EXHAUSTED: nearby wood logs were found but could not be collected locally. ${lastError ? lastError.message : ""}`);
  }
}