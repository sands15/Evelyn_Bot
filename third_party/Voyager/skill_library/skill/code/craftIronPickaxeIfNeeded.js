async function craftIronPickaxeIfNeeded(bot) {
  // Check completion first
  const ironPickaxe = mcData.itemsByName["iron_pickaxe"];
  if (!ironPickaxe) throw new Error("MISSING_ITEM: iron_pickaxe");
  if (bot.inventory.count(ironPickaxe.id, null) >= 1) return;

  // Verify we have enough materials; if not, do a minimal prerequisite (no long chains)
  const ironIngot = mcData.itemsByName["iron_ingot"];
  const stick = mcData.itemsByName["stick"];
  if (!ironIngot || !stick) throw new Error("MISSING_ITEM: iron_ingot or stick");

  const needIngots = 3 - bot.inventory.count(ironIngot.id, null);
  const needSticks = 2 - bot.inventory.count(stick.id, null);

  if (needIngots > 0 || needSticks > 0) {
    // Minimal prerequisite: gather sticks/iron to reach crafting requirements.
    // Prefer not to overextend at night: do only short local recovery/harvest attempts.
    // If sticks are missing, try a quick local wood/leaf harvesting via wood search helper.
    if (needSticks > 0) {
      // Sticks usually come from logs/planks; use the existing wood search helper to avoid long wandering.
      await searchAndHarvest(bot, { goalType: "wood", quantity: 2, maxSearchBudgetSec: 12 });
      // Recompute after harvest
      const ingotsNow = bot.inventory.count(ironIngot.id, null);
      const sticksNow = bot.inventory.count(stick.id, null);
      // If still missing sticks, give a short bounded fallback via nearby exploreUntil
      if (sticksNow < 2) {
        // We expect logs/planks to be nearby given nearby blocks from context; avoid multiple probes.
        // Fail cleanly if still short.
        throw new Error("LOCAL_SEARCH_EXHAUSTED: not enough sticks nearby to craft iron_pickaxe");
      }
    }

    if (needIngots > 0) {
      // If raw_iron exists, smelt minimal amount using existing furnace requirement in smeltItem.
      // Otherwise mine iron ore locally as a short prerequisite.
      if (bot.inventory.count(mcData.itemsByName["raw_iron"]?.id ?? -1, null) >= needIngots) {
        // smeltItem requires a placed furnace; only attempt if furnace block exists nearby.
        const furnaceBlock = bot.findBlock({
          matching: mcData.blocksByName["furnace"].id,
          maxDistance: 32
        });
        if (furnaceBlock) {
          // Need fuel; prefer coal if available, else fail cleanly.
          if (bot.inventory.count(mcData.itemsByName["coal"].id, null) < needIngots) {
            throw new Error("SCARCITY: need coal fuel to smelt raw_iron for iron_pickaxe");
          }
          await smeltItem(bot, "raw_iron", "coal", needIngots);
        } else {
          throw new Error("MISSING: furnace not found nearby for smelting raw_iron");
        }
      } else {
        // Mine iron ore minimally (short local action). If none nearby, fail cleanly.
        await searchForOre(bot, { oreType: "iron", maxSearchBudgetSec: 18 });
      }
    }
  }

  // Final sufficiency check before crafting
  const finalIngots = bot.inventory.count(ironIngot.id, null);
  const finalSticks = bot.inventory.count(stick.id, null);
  if (finalIngots < 3) throw new Error("SCARCITY: need 3 iron_ingot to craft iron_pickaxe");
  if (finalSticks < 2) throw new Error("SCARCITY: need 2 sticks to craft iron_pickaxe");

  // Craft
  await craftItem(bot, "iron_pickaxe", 1);

  // Confirm
  if (bot.inventory.count(ironPickaxe.id, null) < 1) {
    throw new Error("CRAFT_FAILED: iron_pickaxe not found in inventory after crafting");
  }
}

async function craftIronPickaxeIfNeeded(bot) {
  // Check completion first
  const ironPickaxe = mcData.itemsByName["iron_pickaxe"];
  if (!ironPickaxe) throw new Error("MISSING_ITEM: iron_pickaxe");
  if (bot.inventory.count(ironPickaxe.id, null) >= 1) return;

  // Verify we have enough materials; if not, do a minimal prerequisite (no long chains)
  const ironIngot = mcData.itemsByName["iron_ingot"];
  const stick = mcData.itemsByName["stick"];
  if (!ironIngot || !stick) throw new Error("MISSING_ITEM: iron_ingot or stick");

  const needIngots = 3 - bot.inventory.count(ironIngot.id, null);
  const needSticks = 2 - bot.inventory.count(stick.id, null);

  if (needIngots > 0 || needSticks > 0) {
    if (needSticks > 0) {
      await searchAndHarvest(bot, { goalType: "wood", quantity: 2, maxSearchBudgetSec: 12 });
      const sticksNow = bot.inventory.count(stick.id, null);
      if (sticksNow < 2) {
        throw new Error("LOCAL_SEARCH_EXHAUSTED: not enough sticks nearby to craft iron_pickaxe");
      }
    }

    if (needIngots > 0) {
      const rawIronId = mcData.itemsByName["raw_iron"]?.id;
      const rawIronCount = rawIronId ? bot.inventory.count(rawIronId, null) : 0;

      if (rawIronCount >= needIngots) {
        const furnaceBlock = bot.findBlock({
          matching: mcData.blocksByName["furnace"].id,
          maxDistance: 32
        });
        if (!furnaceBlock) throw new Error("MISSING: furnace not found nearby for smelting raw_iron");

        const coalCount = bot.inventory.count(mcData.itemsByName["coal"].id, null);
        if (coalCount < needIngots) throw new Error("SCARCITY: need coal fuel to smelt raw_iron for iron_pickaxe");

        await smeltItem(bot, "raw_iron", "coal", needIngots);
      } else {
        await searchForOre(bot, { oreType: "iron", maxSearchBudgetSec: 18 });
      }
    }
  }

  const finalIngots = bot.inventory.count(ironIngot.id, null);
  const finalSticks = bot.inventory.count(stick.id, null);
  if (finalIngots < 3) throw new Error("SCARCITY: need 3 iron_ingot to craft iron_pickaxe");
  if (finalSticks < 2) throw new Error("SCARCITY: need 2 sticks to craft iron_pickaxe");

  await craftItem(bot, "iron_pickaxe", 1);

  if (bot.inventory.count(ironPickaxe.id, null) < 1) {
    throw new Error("CRAFT_FAILED: iron_pickaxe not found in inventory after crafting");
  }
}