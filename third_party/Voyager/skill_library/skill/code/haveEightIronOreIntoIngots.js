async function haveEightIronOreIntoIngots(bot) {
  if (!bot || typeof mcData === "undefined" || !mcData) {
    throw new Error("BOT_OR_MCDATA_MISSING");
  }
  const ironIngot = mcData.itemsByName.iron_ingot;
  const ingotCount = ironIngot ? bot.inventory.count(ironIngot.id, null) : 0;
  if (ingotCount >= 8) {
    return {
      success: true
    };
  }
  throw new Error("NOT_ENOUGH_IRON_INGOTS");
}