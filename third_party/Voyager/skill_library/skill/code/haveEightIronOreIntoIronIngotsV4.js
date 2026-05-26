async function haveEightIronOreIntoIronIngots(bot) {
  if (!bot || typeof mcData === "undefined" || !mcData) {
    throw new Error("BOT_OR_MCDATA_MISSING");
  }
  const countItem = name => {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  };
  const targetIngots = 8;
  if (countItem("iron_ingot") >= targetIngots) {
    return {
      success: true
    };
  }
  throw new Error("NOT_ENOUGH_IRON_INGOTS");
}