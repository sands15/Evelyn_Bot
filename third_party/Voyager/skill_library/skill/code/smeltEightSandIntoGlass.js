async function smeltEightSandIntoGlass(bot) {
  const glassItem = mcData.itemsByName["glass"];
  const sandItem = mcData.itemsByName["sand"];
  const fuelItem = mcData.itemsByName["jungle_planks"];
  const glassCount = () => bot.inventory.count(glassItem.id, null);
  const sandCount = () => bot.inventory.count(sandItem.id, null);
  const fuelCount = () => bot.inventory.count(fuelItem.id, null);
  if (glassCount() >= 8) return;
  const needed = 8 - glassCount();
  if (sandCount() < needed) {
    throw new Error("Need 8 sand to smelt 8 glass.");
  }
  if (fuelCount() < needed) {
    throw new Error("Need enough jungle_planks as fuel to smelt 8 sand.");
  }
  let furnace = bot.findBlock({
    matching: mcData.blocksByName["furnace"].id,
    maxDistance: 32
  });
  if (!furnace) {
    if (bot.inventory.count(mcData.itemsByName["furnace"].id, null) < 1) {
      throw new Error("Need a furnace to smelt sand into glass.");
    }
    const base = bot.entity.position.floored();
    const offsets = [new Vec3(1, 0, 0), new Vec3(-1, 0, 0), new Vec3(0, 0, 1), new Vec3(0, 0, -1)];
    let placePos = null;
    for (const offset of offsets) {
      const pos = base.plus(offset);
      const block = bot.blockAt(pos);
      const below = bot.blockAt(pos.offset(0, -1, 0));
      if (block && below && block.name === "air" && below.name !== "air") {
        placePos = pos;
        break;
      }
    }
    if (!placePos) {
      throw new Error("Could not find a valid nearby position to place the furnace.");
    }
    await placeItem(bot, "furnace", placePos);
  }
  await smeltItem(bot, "sand", "jungle_planks", needed);
  if (glassCount() < 8) {
    throw new Error("Failed to smelt 8 sand into 8 glass.");
  }
}