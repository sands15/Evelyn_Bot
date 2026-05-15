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

async function craft16Torches(bot) {
  if (countItem(bot, "torch") >= 16) return;
  let craftingTable = bot.findBlock({
    matching: mcData.blocksByName["crafting_table"].id,
    maxDistance: 32
  });
  if (!craftingTable) {
    if (countItem(bot, "crafting_table") < 1) {
      throw new Error("Need a crafting_table nearby or in inventory.");
    }
    const placePos = findNearbyPlacePosition(bot);
    if (!placePos) throw new Error("No valid nearby position to place crafting_table.");
    await placeItem(bot, "crafting_table", placePos);
    craftingTable = bot.findBlock({
      matching: mcData.blocksByName["crafting_table"].id,
      maxDistance: 32
    });
    if (!craftingTable) throw new Error("Failed to place crafting_table.");
  }
  const missingTorches = 16 - countItem(bot, "torch");
  const torchRecipesNeeded = Math.ceil(missingTorches / 4);
  if (countItem(bot, "coal") < torchRecipesNeeded) {
    throw new Error("Need more coal to craft 16 torches.");
  }
  if (countItem(bot, "stick") < torchRecipesNeeded) {
    const sticksNeeded = torchRecipesNeeded - countItem(bot, "stick");
    const stickRecipesNeeded = Math.ceil(sticksNeeded / 4);
    const planksNeeded = stickRecipesNeeded * 2;
    if (countItem(bot, "jungle_planks") < planksNeeded) {
      const missingPlanks = planksNeeded - countItem(bot, "jungle_planks");
      const logsNeeded = Math.ceil(missingPlanks / 4);
      if (countItem(bot, "jungle_log") < logsNeeded) {
        await mineBlock(bot, "jungle_log", logsNeeded - countItem(bot, "jungle_log"));
      }
      await craftItem(bot, "jungle_planks", logsNeeded);
    }
    await craftItem(bot, "stick", stickRecipesNeeded);
  }
  if (countItem(bot, "torch") >= 16) return;
  if (countItem(bot, "stick") < torchRecipesNeeded || countItem(bot, "coal") < torchRecipesNeeded) {
    throw new Error("Missing ingredients to craft remaining torches.");
  }
  await craftItem(bot, "torch", torchRecipesNeeded);
  if (countItem(bot, "torch") < 16) {
    throw new Error("Failed to craft 16 torches.");
  }
}