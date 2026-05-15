async function mineSixteenGravel(bot) {
  const gravelItem = mcData.itemsByName["gravel"];
  function gravelCount() {
    return bot.inventory.count(gravelItem.id, null);
  }
  async function mineNearbyGravelOneAtATime(maxAttempts) {
    let failedAttempts = 0;
    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      if (gravelCount() >= 16) return true;
      const gravel = bot.findBlock({
        matching: block => block.name === "gravel",
        maxDistance: 32
      });
      if (!gravel) return false;
      try {
        await bot.pathfinder.goto(new GoalNear(gravel.position.x, gravel.position.y, gravel.position.z, 3));
        await mineBlock(bot, "gravel", 1);
        failedAttempts = 0;
      } catch (err) {
        failedAttempts++;
        await bot.waitForTicks(10);
        if (failedAttempts >= 3) return false;
      }
    }
    return gravelCount() >= 16;
  }
  if (gravelCount() >= 16) return;
  await mineNearbyGravelOneAtATime(12);
  if (gravelCount() >= 16) return;
  const foundGravel = await exploreUntil(bot, new Vec3(1, 0, 1), 60, () => {
    return bot.findBlock({
      matching: block => block.name === "gravel",
      maxDistance: 32
    });
  });
  if (!foundGravel) {
    throw new Error("Could not find gravel.");
  }
  await mineNearbyGravelOneAtATime(16);
  if (gravelCount() < 16) {
    throw new Error("Failed to mine 16 gravel.");
  }
}