async function craftEightJunglePlanks(bot) {
  const junglePlanks = mcData.itemsByName["jungle_planks"];
  const jungleLog = mcData.itemsByName["jungle_log"];
  const plankCount = () => bot.inventory.count(junglePlanks.id, null);
  const logCount = () => bot.inventory.count(jungleLog.id, null);
  if (plankCount() >= 8) return;
  const logsNeeded = Math.ceil((8 - plankCount()) / 4);
  if (logCount() < logsNeeded) {
    await mineBlock(bot, "jungle_log", logsNeeded - logCount());
  }
  if (plankCount() >= 8) return;
  const remainingLogsNeeded = Math.ceil((8 - plankCount()) / 4);
  if (logCount() < remainingLogsNeeded) {
    throw new Error("Not enough jungle_log to craft 8 jungle_planks.");
  }
  const craftingTable = bot.findBlock({
    matching: mcData.blocksByName["crafting_table"].id,
    maxDistance: 32
  });
  if (!craftingTable) {
    throw new Error("No crafting_table nearby to craft jungle_planks.");
  }
  await craftItem(bot, "jungle_planks", remainingLogsNeeded);
  if (plankCount() < 8) {
    throw new Error("Failed to craft 8 jungle_planks.");
  }
}