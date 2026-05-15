function isAirLikeBlock(block) {
    return !!block && ["air", "cave_air", "void_air"].includes(block.name);
}

function isSolidSupportBlock(block) {
    return !!block && !isAirLikeBlock(block) && block.boundingBox === "block";
}

async function gotoGoalWithTimeout(bot, goal, label, timeoutMs = 12000) {
    let timeout = null;
    try {
        await Promise.race([
            bot.pathfinder.goto(goal),
            new Promise((_, reject) => {
                timeout = setTimeout(() => {
                    try {
                        bot.pathfinder.setGoal(null);
                    } catch (err) {}
                    reject(new Error(`Timed out while moving to ${label}`));
                }, timeoutMs);
            }),
        ]);
    } finally {
        if (timeout) clearTimeout(timeout);
    }
}

async function approachCraftingTable(bot, craftingTable) {
    if (!craftingTable) {
        throw new Error("Crafting table block is missing");
    }
    const distance = bot.entity.position.distanceTo(craftingTable.position.offset(0.5, 0.5, 0.5));
    if (distance > 4.5) {
        try {
            await gotoGoalWithTimeout(
                bot,
                new GoalNear(craftingTable.position.x, craftingTable.position.y, craftingTable.position.z, 2),
                "crafting table",
                12000
            );
        } catch (err) {
            await gotoLookAtBlockWithTimeout(craftingTable, "crafting table", 12000);
        }
    }
    try {
        await bot.lookAt(craftingTable.position.offset(0.5, 0.5, 0.5), true);
    } catch (err) {}
    return craftingTable;
}

function findNearbyCraftingTablePlacement(bot, maxRadius = 2) {
    const base = bot.entity.position.floored();
    const candidates = [];
    for (let radius = 1; radius <= maxRadius; radius++) {
        for (let dx = -radius; dx <= radius; dx++) {
            for (let dz = -radius; dz <= radius; dz++) {
                if (Math.abs(dx) !== radius && Math.abs(dz) !== radius) continue;
                candidates.push(base.offset(dx, 0, dz));
                candidates.push(base.offset(dx, -1, dz));
            }
        }
    }
    for (const pos of candidates) {
        const block = bot.blockAt(pos);
        const above = bot.blockAt(pos.offset(0, 1, 0));
        const below = bot.blockAt(pos.offset(0, -1, 0));
        if (!isAirLikeBlock(block)) continue;
        if (!isAirLikeBlock(above)) continue;
        if (!isSolidSupportBlock(below)) continue;
        return pos;
    }
    return null;
}

async function ensureNearbyCraftingTable(bot) {
    const nearbyCraftingTable = bot.findBlock({
        matching: mcData.blocksByName.crafting_table.id,
        maxDistance: 5,
    });
    if (nearbyCraftingTable) {
        return await approachCraftingTable(bot, nearbyCraftingTable);
    }

    const craftingTableItem = bot.inventory.findInventoryItem(mcData.itemsByName.crafting_table.id, null);
    if (craftingTableItem) {
        const placePos = findNearbyCraftingTablePlacement(bot, 2);
        if (!placePos) {
            throw new Error("No safe nearby position to place crafting_table");
        }
        await placeItem(bot, "crafting_table", placePos);
        const placedTable = bot.blockAt(placePos);
        if (!placedTable || placedTable.name !== "crafting_table") {
            throw new Error("Failed to confirm nearby placed crafting table");
        }
        return await approachCraftingTable(bot, placedTable);
    }

    let craftingTable = bot.findBlock({
        matching: mcData.blocksByName.crafting_table.id,
        maxDistance: 32,
    });
    if (!craftingTable) {
        throw new Error("No crafting table nearby or in inventory");
    }
    return await approachCraftingTable(bot, craftingTable);
}

function failedCraftFeedback(bot, name, item, craftingTable) {
    const recipes = bot.recipesAll(item.id, null, craftingTable);
    if (!recipes.length) {
        throw new Error(`No crafting table nearby`);
    } else {
        const recipes = bot.recipesAll(
            item.id,
            null,
            mcData.blocksByName.crafting_table.id
        );
        var min = 999;
        var min_recipe = null;
        for (const recipe of recipes) {
            const delta = recipe.delta;
            var missing = 0;
            for (const delta_item of delta) {
                if (delta_item.count < 0) {
                    const inventory_item = bot.inventory.findInventoryItem(
                        mcData.items[delta_item.id].name,
                        null
                    );
                    if (!inventory_item) {
                        missing += -delta_item.count;
                    } else {
                        missing += Math.max(
                            -delta_item.count - inventory_item.count,
                            0
                        );
                    }
                }
            }
            if (missing < min) {
                min = missing;
                min_recipe = recipe;
            }
        }
        const delta = min_recipe.delta;
        let message = "";
        for (const delta_item of delta) {
            if (delta_item.count < 0) {
                const inventory_item = bot.inventory.findInventoryItem(
                    mcData.items[delta_item.id].name,
                    null
                );
                if (!inventory_item) {
                    message += ` ${-delta_item.count} more ${
                        mcData.items[delta_item.id].name
                    }, `;
                } else if (inventory_item.count < -delta_item.count) {
                    message += `${
                        -delta_item.count - inventory_item.count
                    } more ${mcData.items[delta_item.id].name}`;
                }
            }
        }
        bot.chat(`I cannot make ${name} because I need: ${message}`);
    }
}
