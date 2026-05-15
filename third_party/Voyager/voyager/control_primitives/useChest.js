async function getItemFromChest(bot, chestPosition, itemsToGet) {
    // return if chestPosition is not Vec3
    if (!(chestPosition instanceof Vec3)) {
        bot.chat("chestPosition for getItemFromChest must be a Vec3");
        return;
    }
    const chestBlock = await moveToChest(bot, chestPosition);
    const chest = await openContainerWithRecovery(chestBlock, "chest");
    for (const name in itemsToGet) {
        const itemByName = mcData.itemsByName[name];
        if (!itemByName) {
            bot.chat(`No item named ${name}`);
            continue;
        }

        const item = chest.findContainerItem(itemByName.id);
        if (!item) {
            bot.chat(`I don't see ${name} in this chest`);
            continue;
        }
        try {
            await chest.withdraw(item.type, null, itemsToGet[name]);
        } catch (err) {
            bot.chat(`Not enough ${name} in chest.`);
        }
    }
    await closeChest(bot, chestBlock, chest);
}

async function depositItemIntoChest(bot, chestPosition, itemsToDeposit) {
    // return if chestPosition is not Vec3
    if (!(chestPosition instanceof Vec3)) {
        throw new Error(
            "chestPosition for depositItemIntoChest must be a Vec3"
        );
    }
    const chestBlock = await moveToChest(bot, chestPosition);
    const chest = await openContainerWithRecovery(chestBlock, "chest");
    for (const name in itemsToDeposit) {
        const itemByName = mcData.itemsByName[name];
        if (!itemByName) {
            bot.chat(`No item named ${name}`);
            continue;
        }
        const item = bot.inventory.findInventoryItem(itemByName.id);
        if (!item) {
            bot.chat(`No ${name} in inventory`);
            continue;
        }
        try {
            await chest.deposit(item.type, null, itemsToDeposit[name]);
        } catch (err) {
            bot.chat(`Not enough ${name} in inventory.`);
        }
    }
    await closeChest(bot, chestBlock, chest);
}

async function checkItemInsideChest(bot, chestPosition) {
    // return if chestPosition is not Vec3
    if (!(chestPosition instanceof Vec3)) {
        throw new Error(
            "chestPosition for depositItemIntoChest must be a Vec3"
        );
    }
    const chestBlock = await moveToChest(bot, chestPosition);
    const chest = await openContainerWithRecovery(chestBlock, "chest");
    await closeChest(bot, chestBlock, chest);
}

async function moveToChest(bot, chestPosition) {
    if (!(chestPosition instanceof Vec3)) {
        throw new Error(
            "chestPosition for depositItemIntoChest must be a Vec3"
        );
    }
    if (chestPosition.distanceTo(bot.entity.position) > 32) {
        bot.chat(
            `/tp ${chestPosition.x} ${chestPosition.y} ${chestPosition.z}`
        );
        await bot.waitForTicks(20);
    }
    let chestBlock = await resolveChestBlock(bot, chestPosition);
    await gotoLookAtBlockWithTimeout(chestBlock, "chest");
    chestBlock = await resolveChestBlock(bot, chestPosition);
    return chestBlock;
}

async function resolveChestBlock(bot, chestPosition, retries = 1) {
    for (let attempt = 0; attempt <= retries; attempt++) {
        const chestBlock = bot.blockAt(chestPosition);
        if (chestBlock && ["chest", "trapped_chest"].includes(chestBlock.name)) {
            return chestBlock;
        }
        if (attempt < retries) {
            await bot.waitForTicks(8);
            continue;
        }
        bot.emit("removeChest", chestPosition);
        throw new Error(
            `No chest at ${chestPosition}, it is ${chestBlock ? chestBlock.name : "missing"}`
        );
    }
}

async function listItemsInChest(bot, chestBlock) {
    const chest = await openContainerWithRecovery(chestBlock, "chest");
    const items = chest.containerItems();
    if (items.length > 0) {
        const itemNames = items.reduce((acc, obj) => {
            if (acc[obj.name]) {
                acc[obj.name] += obj.count;
            } else {
                acc[obj.name] = obj.count;
            }
            return acc;
        }, {});
        bot.emit("closeChest", itemNames, chestBlock.position);
    } else {
        bot.emit("closeChest", {}, chestBlock.position);
    }
    return chest;
}

async function closeChest(bot, chestBlock, chest = null) {
    try {
        if (chest) {
            const items = chest.containerItems();
            if (items.length > 0) {
                const itemNames = items.reduce((acc, obj) => {
                    if (acc[obj.name]) {
                        acc[obj.name] += obj.count;
                    } else {
                        acc[obj.name] = obj.count;
                    }
                    return acc;
                }, {});
                bot.emit("closeChest", itemNames, chestBlock.position);
            } else {
                bot.emit("closeChest", {}, chestBlock.position);
            }
            if (typeof chest.close === "function") {
                await chest.close();
            }
            return;
        }
        const listedChest = await listItemsInChest(bot, chestBlock);
        await listedChest.close();
    } catch (err) {
        if (bot.currentWindow) {
            try {
                bot.closeWindow(bot.currentWindow);
            } catch (closeErr) {}
        }
    }
}

function itemByName(items, name) {
    for (let i = 0; i < items.length; ++i) {
        const item = items[i];
        if (item && item.name === name) return item;
    }
    return null;
}
