async function mineEightCocoaPods(bot) {
  const requiredBeans = 8;
  const cocoaBeans = mcData.itemsByName["cocoa_beans"];
  const cocoaBlock = mcData.blocksByName["cocoa"];
  function hasEnoughCocoaBeans() {
    return bot.inventory.count(cocoaBeans.id, null) >= requiredBeans;
  }
  async function mineNearbyCocoa(maxAttempts) {
    for (let i = 0; i < maxAttempts; i++) {
      if (hasEnoughCocoaBeans()) return true;
      const cocoa = bot.findBlock({
        matching: cocoaBlock.id,
        maxDistance: 32
      });
      if (!cocoa) return false;
      await mineBlock(bot, "cocoa", 1);
      if (hasEnoughCocoaBeans()) return true;
    }
    return hasEnoughCocoaBeans();
  }
  if (hasEnoughCocoaBeans()) return;
  await mineNearbyCocoa(requiredBeans);
  if (hasEnoughCocoaBeans()) return;
  const directions = [new Vec3(1, 0, 1), new Vec3(-1, 0, 1)];
  for (const direction of directions) {
    const foundCocoa = await exploreUntil(bot, direction, 15, () => {
      return bot.findBlock({
        matching: cocoaBlock.id,
        maxDistance: 32
      });
    });
    if (foundCocoa) {
      await mineNearbyCocoa(requiredBeans);
      if (hasEnoughCocoaBeans()) return;
    }
  }
  throw new Error("LOCAL_SEARCH_EXHAUSTED: could not find enough reachable cocoa pods nearby in the current jungle area.");
}