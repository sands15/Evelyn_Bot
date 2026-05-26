function countLocalItem(bot, name) {
  const item = mcData.itemsByName[name];
  return item ? bot.inventory.count(item.id, null) : 0;
}

function isUsableChestSupport(block) {
  return block && block.name !== "air" && block.name !== "cave_air" && block.name !== "water" && block.name !== "lava" && block.name !== "crafting_table";
}

function findChestPlacementCandidates(bot) {
  const base = bot.entity.position.floored();
  const candidates = [];
  for (let y = -1; y <= 1; y++) {
    for (let x = -3; x <= 3; x++) {
      for (let z = -3; z <= 3; z++) {
        if (x === 0 && z === 0 && (y === 0 || y === 1)) continue;
        const pos = base.offset(x, y, z);
        const block = bot.blockAt(pos);
        if (!block || block.name !== "air" && block.name !== "cave_air") continue;
        const below = bot.blockAt(pos.offset(0, -1, 0));
        if (!isUsableChestSupport(below)) continue;
        candidates.push({
          pos,
          distance: Math.abs(x) + Math.abs(y) + Math.abs(z)
        });
      }
    }
  }
  candidates.sort((a, b) => a.distance - b.distance);
  return candidates.map(candidate => candidate.pos);
}

async function placeAChestAroundMe(bot) {
  let existingChest = bot.findBlock({
    matching: mcData.blocksByName["chest"].id,
    maxDistance: 32
  });
  if (existingChest) return;
  if (countLocalItem(bot, "chest") < 1) {
    throw new Error("Need a chest in inventory to place.");
  }
  const candidates = findChestPlacementCandidates(bot);
  if (candidates.length === 0) {
    throw new Error("LOCAL_PLACEMENT_FAILED: no nearby floor-supported air block for chest.");
  }
  for (const pos of candidates.slice(0, 8)) {
    try {
      await placeItem(bot, "chest", pos);
      const placedBlock = bot.blockAt(pos);
      if (placedBlock && placedBlock.name === "chest") return;
      existingChest = bot.findBlock({
        matching: mcData.blocksByName["chest"].id,
        maxDistance: 32
      });
      if (existingChest) return;
    } catch (err) {
      existingChest = bot.findBlock({
        matching: mcData.blocksByName["chest"].id,
        maxDistance: 32
      });
      if (existingChest) return;
    }
    if (countLocalItem(bot, "chest") < 1) {
      existingChest = bot.findBlock({
        matching: mcData.blocksByName["chest"].id,
        maxDistance: 32
      });
      if (existingChest) return;
      throw new Error("Chest item was consumed but no nearby chest was detected.");
    }
  }
  throw new Error("LOCAL_PLACEMENT_FAILED: tried nearby supported positions but could not place chest.");
}