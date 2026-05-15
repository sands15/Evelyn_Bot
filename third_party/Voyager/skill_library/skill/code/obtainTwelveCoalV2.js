function countCoal(bot) {
  const coal = mcData.itemsByName["coal"];
  return coal ? bot.inventory.count(coal.id, null) : 0;
}

function countItem(bot, name) {
  const item = mcData.itemsByName[name];
  return item ? bot.inventory.count(item.id, null) : 0;
}

async function ensureCoalPickaxe(bot) {
  const pickaxeNames = ["stone_pickaxe", "iron_pickaxe", "diamond_pickaxe", "netherite_pickaxe", "wooden_pickaxe"];
  for (const name of pickaxeNames) {
    const item = mcData.itemsByName[name];
    const pickaxe = item ? bot.inventory.findInventoryItem(item.id) : null;
    if (pickaxe) {
      await bot.equip(pickaxe, "hand");
      return;
    }
  }
  const craftingTable = bot.findBlock({
    matching: mcData.blocksByName["crafting_table"].id,
    maxDistance: 32
  });
  if ((craftingTable || countItem(bot, "crafting_table") > 0) && countItem(bot, "cobblestone") >= 3 && countItem(bot, "stick") >= 2) {
    await craftItem(bot, "stone_pickaxe", 1);
    const newPickaxe = bot.inventory.findInventoryItem(mcData.itemsByName["stone_pickaxe"].id);
    if (newPickaxe) {
      await bot.equip(newPickaxe, "hand");
      return;
    }
  }
  throw new Error("Need a pickaxe to mine coal_ore.");
}

async function mineNearbyCoalUntilEnough(bot) {
  if (countCoal(bot) >= 12) return;
  await ensureCoalPickaxe(bot);
  const needed = 12 - countCoal(bot);
  const coalOre = bot.findBlocks({
    matching: block => block.name === "coal_ore",
    maxDistance: 32,
    count: needed
  });
  if (coalOre.length > 0) {
    await mineBlock(bot, "coal_ore", Math.min(coalOre.length, needed));
  }
  if (countCoal(bot) >= 12) return;
  const stillNeeded = 12 - countCoal(bot);
  const deepslateCoalOre = bot.findBlocks({
    matching: block => block.name === "deepslate_coal_ore",
    maxDistance: 32,
    count: stillNeeded
  });
  if (deepslateCoalOre.length > 0) {
    await mineBlock(bot, "deepslate_coal_ore", Math.min(deepslateCoalOre.length, stillNeeded));
  }
}

async function obtainTwelveCoal(bot) {
  if (countCoal(bot) >= 12) return;
  await mineNearbyCoalUntilEnough(bot);
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
      await mineNearbyCoalUntilEnough(bot);
      if (countCoal(bot) >= 12) return;
    }
  }
  throw new Error("LOCAL_SEARCH_EXHAUSTED: Could not obtain 12 coal from nearby coal ore with two short local probes.");
}