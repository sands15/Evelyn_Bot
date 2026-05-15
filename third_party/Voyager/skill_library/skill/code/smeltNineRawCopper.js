function countItem(bot, name) {
  const item = mcData.itemsByName[name];
  return item ? bot.inventory.count(item.id, null) : 0;
}

function findNearbyPlacePosition(bot) {
  const base = bot.entity.position.floored();
  for (let r = 1; r <= 3; r++) {
    for (let dx = -r; dx <= r; dx++) {
      for (let dz = -r; dz <= r; dz++) {
        if (Math.abs(dx) !== r && Math.abs(dz) !== r) continue;
        const pos = base.offset(dx, 0, dz);
        const block = bot.blockAt(pos);
        const below = bot.blockAt(pos.offset(0, -1, 0));
        if (block && block.name === "air" && below && below.name !== "air") {
          return pos;
        }
      }
    }
  }
  return null;
}

async function smeltNineRawCopper(bot) {
  const target = 9;
  if (countItem(bot, "copper_ingot") >= target) return;
  if (countItem(bot, "raw_copper") < target) {
    throw new Error("Need 9 raw_copper to smelt.");
  }
  if (countItem(bot, "coal") < target) {
    const pickaxe = bot.inventory.findInventoryItem(mcData.itemsByName["stone_pickaxe"].id);
    if (!pickaxe) {
      throw new Error("Need a stone_pickaxe to mine coal_ore.");
    }
    await bot.equip(pickaxe, "hand");
    let neededCoal = target - countItem(bot, "coal");
    let nearbyCoal = bot.findBlocks({
      matching: block => block.name === "coal_ore",
      maxDistance: 32,
      count: neededCoal
    });
    if (nearbyCoal.length > 0) {
      await mineBlock(bot, "coal_ore", Math.min(nearbyCoal.length, neededCoal));
    }
    if (countItem(bot, "coal") < target) {
      const foundCoal = await exploreUntil(bot, new Vec3(0, -1, 0), 60, () => {
        return bot.findBlock({
          matching: mcData.blocksByName["coal_ore"].id,
          maxDistance: 32
        });
      });
      if (!foundCoal) {
        throw new Error("Could not find enough coal_ore for fuel.");
      }
      neededCoal = target - countItem(bot, "coal");
      await bot.equip(pickaxe, "hand");
      await mineBlock(bot, "coal_ore", neededCoal);
    }
  }
  if (countItem(bot, "coal") < target) {
    throw new Error("Failed to obtain enough coal.");
  }
  let furnace = bot.findBlock({
    matching: mcData.blocksByName["furnace"].id,
    maxDistance: 32
  });
  if (!furnace) {
    if (countItem(bot, "furnace") < 1) {
      if (countItem(bot, "cobblestone") < 8) {
        throw new Error("Need 8 cobblestone to craft a furnace.");
      }
      await craftItem(bot, "furnace", 1);
    }
    const placePos = findNearbyPlacePosition(bot);
    if (!placePos) {
      throw new Error("Could not find a valid place for the furnace.");
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
  await smeltItem(bot, "raw_copper", "coal", target);
  if (countItem(bot, "copper_ingot") < target) {
    throw new Error("Failed to smelt 9 raw_copper.");
  }
}