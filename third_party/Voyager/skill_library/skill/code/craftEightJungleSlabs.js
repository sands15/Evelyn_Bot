async function craftEightJungleSlabs(bot) {
  const slabItem = mcData.itemsByName["jungle_slab"];
  const plankItem = mcData.itemsByName["jungle_planks"];
  const logItem = mcData.itemsByName["jungle_log"];
  if (bot.inventory.count(slabItem.id, null) >= 8) {
    return;
  }
  const planksNeeded = 6;
  if (bot.inventory.count(plankItem.id, null) < planksNeeded) {
    const missingPlanks = planksNeeded - bot.inventory.count(plankItem.id, null);
    const logsNeeded = Math.ceil(missingPlanks / 4);
    if (bot.inventory.count(logItem.id, null) < logsNeeded) {
      await mineBlock(bot, "jungle_log", logsNeeded - bot.inventory.count(logItem.id, null));
    }
    if (bot.inventory.count(plankItem.id, null) < planksNeeded) {
      await craftItem(bot, "jungle_planks", logsNeeded);
    }
  }
  if (bot.inventory.count(slabItem.id, null) >= 8) {
    return;
  }
  if (bot.inventory.count(plankItem.id, null) < planksNeeded) {
    throw new Error("Not enough jungle_planks to craft 8 jungle_slab.");
  }
  await craftItem(bot, "jungle_slab", 2);
  if (bot.inventory.count(slabItem.id, null) < 8) {
    throw new Error("Failed to craft 8 jungle_slab.");
  }
}