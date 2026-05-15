async function mineBlock(bot, name, count = 1) {
    if (typeof name !== "string") {
        throw new Error(`name for mineBlock must be a string`);
    }
    if (typeof count !== "number") {
        throw new Error(`count for mineBlock must be a number`);
    }
    const blockByName = mcData.blocksByName[name];
    if (!blockByName) {
        throw new Error(`No block named ${name}`);
    }

    const blockPositions = bot.findBlocks({
        matching: [blockByName.id],
        maxDistance: 32,
        count: Math.max(8, count * 4),
    });
    if (blockPositions.length === 0) {
        bot.chat(`No ${name} nearby, please explore first`);
        _mineBlockFailCount++;
        if (_mineBlockFailCount > 10) {
            throw new Error(
                "mineBlock failed too many times, make sure you explore before calling mineBlock"
            );
        }
        return;
    }

    const targets = blockPositions
        .map((pos) => bot.blockAt(pos))
        .filter((block) => !!block)
        .sort((a, b) => bot.entity.position.distanceTo(a.position) - bot.entity.position.distanceTo(b.position));

    const fmtPos = (pos) => `(${pos.x},${pos.y},${pos.z})`;
    const logMineWarning = (message) => {
        console.warn(`[mineBlock:${name}] ${message}`);
    };

    let mined = 0;
    let lastError = null;

    const stopCurrentTask = async () => {
        try {
            await bot.collectBlock.cancelTask();
        } catch (cancelErr) {}
        try {
            bot.pathfinder.stop();
        } catch (stopErr) {}
    };

    const waitForPhysicsTicks = async (ticks) => {
        if (ticks <= 0) return;
        await new Promise((resolve) => {
            let remaining = ticks;
            const onTick = () => {
                remaining -= 1;
                if (remaining <= 0) {
                    bot.off("physicsTick", onTick);
                    resolve();
                }
            };
            bot.on("physicsTick", onTick);
        });
    };

    const findNearbyItemEntities = (origin, maxDistance = 6) => {
        return Object.values(bot.entities)
            .filter((entity) => {
                if (!entity || !entity.isValid || entity === bot.entity) {
                    return false;
                }
                const looksLikeItem = entity.name === "item" || entity.name === "item_stack" || entity.displayName === "Item";
                if (!looksLikeItem) {
                    return false;
                }
                return entity.position.distanceTo(origin.offset(0.5, 0.5, 0.5)) <= maxDistance;
            });
    };

    const collectDroppedEntities = async (origin, entities) => {
        const seenIds = new Set();
        const validEntities = [];
        for (const entity of [...entities, ...findNearbyItemEntities(origin, 6)]) {
            if (!entity || !entity.isValid || seenIds.has(entity.id)) {
                continue;
            }
            seenIds.add(entity.id);
            validEntities.push(entity);
        }
        if (validEntities.length === 0) {
            return 0;
        }
        let collectTimeout = null;
        try {
            await Promise.race([
                bot.collectBlock.collect(validEntities, { ignoreNoPath: true }),
                new Promise((_, reject) => {
                    collectTimeout = setTimeout(() => {
                        reject(new Error(`drop collect timed out for ${name}`));
                    }, 12000);
                }),
            ]);
            await bot.waitForTicks(5);
            return validEntities.length;
        } finally {
            if (collectTimeout) clearTimeout(collectTimeout);
        }
    };

    const mineDirectlyIfReachable = async (target) => {
        const freshTarget = bot.blockAt(target.position);
        if (!freshTarget || freshTarget.type !== blockByName.id) {
            return false;
        }
        const canDigDirect = typeof bot.canDigBlock === "function" && bot.canDigBlock(freshTarget);
        if (!canDigDirect) {
            return false;
        }

        if (bot.tool && typeof bot.tool.equipForBlock === "function") {
            await bot.tool.equipForBlock(freshTarget, { requireHarvest: false });
        }
        try {
            await bot.lookAt(freshTarget.position.offset(0.5, 0.5, 0.5), true);
        } catch (lookErr) {}

        const itemByName = mcData.itemsByName[name];
        const inventoryBefore = itemByName ? bot.inventory.count(itemByName.id, null) : null;
        const droppedEntities = [];
        const onItemDrop = (entity) => {
            if (entity.position.distanceTo(freshTarget.position.offset(0.5, 0.5, 0.5)) <= 1.5) {
                droppedEntities.push(entity);
            }
        };
        bot.on("itemDrop", onItemDrop);

        let digTimeout = null;
        try {
            await Promise.race([
                bot.dig(freshTarget, true),
                new Promise((_, reject) => {
                    digTimeout = setTimeout(() => {
                        reject(new Error(`mineBlock timed out while directly digging ${name}`));
                    }, 8000);
                }),
            ]);
            await waitForPhysicsTicks(10);
            const blockAfter = bot.blockAt(freshTarget.position);
            const blockGone = !blockAfter || blockAfter.type !== freshTarget.type;
            const nearbyItems = findNearbyItemEntities(freshTarget.position, 6);
            if (!blockGone) {
                throw new Error(`direct dig reported success but block remained for ${name} at ${fmtPos(freshTarget.position)}`);
            }
            await collectDroppedEntities(freshTarget.position, droppedEntities);
            if (itemByName) {
                const inventoryAfter = bot.inventory.count(itemByName.id, null);
                if (inventoryAfter <= inventoryBefore) {
                    const suspectedProtected = droppedEntities.length === 0 && nearbyItems.length === 0;
                    if (suspectedProtected) {
                        throw new Error(`possible protected or invalid mining for ${name} at ${fmtPos(freshTarget.position)}: block disappeared but no drops or inventory increase were observed`);
                    }
                    throw new Error(`broke ${name} at ${fmtPos(freshTarget.position)} but failed to collect the drop`);
                }
            }
            return true;
        } catch (err) {
            throw err;
        } finally {
            bot.off("itemDrop", onItemDrop);
            if (digTimeout) clearTimeout(digTimeout);
        }
    };

    for (const target of targets) {
        if (mined >= count) {
            break;
        }
        let timeout = null;
        try {
            const directDigWorked = await mineDirectlyIfReachable(target);
            if (!directDigWorked) {
                const collectPromise = bot.collectBlock.collect(target, {
                    ignoreNoPath: true,
                });
                await Promise.race([
                    collectPromise,
                    new Promise((_, reject) => {
                        timeout = setTimeout(() => {
                            reject(new Error(`mineBlock timed out while collecting ${name}`));
                        }, 20000);
                    }),
                ]);
            }
            mined += 1;
            lastError = null;
            await bot.waitForTicks(5);
        } catch (err) {
            lastError = err;
            logMineWarning(`target=${fmtPos(target.position)} err=${err.message}`);
        } finally {
            if (timeout) clearTimeout(timeout);
            await stopCurrentTask();
        }
    }

    if (mined === 0) {
        _mineBlockFailCount++;
        if (_mineBlockFailCount > 10) {
            throw new Error(
                lastError && lastError.message
                    ? lastError.message
                    : `mineBlock failed too many times while collecting ${name}`
            );
        }
        if (lastError) {
            throw lastError;
        }
        return;
    }

    _mineBlockFailCount = 0;
    bot.save(`${name}_mined`);
}
