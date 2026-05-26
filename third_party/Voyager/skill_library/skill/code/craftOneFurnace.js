async function craftOneFurnace(bot) {
  // Assumptions: mcData, GoalLookAtBlock, GoalNear/GoalPlaceBlock, placeItem/craftItem exist in runtime as per prompt.
  // Task: ensure we end up with at least 1 furnace crafted.

  const getCount = name => {
    const item = mcData.itemsByName[name];
    if (!item) return 0;
    return bot.inventory.count(item.id, null);
  };

  // If already have a furnace item, done.
  if (getCount("furnace") >= 1) return {
    success: true,
    reason: "ALREADY_HAVE_FURNACE_ITEM"
  };

  // Furnace recipe in standard versions: 3 cobblestone + 1 furnace block? (actually: 8 cobblestone + 1? no)
  // In Minecraft crafting: Furnace = 8 cobblestone + 1 (none). It is 8 cobblestone only.
  // We'll collect cobblestone to satisfy recipe; craftItem should handle crafting table placement as needed.
  const cobblestoneNeeded = Math.max(0, 8 - getCount("cobblestone"));

  // Collect cobblestone if insufficient.
  if (cobblestoneNeeded > 0) {
    // Prefer mining nearby stone/cobblestone-like sources? We have "stone_axe"/pickaxe, but use mineBlock on "stone".
    // If cobblestone isn't present enough, mine stone to get cobblestone? Not guaranteed.
    // Better: if we have crafting_table and furnace nearby, mining cobblestone directly is possible,
    // but we only have helper mineBlock with a name. We'll mine "cobblestone" if visible; else mine "stone".
    const hasCobbleNearby = bot.findBlock({
      matching: mcData.blocksByName["cobblestone"].id,
      maxDistance: 32
    });
    if (hasCobbleNearby) {
      await mineBlock(bot, "cobblestone", cobblestoneNeeded);
    } else {
      // fallback: mine stone and rely on furnace/smelting later is not relevant; however stone != cobblestone.
      // Most tasks using cobblestone assume it's available. If not, fail fast to avoid wrong mining.
      const hasStoneNearby = bot.findBlock({
        matching: mcData.blocksByName["stone"].id,
        maxDistance: 32
      });
      if (!hasStoneNearby) {
        throw new Error("LOCAL_SEARCH_EXHAUSTED: NO_COBBLESTONE_OR_STONE_NEARBY");
      }
      await mineBlock(bot, "stone", cobblestoneNeeded);
      // Note: stone may not be directly convertible to cobblestone; but user environment already has cobblestone (91).
      // This path is only a fallback when cobblestone count is low and some stone exists nearby.
    }
    if (getCount("cobblestone") < 8) {
      throw new Error("INSUFFICIENT_COBBLESTONE_FOR_FURNACE");
    }
  }

  // Craft furnace (expects cobblestone >= 8; craftItem handles crafting table placement/approach).
  if (getCount("cobblestone") >= 8) {
    await craftItem(bot, "furnace", 1);
  }

  // Re-check completion by inventory.
  if (getCount("furnace") < 1) {
    throw new Error("FURNACE_CRAFT_FAILED");
  }
  return {
    success: true,
    reason: "FURNACE_CRAFTED"
  };
}