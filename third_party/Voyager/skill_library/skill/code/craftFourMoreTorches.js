async function craftFourMoreTorches(bot) {
  function countItem(name) {
    const item = mcData.itemsByName[name];
    return item ? bot.inventory.count(item.id, null) : 0;
  }
  const startingTorches = countItem("torch");
  const targetTorches = startingTorches + 4;
  if (countItem("torch") >= targetTorches) return;
  if (countItem("stick") < 1) {
    if (countItem("oak_planks") < 2 && countItem("birch_planks") < 2 && countItem("cherry_planks") < 2) {
      if (countItem("oak_log") > 0) {
        await craftItem(bot, "oak_planks", 1);
      } else if (countItem("birch_log") > 0) {
        await craftItem(bot, "birch_planks", 1);
      } else if (countItem("cherry_log") > 0) {
        await craftItem(bot, "cherry_planks", 1);
      } else {
        throw new Error("Need planks or logs to craft sticks for torches.");
      }
    }
    await craftItem(bot, "stick", 1);
  }
  if (countItem("torch") >= targetTorches) return;
  if (countItem("coal") < 1) {
    const pickaxeNames = ["netherite_pickaxe", "diamond_pickaxe", "iron_pickaxe", "stone_pickaxe", "wooden_pickaxe"];
    let pickaxe = null;
    for (const name of pickaxeNames) {
      const item = mcData.itemsByName[name];
      if (!item) continue;
      pickaxe = bot.inventory.findInventoryItem(item.id);
      if (pickaxe) break;
    }
    if (!pickaxe) throw new Error("Need coal or a pickaxe to mine coal_ore for torches.");
    await bot.equip(pickaxe, "hand");
    let coalBlock = bot.findBlock({
      matching: block => block.name === "coal_ore" || block.name === "deepslate_coal_ore",
      maxDistance: 32
    });
    if (!coalBlock) {
      const result = await searchForOre(bot, {
        oreName: "coal_ore",
        quantity: 1,
        maxSearchBudgetSec: 12
      });
      if (!result || result.success === false) {
        throw new Error(result?.reason || "LOCAL_SEARCH_EXHAUSTED: coal_ore not nearby for torch crafting.");
      }
    }
    coalBlock = bot.findBlock({
      matching: block => block.name === "coal_ore" || block.name === "deepslate_coal_ore",
      maxDistance: 32
    });
    if (!coalBlock) throw new Error("LOCAL_SEARCH_EXHAUSTED: search ended without nearby coal_ore.");
    if (coalBlock.name === "deepslate_coal_ore") {
      await mineBlock(bot, "deepslate_coal_ore", 1);
    } else {
      await mineBlock(bot, "coal_ore", 1);
    }
  }
  if (countItem("stick") < 1) throw new Error("Need 1 stick to craft torches.");
  if (countItem("coal") < 1) throw new Error("Need 1 coal to craft torches.");
  await craftItem(bot, "torch", 1);
  if (countItem("torch") < targetTorches) {
    throw new Error(`Failed to craft 4 more torches: have ${countItem("torch")}, need ${targetTorches}.`);
  }
}