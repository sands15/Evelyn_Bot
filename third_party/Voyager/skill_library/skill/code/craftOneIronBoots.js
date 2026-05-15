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

async function craftOneIronBoots(bot) {
  if (countItem(bot, "iron_boots") >= 1) return;
  const neededIngots = 4 - countItem(bot, "iron_ingot");
  if (neededIngots > 0) {
    if (countItem(bot, "raw_iron") < neededIngots) {
      throw new Error("Need raw_iron to smelt enough iron_ingot for iron_boots.");
    }
    if (countItem(bot, "coal") < neededIngots) {
      throw new Error("Need enough coal to smelt iron for iron_boots.");
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
    await smeltItem(bot, "raw_iron", "coal", neededIngots);
  }
  if (countItem(bot, "iron_ingot") < 4) {
    throw new Error("Failed to obtain 4 iron_ingot for iron_boots.");
  }
  await craftItem(bot, "iron_boots", 1);
  if (countItem(bot, "iron_boots") < 1) {
    throw new Error("Failed to craft iron_boots.");
  }
}