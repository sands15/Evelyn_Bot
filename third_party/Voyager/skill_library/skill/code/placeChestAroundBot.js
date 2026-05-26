function countInventoryItem(bot, name) {
  const item = mcData.itemsByName[name];
  return item ? bot.inventory.count(item.id, null) : 0;
}

function isSolidReference(block) {
  return block && block.name !== "air" && block.name !== "water" && block.name !== "lava";
}

function findNearbyChestPlacementSpot(bot) {
  const base = bot.entity.position.floored();
  const offsets = [];
  for (let y = -1; y <= 2; y++) {
    for (let x = -3; x <= 3; x++) {
      for (let z = -3; z <= 3; z++) {
        if (x === 0 && z === 0 && (y === 0 || y === 1)) continue;
        offsets.push(new Vec3(x, y, z));
      }
    }
  }
  offsets.sort((a, b) => {
    const da = Math.abs(a.x) + Math.abs(a.y) + Math.abs(a.z);
    const db = Math.abs(b.x) + Math.abs(b.y) + Math.abs(b.z);
    return da - db;
  });
  for (const offset of offsets) {
    const pos = base.plus(offset);
    const block = bot.blockAt(pos);
    if (!block || block.name !== "air") continue;
    const below = bot.blockAt(pos.offset(0, -1, 0));
    const sides = [bot.blockAt(pos.offset(1, 0, 0)), bot.blockAt(pos.offset(-1, 0, 0)), bot.blockAt(pos.offset(0, 0, 1)), bot.blockAt(pos.offset(0, 0, -1))];
    if (isSolidReference(below) || sides.some(isSolidReference)) {
      return pos;
    }
  }
  return null;
}

async function placeChestAroundBot(bot) {
  let nearbyChest = bot.findBlock({
    matching: mcData.blocksByName["chest"].id,
    maxDistance: 32
  });
  if (nearbyChest) return;
  if (countInventoryItem(bot, "chest") < 1) {
    throw new Error("Need a chest in inventory to place.");
  }
  const placePos = findNearbyChestPlacementSpot(bot);
  if (!placePos) {
    throw new Error("LOCAL_PLACEMENT_FAILED: no nearby existing air block can support chest placement.");
  }
  await placeItem(bot, "chest", placePos);
  nearbyChest = bot.findBlock({
    matching: mcData.blocksByName["chest"].id,
    maxDistance: 32
  });
  if (!nearbyChest) {
    throw new Error("Failed to place chest nearby.");
  }
}