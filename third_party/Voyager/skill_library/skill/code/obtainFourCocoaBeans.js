async function obtainFourCocoaBeans(bot) {
  const required = 4;
  const cocoaBeans = mcData.itemsByName["cocoa_beans"];
  function countCocoaBeans() {
    return bot.inventory.count(cocoaBeans.id, null);
  }
  if (countCocoaBeans() >= required) return;
  for (let i = 0; i < required; i++) {
    if (countCocoaBeans() >= required) return;
    const nearbyCocoa = bot.findBlock({
      matching: mcData.blocksByName["cocoa"].id,
      maxDistance: 32
    });
    if (!nearbyCocoa) break;
    await mineBlock(bot, "cocoa", 1);
  }
  if (countCocoaBeans() >= required) return;
  const foundCocoa = await exploreUntil(bot, new Vec3(1, 0, 1), 60, () => {
    return bot.findBlock({
      matching: mcData.blocksByName["cocoa"].id,
      maxDistance: 32
    });
  });
  if (!foundCocoa) {
    throw new Error("Could not find cocoa pods.");
  }
  for (let i = 0; i < required; i++) {
    if (countCocoaBeans() >= required) return;
    const nearbyCocoa = bot.findBlock({
      matching: mcData.blocksByName["cocoa"].id,
      maxDistance: 32
    });
    if (!nearbyCocoa) break;
    await mineBlock(bot, "cocoa", 1);
  }
  if (countCocoaBeans() < required) {
    throw new Error("Failed to obtain 4 cocoa_beans.");
  }
}