async function craftEightSticks(bot) {
  function countItem(name) {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  }
  if (countItem("stick") >= 8) return;
  const plankNames = ["oak_planks", "spruce_planks", "birch_planks", "jungle_planks", "acacia_planks", "dark_oak_planks", "mangrove_planks", "cherry_planks", "bamboo_planks", "crimson_planks", "warped_planks"];
  const sticksNeeded = 8 - countItem("stick");
  const craftsNeeded = Math.ceil(sticksNeeded / 4);
  let chosenPlank = null;
  for (const plankName of plankNames) {
    if (countItem(plankName) >= craftsNeeded * 2) {
      chosenPlank = plankName;
      break;
    }
  }
  if (!chosenPlank) {
    throw new Error("Need wooden planks to craft 8 sticks.");
  }
  await craftItem(bot, "stick", craftsNeeded);
  if (countItem("stick") < 8) {
    throw new Error("Failed to craft 8 sticks.");
  }
}