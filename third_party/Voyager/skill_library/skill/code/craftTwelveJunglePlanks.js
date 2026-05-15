async function craftTwelveJunglePlanks(bot) {
  const junglePlanks = mcData.itemsByName["jungle_planks"];
  const jungleLog = mcData.itemsByName["jungle_log"];
  const plankCount = () => bot.inventory.count(junglePlanks.id, null);
  const logCount = () => bot.inventory.count(jungleLog.id, null);
  if (plankCount() >= 12) return;
  let craftsNeeded = Math.ceil((12 - plankCount()) / 4);
  if (logCount() < craftsNeeded) {
    const missingLogs = craftsNeeded - logCount();
    const nearbyLogs = bot.findBlocks({
      matching: block => block.name === "jungle_log",
      maxDistance: 32,
      count: missingLogs
    });
    if (nearbyLogs.length < missingLogs) {
      throw new Error("Not enough nearby jungle_log to craft 12 jungle_planks.");
    }
    await mineBlock(bot, "jungle_log", missingLogs);
  }
  if (plankCount() >= 12) return;
  craftsNeeded = Math.ceil((12 - plankCount()) / 4);
  if (logCount() < craftsNeeded) {
    throw new Error("Not enough jungle_log to craft 12 jungle_planks.");
  }
  await craftItem(bot, "jungle_planks", craftsNeeded);
  if (plankCount() < 12) {
    throw new Error("Failed to craft 12 jungle_planks.");
  }
}