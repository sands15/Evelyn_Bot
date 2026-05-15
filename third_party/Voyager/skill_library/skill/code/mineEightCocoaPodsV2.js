async function mineEightCocoaPods(bot) {
  const requiredBeans = 8;
  const cocoaBeansItem = mcData.itemsByName["cocoa_beans"];
  const cocoaBlock = mcData.blocksByName["cocoa"];
  function cocoaBeanCount() {
    return bot.inventory.count(cocoaBeansItem.id, null);
  }
  async function mineNearbyCocoaPass(maxAttempts) {
    for (let i = 0; i < maxAttempts; i++) {
      if (cocoaBeanCount() >= requiredBeans) return true;
      const cocoa = bot.findBlock({
        matching: cocoaBlock.id,
        maxDistance: 32
      });
      if (!cocoa) return false;
      await mineBlock(bot, "cocoa", 1);
    }
    return cocoaBeanCount() >= requiredBeans;
  }
  if (cocoaBeanCount() >= requiredBeans) return;
  await mineNearbyCocoaPass(requiredBeans);
  if (cocoaBeanCount() >= requiredBeans) return;
  const probeDirections = [new Vec3(1, 0, 1), new Vec3(-1, 0, 1)];
  for (const direction of probeDirections) {
    const foundCocoa = await exploreUntil(bot, direction, 15, () => {
      return bot.findBlock({
        matching: cocoaBlock.id,
        maxDistance: 32
      });
    });
    if (foundCocoa) {
      await mineNearbyCocoaPass(requiredBeans);
      if (cocoaBeanCount() >= requiredBeans) return;
    }
  }
  throw new Error("LOCAL_SEARCH_EXHAUSTED: could not find enough cocoa pods nearby in the current jungle area.");
}