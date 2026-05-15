async function mineEightCocoaPods(bot) {
  const cocoaItem = mcData.itemsByName["cocoa_beans"];
  const cocoaBlock = mcData.blocksByName["cocoa"];
  const required = 8;
  function cocoaBeanCount() {
    return bot.inventory.count(cocoaItem.id, null);
  }
  if (cocoaBeanCount() >= required) return;
  const startCount = cocoaBeanCount();
  async function mineNearbyCocoaPods(maxPods) {
    for (let i = 0; i < maxPods; i++) {
      if (cocoaBeanCount() - startCount >= required || cocoaBeanCount() >= required) return true;
      const cocoa = bot.findBlock({
        matching: cocoaBlock.id,
        maxDistance: 32
      });
      if (!cocoa) return false;
      await mineBlock(bot, "cocoa", 1);
    }
    return cocoaBeanCount() - startCount >= required || cocoaBeanCount() >= required;
  }
  if (await mineNearbyCocoaPods(required)) return;
  const directions = [new Vec3(1, 0, 1), new Vec3(-1, 0, 1)];
  for (const direction of directions) {
    const found = await exploreUntil(bot, direction, 15, () => {
      return bot.findBlock({
        matching: cocoaBlock.id,
        maxDistance: 32
      });
    });
    if (found && (await mineNearbyCocoaPods(required))) return;
  }
  if (cocoaBeanCount() >= required) return;
  throw new Error("LOCAL_SEARCH_EXHAUSTED: could not find enough cocoa pods nearby in the current jungle area.");
}