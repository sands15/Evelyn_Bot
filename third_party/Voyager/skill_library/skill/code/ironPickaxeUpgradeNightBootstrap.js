async function craftIronPickaxeFromInventory(bot) {
  const ironPickaxeItem = mcData.itemsByName["iron_pickaxe"];
  if (!ironPickaxeItem) throw new Error("MISSING_ITEM: iron_pickaxe");

  // Goal check
  if (bot.inventory.count(ironPickaxeItem.id, null) >= 1) return "ALREADY_DONE";
  const ironIngotItem = mcData.itemsByName["iron_ingot"];
  const stickItem = mcData.itemsByName["stick"];
  if (!ironIngotItem || !stickItem) throw new Error("MISSING_ITEM: iron_ingot or stick");

  // Inventory-based prerequisites (minimal)
  const needIngots = 3 - bot.inventory.count(ironIngotItem.id, null);
  const needSticks = 2 - bot.inventory.count(stickItem.id, null);
  if (needIngots > 0 || needSticks > 0) {
    // Night safety: keep prerequisites short and local
    if (needSticks > 0) {
      // Try to gather only a tiny amount of wood for sticks
      await searchAndHarvest(bot, {
        goalType: "wood",
        quantity: needSticks,
        maxSearchBudgetSec: 10
      });
    }
    if (needIngots > 0) {
      // If we have raw_iron and nearby furnace, smelt short amount; otherwise try short iron-ore search
      const rawIronItem = mcData.itemsByName["raw_iron"];
      if (rawIronItem && bot.inventory.count(rawIronItem.id, null) >= needIngots) {
        const furnaceBlock = bot.findBlock({
          matching: mcData.blocksByName["furnace"].id,
          maxDistance: 32
        });
        if (!furnaceBlock) throw new Error("MISSING: furnace not found nearby for smelting raw_iron");

        // Prefer coal if available
        const coalItem = mcData.itemsByName["coal"];
        if (!coalItem) throw new Error("MISSING_ITEM: coal");
        if (bot.inventory.count(coalItem.id, null) < needIngots) {
          // Short fallback: try collecting coal locally
          await searchAndHarvest(bot, {
            goalType: "wood",
            quantity: 1,
            maxSearchBudgetSec: 6
          }); // no-op-ish fallback to keep bounded
          throw new Error("SCARCITY: need coal fuel to smelt raw_iron");
        }
        await smeltItem(bot, "raw_iron", "coal", needIngots);
      } else {
        await searchForOre(bot, {
          oreType: "iron",
          maxSearchBudgetSec: 14
        });
      }
    }
  }

  // Re-check sufficiency before crafting
  if (bot.inventory.count(ironIngotItem.id, null) < 3) {
    throw new Error("SCARCITY: need 3 iron_ingot to craft iron_pickaxe");
  }
  if (bot.inventory.count(stickItem.id, null) < 2) {
    throw new Error("SCARCITY: need 2 stick to craft iron_pickaxe");
  }
  await craftItem(bot, "iron_pickaxe", 1);
  if (bot.inventory.count(ironPickaxeItem.id, null) < 1) {
    throw new Error("CRAFT_FAILED: iron_pickaxe not found in inventory after crafting");
  }
  return "DONE";
}

async function ironPickaxeUpgradeNightBootstrap(bot) {
  return craftIronPickaxeFromInventory(bot);
}