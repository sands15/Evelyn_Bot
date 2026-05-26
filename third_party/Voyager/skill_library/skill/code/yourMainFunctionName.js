async function craftFurnaceImmediately(bot) {
  // Re-check for inventory sufficiency first (task completion check)
  const furnaceId = bot.mcData?.itemsByName?.furnace?.id ?? bot.registry?.itemsByName?.furnace?.id; // extra fallback if mcData not ready
  if (!furnaceId) {
    throw new Error("MISSING_DATA: furnace item id not available in mcData/registry.");
  }
  const currentFurnaceCount = bot.inventory.count(furnaceId, null);
  if (currentFurnaceCount >= 1) return {
    success: true
  };
  const cobbleId = bot.mcData?.itemsByName?.cobblestone?.id ?? bot.registry?.itemsByName?.cobblestone?.id;
  if (!cobbleId) throw new Error("MISSING_DATA: cobblestone item id not available.");

  // Need 8 cobblestone to craft 1 furnace
  const haveCobble = bot.inventory.count(cobbleId, null);
  const needCobble = Math.max(0, 8 - haveCobble);
  if (needCobble > 0) {
    // Prefer nearby cobblestone; otherwise mine cobblestone directly if visible; bounded nearby search.
    const cobbleBlock = bot.findBlock({
      matching: bot.mcData.blocksByName["cobblestone"].id,
      maxDistance: 32
    });
    if (cobbleBlock) {
      await mineBlock(bot, "cobblestone", needCobble);
    } else {
      // Fallback: try to quickly locate cobblestone around via up to two short probes
      let lastErr = null;
      for (let i = 0; i < 2; i++) {
        const found = await exploreUntil(bot, new Vec3(1, 0, 0), 15, () => bot.findBlock({
          matching: bot.mcData.blocksByName["cobblestone"].id,
          maxDistance: 32
        }));
        const cobbleNow = bot.findBlock({
          matching: bot.mcData.blocksByName["cobblestone"].id,
          maxDistance: 32
        });
        if (cobbleNow) break;
        lastErr = new Error("LOCAL_SEARCH_EXHAUSTED: cobblestone not nearby.");
      }
      // Re-check after probes
      const haveCobble2 = bot.inventory.count(cobbleId, null);
      const remaining = Math.max(0, 8 - haveCobble2);
      if (remaining > 0) {
        throw lastErr || new Error("LOCAL_SEARCH_EXHAUSTED: insufficient cobblestone for furnace.");
      }
    }
  }

  // Craft furnace using craftItem primitive (handles nearby crafting table setup)
  await craftItem(bot, "furnace", 1);

  // Final completion check
  const afterCount = bot.inventory.count(furnaceId, null);
  if (afterCount >= 1) return {
    success: true
  };
  throw new Error("FURNACE_CRAFT_FAILED: furnace not in inventory after craftItem.");
}

async function yourMainFunctionName(bot) {
  return craftFurnaceImmediately(bot);
}