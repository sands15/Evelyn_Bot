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

async function craftOneIronPickaxe(bot) {
  if (countItem(bot, "iron_pickaxe") >= 1) return;
  if (countItem(bot, "stick") < 2) {
    if (countItem(bot, "oak_planks") < 2 && countItem(bot, "jungle_planks") < 2) {
      throw new Error("Need planks to craft sticks for iron_pickaxe.");
    }
    await craftItem(bot, "stick", 1);
  }
  if (countItem(bot, "iron_pickaxe") >= 1) return;
  const missingIngots = 3 - countItem(bot, "iron_ingot");
  if (missingIngots > 0) {
    if (countItem(bot, "raw_iron") < missingIngots) {
      throw new Error("Need raw_iron to smelt enough iron_ingot for iron_pickaxe.");
    }
    let furnace = bot.findBlock({
      matching: mcData.blocksByName["furnace"].id,
      maxDistance: 32
    });
    if (!furnace) {
      if (countItem(bot, "furnace") < 1) {
        if (countItem(bot, "cobblestone") < 8) {
          await mineBlock(bot, "stone", 8 - countItem(bot, "cobblestone"));
        }
        await craftItem(bot, "furnace", 1);
      }
      const placePos = findNearbyPlacePosition(bot);
      if (!placePos) throw new Error("No valid nearby position to place furnace.");
      await placeItem(bot, "furnace", placePos);
      furnace = bot.findBlock({
        matching: mcData.blocksByName["furnace"].id,
        maxDistance: 32
      });
      if (!furnace) throw new Error("Failed to place furnace.");
    }
    if (countItem(bot, "oak_planks") < missingIngots) {
      throw new Error("Need enough oak_planks fuel to smelt raw_iron.");
    }
    await smeltItem(bot, "raw_iron", "oak_planks", missingIngots);
  }
  if (countItem(bot, "iron_ingot") < 3) {
    throw new Error("Failed to obtain 3 iron_ingot.");
  }
  if (countItem(bot, "stick") < 2) {
    throw new Error("Failed to obtain 2 sticks.");
  }
  await craftItem(bot, "iron_pickaxe", 1);
  if (countItem(bot, "iron_pickaxe") < 1) {
    throw new Error("Failed to craft iron_pickaxe.");
  }
}