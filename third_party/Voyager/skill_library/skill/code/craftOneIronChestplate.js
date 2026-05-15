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

async function craftOneIronChestplate(bot) {
  if (countItem(bot, "iron_chestplate") >= 1) return;
  const missingIngots = 8 - countItem(bot, "iron_ingot");
  if (missingIngots > 0) {
    if (countItem(bot, "raw_iron") < missingIngots) {
      throw new Error("Need more raw_iron to craft an iron_chestplate.");
    }
    if (countItem(bot, "coal") < missingIngots) {
      throw new Error("Need enough coal to smelt iron for an iron_chestplate.");
    }
    if (countItem(bot, "furnace") < 1) {
      if (countItem(bot, "cobblestone") < 8) {
        await mineBlock(bot, "stone", 8 - countItem(bot, "cobblestone"));
      }
      await craftItem(bot, "furnace", 1);
    }
    const placePos = findNearbyPlacePosition(bot);
    if (!placePos) {
      throw new Error("No valid nearby position to place furnace.");
    }
    await placeItem(bot, "furnace", placePos);
    await smeltItem(bot, "raw_iron", "coal", missingIngots);
  }
  if (countItem(bot, "iron_chestplate") >= 1) return;
  if (countItem(bot, "iron_ingot") < 8) {
    throw new Error("Failed to obtain 8 iron_ingot for iron_chestplate.");
  }
  await craftItem(bot, "iron_chestplate", 1);
  if (countItem(bot, "iron_chestplate") < 1) {
    throw new Error("Failed to craft iron_chestplate.");
  }
}