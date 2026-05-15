async function mineEightCocoaPods(bot) {
  const required = 8;
  const cocoaBeans = mcData.itemsByName["cocoa_beans"];
  const cocoaBlock = mcData.blocksByName["cocoa"];
  function cocoaBeanCount() {
    return bot.inventory.count(cocoaBeans.id, null);
  }
  if (cocoaBeanCount() >= required) return;
  for (let i = 0; i < required; i++) {
    if (cocoaBeanCount() >= required) return;
    const nearbyCocoa = bot.findBlock({
      matching: cocoaBlock.id,
      maxDistance: 32
    });
    if (!nearbyCocoa) break;
    await mineBlock(bot, "cocoa", 1);
  }
  if (cocoaBeanCount() >= required) return;
  const foundCocoa = await exploreUntil(bot, new Vec3(1, 0, 1), 60, () => {
    return bot.findBlock({
      matching: cocoaBlock.id,
      maxDistance: 32
    });
  });
  if (!foundCocoa) {
    throw new Error("Could not find cocoa pods nearby.");
  }
  for (let i = 0; i < required; i++) {
    if (cocoaBeanCount() >= required) return;
    const nearbyCocoa = bot.findBlock({
      matching: cocoaBlock.id,
      maxDistance: 32
    });
    if (!nearbyCocoa) break;
    await mineBlock(bot, "cocoa", 1);
  }
  if (cocoaBeanCount() < required) {
    throw new Error("Failed to mine 8 cocoa pods.");
  }
}