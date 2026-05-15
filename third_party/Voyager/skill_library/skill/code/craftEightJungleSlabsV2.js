function countItem(bot, name) {
  const item = mcData.itemsByName[name];
  return item ? bot.inventory.count(item.id, null) : 0;
}

function findNearbyPlacePosition(bot) {
  const base = bot.entity.position.floored();
  const offsets = [new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1), new Vec3(1, 0, 1), new Vec3(-1, 0, -1), new Vec3(1, 0, -1), new Vec3(-1, 0, 1)];
  for (const offset of offsets) {
    const pos = base.plus(offset);
    const block = bot.blockAt(pos);
    const below = bot.blockAt(pos.offset(0, -1, 0));
    if (block && below && block.name === "air" && below.name !== "air") {
      return pos;
    }
  }
  return null;
}

async function craftEightJungleSlabs(bot) {
  if (countItem(bot, "jungle_slab") >= 8) return;
  if (countItem(bot, "jungle_planks") < 6) {
    const missingPlanks = 6 - countItem(bot, "jungle_planks");
    const logsNeeded = Math.ceil(missingPlanks / 4);
    if (countItem(bot, "jungle_log") < logsNeeded) {
      await mineBlock(bot, "jungle_log", logsNeeded - countItem(bot, "jungle_log"));
    }
    const plankItem = mcData.itemsByName["jungle_planks"];
    const recipe = bot.recipesFor(plankItem.id, null, 1, null)[0];
    if (!recipe) throw new Error("No inventory recipe found for jungle_planks.");
    await bot.craft(recipe, logsNeeded, null);
  }
  if (countItem(bot, "jungle_slab") >= 8) return;
  let craftingTable = bot.findBlock({
    matching: mcData.blocksByName["crafting_table"].id,
    maxDistance: 32
  });
  if (!craftingTable) {
    if (countItem(bot, "crafting_table") < 1) {
      if (countItem(bot, "jungle_planks") < 4) {
        throw new Error("Need 4 jungle_planks to craft a crafting_table.");
      }
      const tableItem = mcData.itemsByName["crafting_table"];
      const tableRecipe = bot.recipesFor(tableItem.id, null, 1, null)[0];
      if (!tableRecipe) throw new Error("No inventory recipe found for crafting_table.");
      await bot.craft(tableRecipe, 1, null);
    }
    const placePos = findNearbyPlacePosition(bot);
    if (!placePos) throw new Error("No valid nearby position to place crafting_table.");
    await placeItem(bot, "crafting_table", placePos);
  }
  if (countItem(bot, "jungle_slab") >= 8) return;
  if (countItem(bot, "jungle_planks") < 3) {
    throw new Error("Not enough jungle_planks to craft jungle_slab.");
  }
  await craftItem(bot, "jungle_slab", 2);
  if (countItem(bot, "jungle_slab") < 8) {
    throw new Error("Failed to craft 8 jungle_slab.");
  }
}