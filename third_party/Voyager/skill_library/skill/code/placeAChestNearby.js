function findNearbyChestPlacePosition(bot) {
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

async function placeAChestNearby(bot) {
  const existingChest = bot.findBlock({
    matching: mcData.blocksByName["chest"].id,
    maxDistance: 32
  });
  if (existingChest) return;
  if (countItem(bot, "chest") < 1) {
    await craftOneChest(bot);
  }
  if (countItem(bot, "chest") < 1) {
    throw new Error("Need a chest to place.");
  }
  const placePos = findNearbyChestPlacePosition(bot);
  if (!placePos) {
    throw new Error("No valid nearby position to place chest.");
  }
  await placeItem(bot, "chest", placePos);
  const placedChest = bot.findBlock({
    matching: mcData.blocksByName["chest"].id,
    maxDistance: 32
  });
  if (!placedChest) {
    throw new Error("Failed to place chest nearby.");
  }
}