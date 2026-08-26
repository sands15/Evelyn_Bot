import Vec3 from 'vec3';

const HOSTILE_NAMES = new Set([
    'blaze', 'bogged', 'breeze', 'cave_spider', 'creeper', 'drowned', 'elder_guardian',
    'enderman', 'endermite', 'evoker', 'ghast', 'guardian', 'hoglin', 'husk', 'magma_cube',
    'phantom', 'piglin_brute', 'pillager', 'ravager', 'shulker', 'silverfish', 'skeleton',
    'slime', 'spider', 'stray', 'vex', 'vindicator', 'warden', 'witch', 'wither_skeleton',
    'zoglin', 'zombie', 'zombie_villager'
]);

export function isKnownHostile(name) {
    return HOSTILE_NAMES.has(String(name || '').toLowerCase());
}

const FOOD_NAMES = new Set([
    'apple', 'baked_potato', 'beef', 'beetroot', 'beetroot_soup', 'bread', 'carrot',
    'cod', 'cooked_beef', 'cooked_chicken', 'cooked_cod', 'cooked_mutton',
    'cooked_porkchop', 'cooked_rabbit', 'cooked_salmon', 'cookie', 'dried_kelp',
    'golden_apple', 'golden_carrot', 'melon_slice', 'mushroom_stew', 'mutton',
    'porkchop', 'potato', 'pumpkin_pie', 'rabbit', 'rabbit_stew', 'salmon',
    'sweet_berries'
]);

const TARGET_TAGS = {
    '#logs': (name) => /_(?:log|stem|hyphae)$/.test(name),
    '#planks': (name) => /_planks$/.test(name),
    '#food': (name) => FOOD_NAMES.has(name),
    '#pickaxes': (name) => /_pickaxe$/.test(name),
    '#weapons': (name) => /_(?:sword|axe)$/.test(name) || ['bow', 'crossbow', 'trident'].includes(name),
    '#armor': (name) => /_(?:helmet|chestplate|leggings|boots)$/.test(name),
    '#fuel': (name) => ['coal', 'charcoal', 'coal_block'].includes(name),
    '#iron': (name) => ['iron_ingot', 'raw_iron', 'iron_block'].includes(name),
    '#diamonds': (name) => ['diamond', 'diamond_block'].includes(name),
    '#ender_pearls': (name) => name === 'ender_pearl',
    '#blaze_rods': (name) => name === 'blaze_rod',
    '#eyes_of_ender': (name) => name === 'ender_eye'
};

export function itemMatchesTarget(itemName, target) {
    const name = String(itemName || '').trim().toLowerCase();
    const wanted = String(target || '').trim().toLowerCase();
    if (!name || !wanted) return false;
    if (TARGET_TAGS[wanted]) return TARGET_TAGS[wanted](name);
    return name === wanted;
}

export function inventoryCounts(bot) {
    const counts = {};
    for (const item of bot?.inventory?.items?.() || []) {
        const name = String(item?.name || '').trim();
        if (!name) continue;
        counts[name] = (counts[name] || 0) + Number(item?.count || 0);
    }
    return counts;
}

export function inventoryCountForTarget(inventory, target) {
    return Object.entries(inventory || {}).reduce(
        (total, [name, count]) => total + (itemMatchesTarget(name, target) ? Number(count || 0) : 0),
        0
    );
}

export function hostileIsActionable(origin, entityPosition, distance) {
    if (!origin || !entityPosition || !Number.isFinite(distance)) return false;
    const verticalDistance = Math.abs(Number(entityPosition.y) - Number(origin.y));
    return distance <= 8 || verticalDistance <= 5;
}

export function hostileHasClearLine(bot, entity) {
    const origin = bot?.entity?.position;
    const target = entity?.position;
    if (!origin || !target) return false;
    if (typeof bot.blockAt !== 'function') return true;
    const start = {x: Number(origin.x), y: Number(origin.y) + 1.55, z: Number(origin.z)};
    const end = {
        x: Number(target.x),
        y: Number(target.y) + Math.min(1.2, Number(entity.height || 1.8) * 0.6),
        z: Number(target.z),
    };
    const distance = Math.hypot(end.x - start.x, end.y - start.y, end.z - start.z);
    const steps = Math.max(2, Math.ceil(distance * 2));
    for (let index = 1; index < steps - 1; index++) {
        const ratio = index / steps;
        const block = bot.blockAt(new Vec3(
            Math.floor(start.x + ((end.x - start.x) * ratio)),
            Math.floor(start.y + ((end.y - start.y) * ratio)),
            Math.floor(start.z + ((end.z - start.z) * ratio)),
        ));
        if (block?.boundingBox === 'block') return false;
    }
    return true;
}

function nearbyHostiles(bot) {
    const origin = bot?.entity?.position;
    if (!origin) return [];
    return Object.values(bot?.entities || {})
        .filter((entity) => (
            entity?.position &&
            isKnownHostile(entity?.name)
        ))
        .map((entity) => {
            const distance = Math.round(origin.distanceTo(entity.position) * 10) / 10;
            return {
                name: String(entity.name),
                distance,
                verticalDistance: Math.round(Math.abs(origin.y - entity.position.y) * 10) / 10,
                actionable: hostileIsActionable(origin, entity.position, distance) &&
                    (distance <= 4 || hostileHasClearLine(bot, entity))
            };
        })
        .filter((entity) => entity.distance <= 24)
        .sort((left, right) => left.distance - right.distance)
        .slice(0, 8);
}

function normalizeDimension(value) {
    return String(value || 'unknown')
        .replace(/^minecraft:/, '')
        .replace(/^the_/, '');
}

export function buildWorldState(bot, now = Date.now()) {
    const position = bot?.entity?.position;
    const inventory = inventoryCounts(bot);
    return {
        observedAt: now,
        connected: Boolean(bot?.entity),
        health: Number.isFinite(bot?.health) ? Number(bot.health) : null,
        hunger: Number.isFinite(bot?.food) ? Number(bot.food) : null,
        foodSaturation: Number.isFinite(bot?.foodSaturation) ? Number(bot.foodSaturation) : null,
        dimension: normalizeDimension(bot?.game?.dimension),
        position: position
            ? {
                x: Math.round(position.x * 10) / 10,
                y: Math.round(position.y * 10) / 10,
                z: Math.round(position.z * 10) / 10
            }
            : null,
        timeOfDay: Number.isFinite(bot?.time?.timeOfDay) ? Number(bot.time.timeOfDay) : null,
        inventory,
        inventorySummary: {
            logs: inventoryCountForTarget(inventory, '#logs'),
            planks: inventoryCountForTarget(inventory, '#planks'),
            food: inventoryCountForTarget(inventory, '#food'),
            pickaxes: inventoryCountForTarget(inventory, '#pickaxes'),
            weapons: inventoryCountForTarget(inventory, '#weapons'),
            armor: inventoryCountForTarget(inventory, '#armor'),
            iron: inventoryCountForTarget(inventory, '#iron'),
            diamonds: inventoryCountForTarget(inventory, '#diamonds'),
            blazeRods: inventoryCountForTarget(inventory, '#blaze_rods'),
            enderPearls: inventoryCountForTarget(inventory, '#ender_pearls'),
            eyesOfEnder: inventoryCountForTarget(inventory, '#eyes_of_ender')
        },
        heldItem: bot?.heldItem?.name || null,
        hostilesNearby: nearbyHostiles(bot),
        defeatedEntities: {...(bot?.evelynGoalFacts?.defeatedEntities || {})}
    };
}

export function worldStateChanged(before, after) {
    if (!before || !after) return false;
    if (before.dimension !== after.dimension) return true;
    if (before.health !== after.health || before.hunger !== after.hunger) return true;
    const names = new Set([
        ...Object.keys(before.inventory || {}),
        ...Object.keys(after.inventory || {})
    ]);
    for (const name of names) {
        if (Number(before.inventory?.[name] || 0) !== Number(after.inventory?.[name] || 0)) {
            return true;
        }
    }
    const left = before.position;
    const right = after.position;
    if (left && right) {
        const distance = Math.hypot(right.x - left.x, right.y - left.y, right.z - left.z);
        if (distance >= 2) return true;
    }
    return false;
}
