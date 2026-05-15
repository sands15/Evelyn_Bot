function countCoal(bot) {
  const coal = mcData.itemsByName["coal"];
  return coal ? bot.inventory.count(coal.id, null) : 0;
}

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

async function ensurePickaxeForCoal(bot) {
  const pickaxe = bot.inventory.findInventoryItem(mcData.itemsByName["iron_pickaxe"]?.id) || bot.inventory.findInventoryItem(mcData.itemsByName["stone_pickaxe"]?.id) || bot.inventory.findInventoryItem(mcData.itemsByName["wooden_pickaxe"]?.id);
  if (pickaxe) {
    await bot.equip(pickaxe, "hand");
    return;
  }
  if (countItem(bot, "cobblestone") < 3 || countItem(bot, "stick") < 2) {
    throw new Error("Need 3 cobblestone and 2 sticks to craft a stone_pickaxe.");
  }
  let craftingTable = bot.findBlock({
    matching: mcData.blocksByName["crafting_table"].id,
    maxDistance: 32
  });
  if (!craftingTable) {
    if (countItem(bot, "crafting_table") < 1) {
      throw new Error("Need a crafting_table to craft a stone_pickaxe.");
    }
    const tablePos = findNearbyPlacePosition(bot);
    if (!tablePos) throw new Error("Could not find a valid place for the crafting_table.");
    await placeItem(bot, "crafting_table", tablePos);
  }
  await craftItem(bot, "stone_pickaxe", 1);
  const newPickaxe = bot.inventory.findInventoryItem(mcData.itemsByName["stone_pickaxe"].id);
  if (!newPickaxe) throw new Error("Failed to craft a stone_pickaxe.");
  await bot.equip(newPickaxe, "hand");
}

async function mineNearbyCoalOre(bot) {
  const before = countCoal(bot);
  const needed = 12 - before;
  if (needed <= 0) return;
  const coalOreBlocks = bot.findBlocks({
    matching: block => block.name === "coal_ore",
    maxDistance: 32,
    count: needed
  });
  if (coalOreBlocks.length > 0) {
    await ensurePickaxeForCoal(bot);
    await mineBlock(bot, "coal_ore", Math.min(coalOreBlocks.length, needed));
  }
  if (countCoal(bot) >= 12) return;
  const stillNeeded = 12 - countCoal(bot);
  const deepslateCoalOreBlocks = bot.findBlocks({
    matching: block => block.name === "deepslate_coal_ore",
    maxDistance: 32,
    count: stillNeeded
  });
  if (deepslateCoalOreBlocks.length > 0) {
    await ensurePickaxeForCoal(bot);
    await mineBlock(bot, "deepslate_coal_ore", Math.min(deepslateCoalOreBlocks.length, stillNeeded));
  }
}

async function obtainTwelveCoal(bot) {
  if (countCoal(bot) >= 12) return;
  await ensurePickaxeForCoal(bot);
  await mineNearbyCoalOre(bot);
  if (countCoal(bot) >= 12) return;
  const probes = [new Vec3(0, -1, 0), new Vec3(1, 0, 1)];
  for (const direction of probes) {
    const foundCoal = await exploreUntil(bot, direction, 15, () => {
      return bot.findBlock({
        matching: block => block.name === "coal_ore" || block.name === "deepslate_coal_ore",
        maxDistance: 32
      });
    });
    if (foundCoal) {
      await ensurePickaxeForCoal(bot);
      await mineNearbyCoalOre(bot);
      if (countCoal(bot) >= 12) return;
    }
  }
  throw new Error("LOCAL_SEARCH_EXHAUSTED: Could not obtain 12 coal from nearby coal_ore within two short probes.");
}