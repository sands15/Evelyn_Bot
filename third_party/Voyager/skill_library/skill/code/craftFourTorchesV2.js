async function craftFourTorches(bot) {
  function count(name) {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  }
  function hasAny(names) {
    return names.some(name => count(name) > 0);
  }
  if (count("torch") >= 4) return;
  if (count("stick") < 1) {
    const plankNames = ["oak_planks", "birch_planks", "spruce_planks", "jungle_planks", "acacia_planks", "dark_oak_planks", "mangrove_planks", "cherry_planks"];
    const logToPlanks = [["oak_log", "oak_planks"], ["birch_log", "birch_planks"], ["spruce_log", "spruce_planks"], ["jungle_log", "jungle_planks"], ["acacia_log", "acacia_planks"], ["dark_oak_log", "dark_oak_planks"], ["mangrove_log", "mangrove_planks"], ["cherry_log", "cherry_planks"]];
    if (!hasAny(plankNames)) {
      let craftedPlanks = false;
      for (const [logName, plankName] of logToPlanks) {
        if (count(logName) > 0) {
          await craftItem(bot, plankName, 1);
          craftedPlanks = true;
          break;
        }
      }
      if (!craftedPlanks) throw new Error("Need planks or logs to craft sticks for torches.");
    }
    await craftItem(bot, "stick", 1);
  }
  if (count("coal") < 1) {
    throw new Error("Need at least 1 coal to craft 4 torches.");
  }
  const recipesNeeded = Math.ceil((4 - count("torch")) / 4);
  await craftItem(bot, "torch", recipesNeeded);
  if (count("torch") < 4) {
    throw new Error(`Failed to craft 4 torches; only have ${count("torch")}.`);
  }
}