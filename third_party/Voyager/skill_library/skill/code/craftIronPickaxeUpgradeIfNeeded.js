async function craftIronPickaxeUpgradeIfNeeded(bot) {
  // Goal check
  const ironPickaxe = mcData.itemsByName["iron_pickaxe"];
  const ironIngot = mcData.itemsByName["iron_ingot"];
  const stick = mcData.itemsByName["stick"];
  if (!ironPickaxe) throw new Error("MISSING_ITEM: iron_pickaxe");
  if (!ironIngot || !stick) throw new Error("MISSING_ITEM: iron_ingot or stick");
  if (bot.inventory.count(ironPickaxe.id, null) >= 1) return "ALREADY_DONE";

  // Inventory sufficiency check
  if (bot.inventory.count(ironIngot.id, null) < 3) throw new Error("SCARCITY: need 3 iron_ingot to craft iron_pickaxe");
  if (bot.inventory.count(stick.id, null) < 2) throw new Error("SCARCITY: need 2 stick to craft iron_pickaxe");

  // Try crafting directly first (craftItem handles table setup when possible)
  try {
    await craftItem(bot, "iron_pickaxe", 1);
    if (bot.inventory.count(ironPickaxe.id, null) >= 1) return "DONE";
  } catch (e) {
    // Fall through to bounded fallback that ensures a nearby crafting table
  }

  // Fallback: place a crafting table adjacent, then retry craftItem
  const craftingTableId = mcData.blocksByName["crafting_table"]?.id;
  if (!craftingTableId) throw new Error("MISSING_BLOCK: crafting_table");

  // Ensure we have a crafting table item; craftItem can also craft it, but keep it bounded.
  const craftingTableItem = mcData.itemsByName["crafting_table"];
  if (!craftingTableItem) throw new Error("MISSING_ITEM: crafting_table");
  if (bot.inventory.count(craftingTableItem.id, null) < 1) {
    // Need planks to make a crafting table; short bounded gather
    await searchAndHarvest(bot, {
      goalType: "wood",
      quantity: 2,
      maxSearchBudgetSec: 10
    });
    // craftItem will place/use crafting station if needed
    // (If planks/log are available, this is typically fast; if not, higher-level planner will handle)
    await craftItem(bot, "crafting_table", 1);
  }
  const placePosCandidates = [bot.entity.position.offset(1, 0, 0), bot.entity.position.offset(-1, 0, 0), bot.entity.position.offset(0, 0, 1), bot.entity.position.offset(0, 0, -1)];
  let placed = false;
  for (const p of placePosCandidates) {
    const block = bot.blockAt(p);
    if (block && block.name === "air") {
      try {
        await placeItem(bot, "crafting_table", p);
        placed = true;
        break;
      } catch (_) {
        // try next adjacent spot
      }
    }
  }
  if (!placed) {
    // If placement failed (e.g., all sides obstructed), do a short local wood search to find a more open spot.
    await searchAndHarvest(bot, {
      goalType: "wood",
      quantity: 1,
      maxSearchBudgetSec: 8
    });
  }

  // Retry crafting once table should exist nearby
  await craftItem(bot, "iron_pickaxe", 1);
  if (bot.inventory.count(ironPickaxe.id, null) < 1) {
    throw new Error("CRAFT_FAILED: iron_pickaxe not found in inventory after crafting");
  }
  return "DONE";
}