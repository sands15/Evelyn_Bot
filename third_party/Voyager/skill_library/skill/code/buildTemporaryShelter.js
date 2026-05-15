async function buildTemporaryShelter(bot) {
  function countItem(name) {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  }
  function shelterPositions(center) {
    const positions = [];

    // Two-high 3x3 wall ring around the bot.
    for (let y = 0; y <= 1; y++) {
      for (let dx = -1; dx <= 1; dx++) {
        for (let dz = -1; dz <= 1; dz++) {
          if (dx === 0 && dz === 0) continue;
          positions.push(center.offset(dx, y, dz));
        }
      }
    }

    // Roof over the 3x3 shelter.
    for (let dx = -1; dx <= 1; dx++) {
      for (let dz = -1; dz <= 1; dz++) {
        positions.push(center.offset(dx, 2, dz));
      }
    }
    return positions;
  }
  const center = bot.entity.position.floored();
  const positions = shelterPositions(center);
  let missingBlocks = 0;
  for (const pos of positions) {
    const block = bot.blockAt(pos);
    if (block && block.name === "air") missingBlocks++;
  }
  if (missingBlocks === 0) return;
  if (countItem("dirt") < missingBlocks) {
    const needed = missingBlocks - countItem("dirt");
    await mineBlock(bot, "dirt", needed);
  }
  if (countItem("dirt") < missingBlocks) {
    throw new Error("LOCAL_SEARCH_EXHAUSTED: not enough nearby dirt to complete a temporary shelter.");
  }
  for (const pos of positions) {
    const block = bot.blockAt(pos);
    if (!block || block.name !== "air") continue;
    if (pos.equals(center) || pos.equals(center.offset(0, 1, 0))) continue;
    await placeItem(bot, "dirt", pos);
  }
  await bot.pathfinder.goto(new GoalNear(center.x, center.y, center.z, 0));
}