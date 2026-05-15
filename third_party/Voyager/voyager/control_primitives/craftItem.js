async function craftItem(bot, name, count = 1) {
    if (typeof name !== "string") {
        throw new Error("name for craftItem must be a string");
    }
    if (typeof count !== "number") {
        throw new Error("count for craftItem must be a number");
    }
    const itemByName = mcData.itemsByName[name];
    if (!itemByName) {
        throw new Error(`No item named ${name}`);
    }

    const inventoryRecipe = bot.recipesFor(itemByName.id, null, 1, null)[0];
    if (inventoryRecipe) {
        bot.chat(`I can make ${name} without a crafting table`);
        try {
            await craftWithRecovery(inventoryRecipe, count, null, `inventory craft ${name}`);
            bot.chat(`I did the recipe for ${name} ${count} times`);
            return;
        } catch (err) {
            bot.chat(`I cannot do the inventory recipe for ${name} ${count} times`);
        }
    }

    let craftingTable = null;
    try {
        craftingTable = await ensureNearbyCraftingTable(bot);
    } catch (err) {
        bot.chat(`Crafting table setup failed for ${name}: ${err.message}`);
    }
    const recipe = bot.recipesFor(itemByName.id, null, 1, craftingTable)[0];
    if (recipe) {
        bot.chat(`I can make ${name}`);
        try {
            await craftWithRecovery(recipe, count, craftingTable, `craft ${name}`);
            bot.chat(`I did the recipe for ${name} ${count} times`);
        } catch (err) {
            bot.chat(`I cannot do the recipe for ${name} ${count} times`);
        }
    } else {
        failedCraftFeedback(bot, name, itemByName, craftingTable);
        _craftItemFailCount++;
        if (_craftItemFailCount > 10) {
            throw new Error(
                "craftItem failed too many times, check chat log to see what happened"
            );
        }
    }
}
