async function craftSixJunglePlanks(bot) {
  const junglePlanks = mcData.itemsByName["jungle_planks"];
  const jungleLog = mcData.itemsByName["jungle_log"];
  const plankCount = () => bot.inventory.count(junglePlanks.id, null);
  const logCount = () => bot.inventory.count(jungleLog.id, null);
  if (plankCount() >= 6) return;
  let logsNeeded = Math.ceil((6 - plankCount()) / 4);
  if (logCount() < logsNeeded) {
    const missingLogs = logsNeeded - logCount();
    const nearbyLogs = bot.findBlocks({
      matching: block => block.name === "jungle_log",
      maxDistance: 32,
      count: missingLogs
    });
    if (nearbyLogs.length > 0) {
      await mineBlock(bot, "jungle_log", Math.min(nearbyLogs.length, missingLogs));
    }
  }
  if (plankCount() >= 6) return;
  logsNeeded = Math.ceil((6 - plankCount()) / 4);
  if (logCount() < logsNeeded) {
    throw new Error("Not enough jungle_log to craft 6 jungle_planks.");
  }
  const craftingTable = bot.findBlock({
    matching: mcData.blocksByName["crafting_table"].id,
    maxDistance: 32
  });
  if (!craftingTable) {
    throw new Error("No crafting_table nearby to craft jungle_planks.");
  }
  await craftItem(bot, "jungle_planks", logsNeeded);
  if (plankCount() < 6) {
    throw new Error("Failed to craft 6 jungle_planks.");
  }
}