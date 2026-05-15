async function mineEightOakLogs(bot) {
  const oakLogItem = mcData.itemsByName["oak_log"];
  function oakLogCount() {
    return bot.inventory.count(oakLogItem.id, null);
  }
  if (oakLogCount() >= 8) return;
  let needed = 8 - oakLogCount();
  let nearbyOakLogs = bot.findBlocks({
    matching: block => block.name === "oak_log",
    maxDistance: 32,
    count: needed
  });
  if (nearbyOakLogs.length > 0) {
    await mineBlock(bot, "oak_log", Math.min(nearbyOakLogs.length, needed));
    if (oakLogCount() >= 8) return;
  }
  const foundOakLog = await exploreUntil(bot, new Vec3(1, 0, 1), 60, () => {
    return bot.findBlock({
      matching: block => block.name === "oak_log",
      maxDistance: 32
    });
  });
  if (!foundOakLog) {
    throw new Error("Could not find oak_log nearby or during surface exploration.");
  }
  needed = 8 - oakLogCount();
  if (needed > 0) {
    await mineBlock(bot, "oak_log", needed);
  }
  if (oakLogCount() < 8) {
    throw new Error("Failed to mine 8 oak_log.");
  }
}