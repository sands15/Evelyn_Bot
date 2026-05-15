function countItem(bot, name) {
  const item = mcData.itemsByName[name];
  return item ? bot.inventory.count(item.id, null) : 0;
}

function findClosePlacement(bot) {
  const base = bot.entity.position.floored();
  const offsets = [new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1), new Vec3(1, 0, 1), new Vec3(-1, 0, -1), new Vec3(1, 0, -1), new Vec3(-1, 0, 1)];
  for (const offset of offsets) {
    const pos = base.plus(offset);
    const block = bot.blockAt(pos);
    const below = bot.blockAt(pos.offset(0, -1, 0));
    const above = bot.blockAt(pos.offset(0, 1, 0));
    if (block && below && above && block.name === "air" && above.name === "air" && below.name !== "air") {
      return pos;
    }
  }
  return null;
}

async function craftInventoryItem(bot, name, count) {
  const item = mcData.itemsByName[name];
  const recipe = bot.recipesFor(item.id, null, 1, null)[0];
  if (!recipe) throw new Error("No inventory recipe for " + name + ".");
  await bot.craft(recipe, count, null);
}

async function ensureCraftingTableNearby(bot) {
  let table = bot.findBlock({
    matching: mcData.blocksByName["crafting_table"].id,
    maxDistance: 32
  });
  if (table) return table;
  if (countItem(bot, "crafting_table") < 1) {
    if (countItem(bot, "jungle_planks") < 4) {
      if (countItem(bot, "jungle_log") < 1) {
        throw new Error("Need planks or logs to craft a crafting_table.");
      }
      await craftInventoryItem(bot, "jungle_planks", 1);
    }
    if (countItem(bot, "crafting_table") < 1) {
      await craftInventoryItem(bot, "crafting_table", 1);
    }
  }
  const placePos = findClosePlacement(bot);
  if (!placePos) throw new Error("No nearby solid position to place crafting_table.");
  await placeItem(bot, "crafting_table", placePos);
  table = bot.findBlock({
    matching: mcData.blocksByName["crafting_table"].id,
    maxDistance: 32
  });
  if (!table) throw new Error("Failed to place crafting_table.");
  return table;
}

async function craftOneStonePickaxe(bot) {
  if (countItem(bot, "stone_pickaxe") >= 1) return;
  if (countItem(bot, "cobblestone") < 3) {
    await mineBlock(bot, "stone", 3 - countItem(bot, "cobblestone"));
  }
  if (countItem(bot, "stone_pickaxe") >= 1) return;
  if (countItem(bot, "cobblestone") < 3) {
    throw new Error("Need 3 cobblestone to craft a stone_pickaxe.");
  }
  if (countItem(bot, "stick") < 2) {
    throw new Error("Need 2 sticks to craft a stone_pickaxe.");
  }
  await ensureCraftingTableNearby(bot);
  await craftItem(bot, "stone_pickaxe", 1);
  if (countItem(bot, "stone_pickaxe") < 1) {
    throw new Error("Failed to craft stone_pickaxe.");
  }
}