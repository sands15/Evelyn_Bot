async function craftOneIronAxe(bot) {
  const ironAxe = mcData.itemsByName["iron_axe"];
  const ironIngot = mcData.itemsByName["iron_ingot"];
  const stick = mcData.itemsByName["stick"];
  if (bot.inventory.count(ironAxe.id, null) >= 1) return;
  if (bot.inventory.count(ironIngot.id, null) < 3) {
    throw new Error("Need 3 iron_ingot to craft an iron_axe.");
  }
  if (bot.inventory.count(stick.id, null) < 2) {
    const oakPlanks = mcData.itemsByName["oak_planks"];
    const birchPlanks = mcData.itemsByName["birch_planks"];
    const oakLog = mcData.itemsByName["oak_log"];
    const birchLog = mcData.itemsByName["birch_log"];
    if (bot.inventory.count(oakPlanks.id, null) + bot.inventory.count(birchPlanks.id, null) < 2) {
      if (bot.inventory.count(oakLog.id, null) >= 1) {
        await craftItem(bot, "oak_planks", 1);
      } else if (bot.inventory.count(birchLog.id, null) >= 1) {
        await craftItem(bot, "birch_planks", 1);
      } else {
        throw new Error("Need planks or logs to craft sticks for an iron_axe.");
      }
    }
    await craftItem(bot, "stick", 1);
  }
  if (bot.inventory.count(stick.id, null) < 2) {
    throw new Error("Failed to obtain 2 sticks for an iron_axe.");
  }
  const craftingTable = bot.findBlock({
    matching: mcData.blocksByName["crafting_table"].id,
    maxDistance: 32
  });
  if (!craftingTable) {
    throw new Error("Need a nearby crafting_table to craft an iron_axe.");
  }
  await craftItem(bot, "iron_axe", 1);
  if (bot.inventory.count(ironAxe.id, null) < 1) {
    throw new Error("Failed to craft iron_axe.");
  }
}