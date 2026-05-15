function _detectDomain(bot) {
    try {
        if (typeof bot.canSeeSky === "function" && bot.canSeeSky(bot.entity.position.floored())) {
            return "surface";
        }
    } catch (err) {}
    return bot.entity.position.y < 58 ? "underground" : "surface";
}

function _distance(a, b) {
    return a.distanceTo(b);
}

function _toPos(positionLike) {
    if (!positionLike) return null;
    if (typeof positionLike.clone === "function") {
        return positionLike.clone();
    }
    return new Vec3(positionLike.x, positionLike.y, positionLike.z);
}

function _isPassableBlock(block) {
    if (!block) return false;
    const passableNames = new Set(["air", "cave_air", "void_air", "short_grass", "tall_grass"]);
    return block.boundingBox === "empty" || passableNames.has(block.name);
}

function _isHazardBlock(block) {
    if (!block) return false;
    return ["water", "lava", "magma_block", "fire", "campfire", "soul_campfire"].includes(block.name);
}

function _isSafeStandPosition(bot, positionLike) {
    const position = _toPos(positionLike);
    if (!position) return false;
    const feet = bot.blockAt(position);
    const head = bot.blockAt(position.offset(0, 1, 0));
    const ground = bot.blockAt(position.offset(0, -1, 0));
    if (!feet || !head || !ground) return false;
    if (!_isPassableBlock(feet) || !_isPassableBlock(head)) return false;
    if (_isHazardBlock(feet) || _isHazardBlock(head) || _isHazardBlock(ground)) return false;
    if (ground.boundingBox !== "block") return false;
    return true;
}

function _findSurfaceRecoveryCandidates(bot, radius = 24, maxVerticalScan = 18) {
    const origin = bot.entity.position.floored();
    const candidates = [];
    const step = radius <= 16 ? 2 : 3;
    for (let dx = -radius; dx <= radius; dx += step) {
        for (let dz = -radius; dz <= radius; dz += step) {
            const horizontalDistance = Math.sqrt(dx * dx + dz * dz);
            if (horizontalDistance < 2 || horizontalDistance > radius) continue;
            const x = origin.x + dx;
            const z = origin.z + dz;
            const minY = Math.max(1, origin.y - 6);
            const maxY = origin.y + maxVerticalScan;
            for (let y = maxY; y >= minY; y -= 1) {
                const feetPos = new Vec3(x, y, z);
                if (!_isSafeStandPosition(bot, feetPos)) continue;
                let skyVisible = false;
                try {
                    skyVisible = typeof bot.canSeeSky === "function" && bot.canSeeSky(feetPos.offset(0, 1, 0));
                } catch (err) {}
                if (!skyVisible) continue;
                const score = 1000 + (y - origin.y) * 8 - horizontalDistance;
                candidates.push({
                    type: "recovery",
                    position: feetPos.clone(),
                    distance: _distance(bot.entity.position, feetPos),
                    score,
                });
                break;
            }
        }
    }
    return candidates.sort((a, b) => b.score - a.score || a.distance - b.distance);
}

function _scoreWoodArea(bot, positionLike, radius = 6) {
    const position = _toPos(positionLike);
    if (!position) return -Infinity;
    let leafCount = 0;
    let logCount = 0;
    let grassCount = 0;
    let hazardCount = 0;
    let openSkyBonus = 0;
    const leafSet = new Set(WOOD_LEAF_NAMES);
    const logSet = new Set(WOOD_LOG_NAMES);
    const grassSet = new Set(["grass_block", "short_grass", "tall_grass", "fern", "large_fern", "dirt", "podzol"]);
    for (let dx = -radius; dx <= radius; dx += 2) {
        for (let dz = -radius; dz <= radius; dz += 2) {
            const sample = position.offset(dx, 0, dz);
            const ground = bot.blockAt(sample.offset(0, -1, 0));
            const feet = bot.blockAt(sample);
            const canopy = bot.blockAt(sample.offset(0, 1, 0));
            if (ground && grassSet.has(ground.name)) grassCount += 1;
            if (ground && _isHazardBlock(ground)) hazardCount += 1;
            if (feet && logSet.has(feet.name)) logCount += 2;
            if (canopy && leafSet.has(canopy.name)) leafCount += 1;
            try {
                if (typeof bot.canSeeSky === "function" && bot.canSeeSky(sample.offset(0, 1, 0))) {
                    openSkyBonus += 0.25;
                }
            } catch (err) {}
        }
    }
    const elevationBonus = Math.max(0, position.y - bot.entity.position.y) * 1.5;
    const distancePenalty = _distance(bot.entity.position, position) * 0.6;
    return logCount * 12 + leafCount * 5 + grassCount * 1.2 + openSkyBonus + elevationBonus - hazardCount * 12 - distancePenalty;
}

function _findWoodScoutCandidates(bot, radius = 32) {
    return _findSurfaceRecoveryCandidates(bot, radius, 14)
        .slice(0, 24)
        .map((candidate) => ({
            ...candidate,
            type: "wood_scout",
            score: candidate.score + _scoreWoodArea(bot, candidate.position, 6),
        }))
        .sort((a, b) => b.score - a.score || a.distance - b.distance);
}

function _scoreFoodArea(bot, positionLike, radius = 8) {
    const position = _toPos(positionLike);
    if (!position) return -Infinity;
    let grassCount = 0;
    let cropCount = 0;
    let waterEdgeCount = 0;
    let hazardCount = 0;
    let openSkyBonus = 0;
    const grassSet = new Set(["grass_block", "short_grass", "tall_grass", "fern", "large_fern", "dirt", "podzol"]);
    const cropSet = new Set(["wheat", "carrots", "potatoes", "beetroots", "melon", "pumpkin"]);
    const waterSet = new Set(["water", "seagrass", "tall_seagrass"]);
    for (let dx = -radius; dx <= radius; dx += 2) {
        for (let dz = -radius; dz <= radius; dz += 2) {
            const sample = position.offset(dx, 0, dz);
            const ground = bot.blockAt(sample.offset(0, -1, 0));
            const feet = bot.blockAt(sample);
            if (ground && grassSet.has(ground.name)) grassCount += 1;
            if (feet && cropSet.has(feet.name)) cropCount += 3;
            if ((ground && waterSet.has(ground.name)) || (feet && waterSet.has(feet.name))) waterEdgeCount += 1;
            if ((ground && _isHazardBlock(ground)) || (feet && _isHazardBlock(feet))) hazardCount += 1;
            try {
                if (typeof bot.canSeeSky === "function" && bot.canSeeSky(sample.offset(0, 1, 0))) {
                    openSkyBonus += 0.25;
                }
            } catch (err) {}
        }
    }
    const nearbyFoodEntities = Object.values(bot.entities || {})
        .filter((entity) => entity && entity.position && ["cow", "pig", "chicken", "sheep", "rabbit"].includes(entity.name))
        .filter((entity) => _distance(entity.position, position) <= radius + 8).length;
    const elevationPenalty = Math.max(0, bot.entity.position.y - position.y) * 0.5;
    const distancePenalty = _distance(bot.entity.position, position) * 0.55;
    return cropCount * 8 + nearbyFoodEntities * 14 + grassCount * 1.5 + waterEdgeCount * 0.8 + openSkyBonus - hazardCount * 12 - elevationPenalty - distancePenalty;
}

function _findFoodScoutCandidates(bot, radius = 32) {
    return _findSurfaceRecoveryCandidates(bot, radius, 12)
        .slice(0, 24)
        .map((candidate) => ({
            ...candidate,
            type: "food_scout",
            score: candidate.score + _scoreFoodArea(bot, candidate.position, 8),
        }))
        .sort((a, b) => b.score - a.score || a.distance - b.distance);
}

function _desiredOreY(options = {}) {
    const target = String(options.target || options.targetBlock || options.targetName || "").toLowerCase();
    if (target.includes("diamond") || target.includes("redstone")) return -54;
    if (target.includes("gold")) return -16;
    if (target.includes("iron")) return 16;
    if (target.includes("copper")) return 48;
    if (target.includes("coal")) return 64;
    return 16;
}

function _findUndergroundScoutCandidates(bot, radius = 24, verticalRange = 8) {
    const origin = bot.entity.position.floored();
    const candidates = [];
    for (let dx = -radius; dx <= radius; dx += 3) {
        for (let dz = -radius; dz <= radius; dz += 3) {
            const horizontalDistance = Math.sqrt(dx * dx + dz * dz);
            if (horizontalDistance < 2 || horizontalDistance > radius) continue;
            const x = origin.x + dx;
            const z = origin.z + dz;
            for (let dy = verticalRange; dy >= -verticalRange; dy -= 1) {
                const feetPos = new Vec3(x, origin.y + dy, z);
                if (!_isSafeStandPosition(bot, feetPos)) continue;
                let skyVisible = false;
                try {
                    skyVisible = typeof bot.canSeeSky === "function" && bot.canSeeSky(feetPos.offset(0, 1, 0));
                } catch (err) {}
                if (skyVisible) continue;
                candidates.push({
                    type: "ore_scout",
                    position: feetPos.clone(),
                    distance: _distance(bot.entity.position, feetPos),
                    score: 100 - horizontalDistance - Math.abs(dy) * 2,
                });
                break;
            }
        }
    }
    return candidates.sort((a, b) => b.score - a.score || a.distance - b.distance);
}

function _scoreOreArea(bot, positionLike, options = {}, radius = 6) {
    const position = _toPos(positionLike);
    if (!position) return -Infinity;
    const oreSet = new Set(ORE_BLOCK_NAMES);
    const wallSet = new Set(["stone", "deepslate", "tuff", "granite", "diorite", "andesite"]);
    let exposedOreCount = 0;
    let caveAirCount = 0;
    let exposedWallCount = 0;
    let hazardCount = 0;
    for (let dx = -radius; dx <= radius; dx += 2) {
        for (let dy = -3; dy <= 3; dy += 1) {
            for (let dz = -radius; dz <= radius; dz += 2) {
                const sample = position.offset(dx, dy, dz);
                const block = bot.blockAt(sample);
                if (!block) continue;
                if (oreSet.has(block.name)) exposedOreCount += 6;
                if (block.name === "air" || block.name === "cave_air") caveAirCount += 1;
                if (wallSet.has(block.name)) exposedWallCount += 0.5;
                if (_isHazardBlock(block)) hazardCount += 2;
            }
        }
    }
    const targetY = _desiredOreY(options);
    const yPenalty = Math.abs(position.y - targetY) * 1.2;
    const distancePenalty = _distance(bot.entity.position, position) * 0.5;
    return exposedOreCount * 14 + caveAirCount * 1.4 + exposedWallCount - hazardCount * 10 - yPenalty - distancePenalty;
}

function _findOreScoutCandidates(bot, options = {}, radius = 24) {
    return _findUndergroundScoutCandidates(bot, radius, 10)
        .slice(0, 28)
        .map((candidate) => ({
            ...candidate,
            type: "ore_scout",
            score: candidate.score + _scoreOreArea(bot, candidate.position, options, 6),
        }))
        .sort((a, b) => b.score - a.score || a.distance - b.distance);
}

function _findNearbyBlocks(bot, blockNames, radius, limit = 8) {
    if (!Array.isArray(blockNames) || blockNames.length === 0) {
        return [];
    }
    const names = new Set(blockNames);
    const positions = bot.findBlocks({
        matching: (block) => !!block && names.has(block.name),
        maxDistance: radius,
        count: limit,
    });
    return positions
        .map((position) => bot.blockAt(position))
        .filter((block) => !!block)
        .sort((a, b) => _distance(bot.entity.position, a.position) - _distance(bot.entity.position, b.position))
        .map((block) => ({
            type: "block",
            blockName: block.name,
            position: block.position.clone(),
            distance: _distance(bot.entity.position, block.position),
        }));
}

function _findNearbyEntities(bot, entityNames, radius) {
    if (!Array.isArray(entityNames) || entityNames.length === 0) {
        return [];
    }
    const names = new Set(entityNames);
    return Object.values(bot.entities || {})
        .filter((entity) => entity && entity.position && names.has(entity.name))
        .filter((entity) => _distance(bot.entity.position, entity.position) <= radius)
        .sort((a, b) => _distance(bot.entity.position, a.position) - _distance(bot.entity.position, b.position))
        .map((entity) => ({
            type: "entity",
            entityName: entity.name,
            position: entity.position.clone(),
            distance: _distance(bot.entity.position, entity.position),
        }));
}

function _summarizeHazards(bot) {
    const hostileNames = new Set(["zombie", "skeleton", "creeper", "spider", "witch", "drowned", "enderman"]);
    const nearbyHostiles = Object.values(bot.entities || {})
        .filter((entity) => entity && entity.position && hostileNames.has(entity.name))
        .filter((entity) => _distance(bot.entity.position, entity.position) <= 12)
        .map((entity) => entity.name);
    return {
        nearbyHostiles,
        hostileCount: nearbyHostiles.length,
    };
}

function perceiveSearchState(bot, options = {}) {
    const goalType = options.goalType || "generic";
    const profile = _resolveProfile(bot, goalType, options.profile);
    const targets = _resolveTargets(goalType);
    const radiusIndex = Math.max(0, Number(options.radiusIndex || 0));
    const radius = profile.searchRadii[Math.min(radiusIndex, profile.searchRadii.length - 1)] || 32;
    const domain = _detectDomain(bot);
    const blocks = _findNearbyBlocks(bot, targets.blockNames, radius, 12);
    const entities = _findNearbyEntities(bot, targets.entityNames, radius);
    const recoveryCandidates = goalType === "recovery" || (domain === "underground" && ["wood", "food"].includes(goalType))
        ? _findSurfaceRecoveryCandidates(bot, Math.max(radius, 24), 18)
        : [];
    const woodScoutCandidates = goalType === "wood" && domain === "surface" && blocks.length === 0
        ? _findWoodScoutCandidates(bot, Math.max(radius, 24))
        : [];
    const foodScoutCandidates = goalType === "food" && domain === "surface" && blocks.length === 0 && entities.length === 0
        ? _findFoodScoutCandidates(bot, Math.max(radius, 24))
        : [];
    const oreScoutCandidates = goalType === "ore" && domain === "underground" && blocks.length === 0
        ? _findOreScoutCandidates(bot, options, Math.max(radius, 20))
        : [];
    return {
        goalType,
        profile,
        domain,
        radius,
        hazards: _summarizeHazards(bot),
        candidates: {
            blocks,
            entities,
            recovery: recoveryCandidates,
            woodScout: woodScoutCandidates,
            foodScout: foodScoutCandidates,
            oreScout: oreScoutCandidates,
            primary: [...blocks, ...entities].sort((a, b) => a.distance - b.distance),
        },
    };
}
