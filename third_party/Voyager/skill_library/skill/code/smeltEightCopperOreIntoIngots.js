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

async function smeltEightCopperOreIntoIngots(bot) {
  if (countItem(bot, "copper_ingot") >= 8) return;
  let neededIngots = 8 - countItem(bot, "copper_ingot");
  if (countItem(bot, "raw_copper") < neededIngots) {
    const pickaxe = bot.inventory.findInventoryItem(mcData.itemsByName["iron_pickaxe"].id) || bot.inventory.findInventoryItem(mcData.itemsByName["stone_pickaxe"].id) || bot.inventory.findInventoryItem(mcData.itemsByName["copper_pickaxe"].id) || bot.inventory.findInventoryItem(mcData.itemsByName["wooden_pickaxe"].id);
    if (!pickaxe) {
      throw new Error("Need a pickaxe to mine copper_ore.");
    }
    await bot.equip(pickaxe, "hand");
    let nearbyCopper = bot.findBlocks({
      matching: block => block.name === "copper_ore",
      maxDistance: 32,
      count: neededIngots
    });
    if (nearbyCopper.length === 0) {
      const foundCopper = await exploreUntil(bot, new Vec3(0, -1, 0), 60, () => {
        return bot.findBlock({
          matching: mcData.blocksByName["copper_ore"].id,
          maxDistance: 32
        });
      });
      if (!foundCopper) {
        throw new Error("Could not find copper_ore to smelt.");
      }
    }
    neededIngots = 8 - countItem(bot, "copper_ingot");
    const neededRaw = Math.max(0, neededIngots - countItem(bot, "raw_copper"));
    if (neededRaw > 0) {
      await mineBlock(bot, "copper_ore", neededRaw);
    }
  }
  neededIngots = 8 - countItem(bot, "copper_ingot");
  if (neededIngots <= 0) return;
  if (countItem(bot, "raw_copper") < neededIngots) {
    throw new Error("Failed to collect enough raw_copper from copper_ore.");
  }
  if (countItem(bot, "coal") < neededIngots) {
    throw new Error("Need coal to smelt copper into copper_ingots.");
  }
  let furnace = bot.findBlock({
    matching: mcData.blocksByName["furnace"].id,
    maxDistance: 32
  });
  if (!furnace) {
    if (countItem(bot, "furnace") < 1) {
      throw new Error("Need a furnace to smelt copper.");
    }
    const placePos = findNearbyPlacePosition(bot);
    if (!placePos) {
      throw new Error("Could not find a valid position to place the furnace.");
    }
    await placeItem(bot, "furnace", placePos);
    furnace = bot.findBlock({
      matching: mcData.blocksByName["furnace"].id,
      maxDistance: 32
    });
    if (!furnace) {
      throw new Error("Failed to place furnace.");
    }
  }
  await smeltItem(bot, "raw_copper", "coal", neededIngots);
  if (countItem(bot, "copper_ingot") < 8) {
    throw new Error("Failed to smelt 8 copper_ingots.");
  }
}