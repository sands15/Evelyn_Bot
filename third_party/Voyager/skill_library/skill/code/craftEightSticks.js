async function craftEightSticks(bot) {
  const stick = mcData.itemsByName["stick"];
  const junglePlanks = mcData.itemsByName["jungle_planks"];
  const jungleLog = mcData.itemsByName["jungle_log"];
  const stickCount = () => bot.inventory.count(stick.id, null);
  const plankCount = () => bot.inventory.count(junglePlanks.id, null);
  const logCount = () => bot.inventory.count(jungleLog.id, null);
  if (stickCount() >= 8) return;
  const sticksNeeded = 8 - stickCount();
  const stickCraftsNeeded = Math.ceil(sticksNeeded / 4);
  const planksNeeded = stickCraftsNeeded * 2;
  if (plankCount() < planksNeeded) {
    const logsToCraft = Math.ceil((planksNeeded - plankCount()) / 4);
    if (logCount() < logsToCraft) {
      throw new Error("Not enough jungle_log to craft 8 sticks.");
    }
    await craftItem(bot, "jungle_planks", logsToCraft);
  }
  if (stickCount() >= 8) return;
  const remainingCrafts = Math.ceil((8 - stickCount()) / 4);
  if (plankCount() < remainingCrafts * 2) {
    throw new Error("Not enough jungle_planks to craft 8 sticks.");
  }
  await craftItem(bot, "stick", remainingCrafts);
  if (stickCount() < 8) {
    throw new Error("Failed to craft 8 sticks.");
  }
}