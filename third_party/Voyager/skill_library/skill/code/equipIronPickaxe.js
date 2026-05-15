function countItem(bot, name) {
  const item = mcData.itemsByName[name];
  return item ? bot.inventory.count(item.id, null) : 0;
}

async function equipIronPickaxe(bot) {
  const ironPickaxeItem = mcData.itemsByName["iron_pickaxe"];
  const equipped = bot.inventory.slots[bot.getEquipmentDestSlot("hand")];
  if (equipped && equipped.type === ironPickaxeItem.id) return;
  let ironPickaxe = bot.inventory.findInventoryItem(ironPickaxeItem.id);
  if (ironPickaxe) {
    await bot.equip(ironPickaxe, "hand");
    return;
  }
  if (countItem(bot, "iron_ingot") < 3) {
    const missingIngots = 3 - countItem(bot, "iron_ingot");
    if (countItem(bot, "raw_iron") < missingIngots) {
      await mineBlock(bot, "iron_ore", missingIngots - countItem(bot, "raw_iron"));
    }
    if (countItem(bot, "raw_iron") < missingIngots) {
      throw new Error("Failed to obtain enough raw_iron for iron_pickaxe.");
    }
    if (countItem(bot, "coal") < missingIngots) {
      throw new Error("Need coal fuel to smelt raw_iron.");
    }
    await smeltItem(bot, "raw_iron", "coal", missingIngots);
  }
  if (countItem(bot, "iron_ingot") < 3) {
    throw new Error("Failed to obtain 3 iron_ingot.");
  }
  if (countItem(bot, "stick") < 2) {
    throw new Error("Need 2 sticks to craft iron_pickaxe.");
  }
  await craftItem(bot, "iron_pickaxe", 1);
  ironPickaxe = bot.inventory.findInventoryItem(ironPickaxeItem.id);
  if (!ironPickaxe) {
    throw new Error("Failed to craft iron_pickaxe.");
  }
  await bot.equip(ironPickaxe, "hand");
}