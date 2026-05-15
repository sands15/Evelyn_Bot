function countItem(bot, name) {
  const item = mcData.itemsByName[name];
  return item ? bot.inventory.count(item.id, null) : 0;
}

function findPlacePosition(bot) {
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

async function craftOneCopperPickaxe(bot) {
  if (countItem(bot, "copper_pickaxe") >= 1) return;
  if (countItem(bot, "copper_ingot") < 3) {
    throw new Error("Need 3 copper_ingot to craft a copper_pickaxe.");
  }
  let craftingTable = bot.findBlock({
    matching: mcData.blocksByName["crafting_table"].id,
    maxDistance: 32
  });
  if (!craftingTable) {
    if (countItem(bot, "crafting_table") < 1) {
      throw new Error("Need a crafting_table to craft a copper_pickaxe.");
    }
    const placePos = findPlacePosition(bot);
    if (!placePos) throw new Error("No valid nearby position to place crafting_table.");
    await placeItem(bot, "crafting_table", placePos);
    craftingTable = bot.findBlock({
      matching: mcData.blocksByName["crafting_table"].id,
      maxDistance: 32
    });
    if (!craftingTable) throw new Error("Failed to place crafting_table.");
  }
  if (countItem(bot, "stick") < 2) {
    if (countItem(bot, "jungle_planks") < 2) {
      if (countItem(bot, "jungle_log") < 1) {
        throw new Error("Need planks or logs to craft sticks.");
      }
      await craftItem(bot, "jungle_planks", 1);
    }
    if (countItem(bot, "stick") < 2) {
      await craftItem(bot, "stick", 1);
    }
  }
  if (countItem(bot, "stick") < 2) {
    throw new Error("Failed to craft enough sticks.");
  }
  if (countItem(bot, "copper_pickaxe") >= 1) return;
  await craftItem(bot, "copper_pickaxe", 1);
  if (countItem(bot, "copper_pickaxe") < 1) {
    throw new Error("Failed to craft copper_pickaxe.");
  }
}