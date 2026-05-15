async function mineEightCocoaPods(bot) {
  const required = 8;
  const cocoaBeansItem = mcData.itemsByName["cocoa_beans"];
  const cocoaBlock = mcData.blocksByName["cocoa"];
  function cocoaBeanCount() {
    return bot.inventory.count(cocoaBeansItem.id, null);
  }
  if (cocoaBeanCount() >= required) return;
  let podsMined = 0;
  async function mineNearbyCocoaPods(maxPods) {
    for (let i = 0; i < maxPods; i++) {
      if (podsMined >= required || cocoaBeanCount() >= required) return true;
      const cocoa = bot.findBlock({
        matching: cocoaBlock.id,
        maxDistance: 32
      });
      if (!cocoa) return false;
      await mineBlock(bot, "cocoa", 1);
      podsMined++;
    }
    return podsMined >= required || cocoaBeanCount() >= required;
  }
  if (await mineNearbyCocoaPods(required)) return;
  const directions = [new Vec3(1, 0, 1), new Vec3(-1, 0, -1)];
  for (const direction of directions) {
    if (podsMined >= required || cocoaBeanCount() >= required) return;
    const foundCocoa = await exploreUntil(bot, direction, 15, () => {
      return bot.findBlock({
        matching: cocoaBlock.id,
        maxDistance: 32
      });
    });
    if (foundCocoa) {
      const remaining = required - podsMined;
      if (await mineNearbyCocoaPods(remaining)) return;
    }
  }
  if (podsMined >= required || cocoaBeanCount() >= required) return;
  throw new Error("LOCAL_SEARCH_EXHAUSTED: could not find 8 cocoa pod blocks nearby in the current jungle area.");
}