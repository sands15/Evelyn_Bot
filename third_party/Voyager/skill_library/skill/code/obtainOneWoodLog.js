async function obtainOneWoodLog(bot) {
  const logNames = ["oak_log", "spruce_log", "birch_log", "jungle_log", "acacia_log", "dark_oak_log", "mangrove_log", "cherry_log"];
  function hasAnyLog() {
    return logNames.some(name => {
      const item = mcData.itemsByName[name];
      return item && bot.inventory.count(item.id, null) >= 1;
    });
  }
  function findNearbyLog() {
    return bot.findBlock({
      matching: block => logNames.includes(block.name),
      maxDistance: 32
    });
  }
  if (hasAnyLog()) return;
  let logBlock = findNearbyLog();
  if (!logBlock) {
    logBlock = await exploreUntil(bot, new Vec3(1, 0, 1), 15, () => {
      return findNearbyLog();
    });
  }
  if (!logBlock && !hasAnyLog()) {
    logBlock = await exploreUntil(bot, new Vec3(-1, 0, -1), 15, () => {
      return findNearbyLog();
    });
  }
  if (!logBlock && !hasAnyLog()) {
    throw new Error("LOCAL_SEARCH_EXHAUSTED: no wood log was found nearby after two short surface probes.");
  }
  if (hasAnyLog()) return;
  await mineBlock(bot, logBlock.name, 1);
  if (!hasAnyLog()) {
    throw new Error("Failed to obtain a wood log after mining.");
  }
}