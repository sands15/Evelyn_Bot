async function craftOneWoodenSword(bot) {
  function countItem(name) {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  }
  function totalPlanks() {
    const plankNames = ["oak_planks", "spruce_planks", "birch_planks", "jungle_planks", "acacia_planks", "dark_oak_planks", "mangrove_planks", "cherry_planks", "bamboo_planks", "crimson_planks", "warped_planks"];
    return plankNames.reduce((sum, name) => sum + countItem(name), 0);
  }
  function findPlacementPosition() {
    const base = bot.entity.position.floored();
    for (let r = 1; r <= 3; r++) {
      for (let dx = -r; dx <= r; dx++) {
        for (let dz = -r; dz <= r; dz++) {
          if (Math.abs(dx) !== r && Math.abs(dz) !== r) continue;
          const pos = base.offset(dx, 0, dz);
          const block = bot.blockAt(pos);
          const below = bot.blockAt(pos.offset(0, -1, 0));
          if (block && block.name === "air" && below && below.name !== "air") {
            return pos;
          }
        }
      }
    }
    return null;
  }
  if (countItem("wooden_sword") >= 1) return;
  let craftingTable = bot.findBlock({
    matching: mcData.blocksByName["crafting_table"].id,
    maxDistance: 32
  });
  if (!craftingTable) {
    if (countItem("crafting_table") < 1) {
      if (totalPlanks() < 4) {
        if (countItem("jungle_log") > 0) {
          await craftItem(bot, "jungle_planks", 1);
        }
      }
      if (totalPlanks() < 4) {
        throw new Error("Need 4 planks to craft a crafting_table.");
      }
      const tableRecipe = bot.recipesFor(mcData.itemsByName["crafting_table"].id, null, 1, null)[0];
      await bot.craft(tableRecipe, 1, null);
    }
    const placePos = findPlacementPosition();
    if (!placePos) {
      throw new Error("Could not find a valid nearby position to place crafting_table.");
    }
    await placeItem(bot, "crafting_table", placePos);
    craftingTable = bot.findBlock({
      matching: mcData.blocksByName["crafting_table"].id,
      maxDistance: 32
    });
    if (!craftingTable) {
      throw new Error("Failed to place crafting_table.");
    }
  }
  if (totalPlanks() < 2) {
    if (countItem("jungle_log") > 0) {
      await craftItem(bot, "jungle_planks", 1);
    }
  }
  if (countItem("stick") < 1) {
    if (totalPlanks() < 2) {
      throw new Error("Need planks to craft sticks.");
    }
    await craftItem(bot, "stick", 1);
  }
  if (totalPlanks() < 2) {
    throw new Error("Need 2 planks to craft a wooden_sword.");
  }
  if (countItem("stick") < 1) {
    throw new Error("Need 1 stick to craft a wooden_sword.");
  }
  await craftItem(bot, "wooden_sword", 1);
  if (countItem("wooden_sword") < 1) {
    throw new Error("Failed to craft wooden_sword.");
  }
}