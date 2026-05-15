async function mineFourJungleLogs(bot) {
  const required = 4;
  const jungleLog = mcData.itemsByName["jungle_log"];
  function jungleLogCount() {
    return bot.inventory.count(jungleLog.id, null);
  }
  if (jungleLogCount() >= required) return;
  const needed = required - jungleLogCount();
  const nearbyLogs = bot.findBlocks({
    matching: block => block.name === "jungle_log",
    maxDistance: 32,
    count: needed
  });
  if (nearbyLogs.length > 0) {
    await mineBlock(bot, "jungle_log", Math.min(nearbyLogs.length, needed));
  }
  if (jungleLogCount() >= required) return;
  throw new Error("LOCAL_SEARCH_EXHAUSTED: fewer than 4 jungle_log were available within 32 blocks.");
}