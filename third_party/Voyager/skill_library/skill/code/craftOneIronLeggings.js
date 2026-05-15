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

function countAnyPlanks(bot) {
  const plankNames = ["oak_planks", "birch_planks", "spruce_planks", "jungle_planks", "acacia_planks", "dark_oak_planks", "mangrove_planks", "cherry_planks"];
  return plankNames.reduce((sum, name) => sum + countItem(bot, name), 0);
}

async function ensureCraftingTableNearby(bot) {
  let table = bot.findBlock({
    matching: mcData.blocksByName["crafting_table"].id,
    maxDistance: 32
  });
  if (table) return;
  if (countItem(bot, "crafting_table") < 1) {
    if (countAnyPlanks(bot) < 4) {
      if (countItem(bot, "oak_log") > 0) {
        await craftItem(bot, "oak_planks", 1);
      } else if (countItem(bot, "birch_log") > 0) {
        await craftItem(bot, "birch_planks", 1);
      } else {
        const result = await searchAndHarvest(bot, {
          goalType: "wood",
          quantity: 1,
          maxSearchBudgetSec: 24
        });
        if (!result.success) throw new Error(result.reason || "WOOD_SEARCH_FAILED");
        if (countItem(bot, "oak_log") > 0) {
          await craftItem(bot, "oak_planks", 1);
        } else if (countItem(bot, "birch_log") > 0) {
          await craftItem(bot, "birch_planks", 1);
        }
      }
    }
    await craftItem(bot, "crafting_table", 1);
  }
  const placePos = findNearbyPlacePosition(bot);
  if (!placePos) throw new Error("No valid nearby position to place crafting_table.");
  await placeItem(bot, "crafting_table", placePos);
}

async function craftOneIronLeggings(bot) {
  if (countItem(bot, "iron_leggings") >= 1) return;
  await ensureCraftingTableNearby(bot);
  if (countItem(bot, "iron_leggings") >= 1) return;
  const missingIngots = 7 - countItem(bot, "iron_ingot");
  if (missingIngots > 0) {
    if (countItem(bot, "raw_iron") < missingIngots) {
      throw new Error("Need more raw_iron to smelt enough iron_ingot for iron_leggings.");
    }
    if (countItem(bot, "coal") < missingIngots) {
      throw new Error("Need enough coal to smelt iron for iron_leggings.");
    }
    let furnace = bot.findBlock({
      matching: mcData.blocksByName["furnace"].id,
      maxDistance: 32
    });
    if (!furnace) {
      if (countItem(bot, "furnace") < 1) {
        if (countItem(bot, "cobblestone") < 8) {
          await mineBlock(bot, "stone", 8 - countItem(bot, "cobblestone"));
        }
        await craftItem(bot, "furnace", 1);
      }
      const furnacePos = findNearbyPlacePosition(bot);
      if (!furnacePos) throw new Error("No valid nearby position to place furnace.");
      await placeItem(bot, "furnace", furnacePos);
      furnace = bot.findBlock({
        matching: mcData.blocksByName["furnace"].id,
        maxDistance: 32
      });
      if (!furnace) throw new Error("Failed to place furnace.");
    }
    await smeltItem(bot, "raw_iron", "coal", missingIngots);
  }
  if (countItem(bot, "iron_ingot") < 7) {
    throw new Error("Failed to obtain 7 iron_ingot for iron_leggings.");
  }
  await ensureCraftingTableNearby(bot);
  await craftItem(bot, "iron_leggings", 1);
  if (countItem(bot, "iron_leggings") < 1) {
    throw new Error("Failed to craft iron_leggings.");
  }
}