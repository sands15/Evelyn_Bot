const DEFAULT_HOSTILE_THREAT = 8;
const COMBAT_TICK_MS = 250;

const HOSTILE_THREAT = Object.freeze({
    blaze: 14,
    bogged: 11,
    breeze: 12,
    cave_spider: 10,
    creeper: 38,
    drowned: 8,
    elder_guardian: 30,
    ender_dragon: 120,
    enderman: 16,
    evoker: 20,
    ghast: 13,
    guardian: 13,
    hoglin: 13,
    husk: 7,
    parched: 11,
    phantom: 10,
    piglin_brute: 16,
    pillager: 11,
    ravager: 24,
    shulker: 13,
    skeleton: 10,
    spider: 6,
    stray: 11,
    vex: 11,
    vindicator: 14,
    warden: 120,
    witch: 17,
    wither: 120,
    wither_skeleton: 13,
    zoglin: 15,
    zombie: 6,
});

const RANGED_HOSTILES = new Set([
    'blaze', 'bogged', 'elder_guardian', 'evoker', 'ghast', 'guardian',
    'parched', 'pillager', 'shulker', 'skeleton', 'stray', 'witch',
]);

const WEAPON_POWER = Object.freeze({
    wooden_sword: 4,
    golden_sword: 4,
    stone_sword: 5,
    iron_sword: 6,
    diamond_sword: 7,
    netherite_sword: 8,
    wooden_axe: 7,
    golden_axe: 7,
    stone_axe: 9,
    iron_axe: 9,
    diamond_axe: 9,
    netherite_axe: 10,
    trident: 9,
    mace: 8,
});

const ARMOR_POINTS = Object.freeze({
    leather_helmet: 1,
    golden_helmet: 2,
    chainmail_helmet: 2,
    iron_helmet: 2,
    diamond_helmet: 3,
    netherite_helmet: 3,
    turtle_helmet: 2,
    leather_chestplate: 3,
    golden_chestplate: 5,
    chainmail_chestplate: 5,
    iron_chestplate: 6,
    diamond_chestplate: 8,
    netherite_chestplate: 8,
    leather_leggings: 2,
    golden_leggings: 3,
    chainmail_leggings: 4,
    iron_leggings: 5,
    diamond_leggings: 6,
    netherite_leggings: 6,
    leather_boots: 1,
    golden_boots: 1,
    chainmail_boots: 1,
    iron_boots: 2,
    diamond_boots: 3,
    netherite_boots: 3,
});

function finite(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
}

function hostileThreat(name) {
    return HOSTILE_THREAT[String(name || '').toLowerCase()] || DEFAULT_HOSTILE_THREAT;
}

function normalizeHostiles(snapshot) {
    const listed = Array.isArray(snapshot?.hostiles)
        ? snapshot.hostiles.filter((hostile) => hostile && hostile.name)
        : [];
    const expectedCount = Math.max(0, finite(snapshot?.hostileCount));
    if (!listed.length && snapshot?.hostileName) {
        listed.push({
            id: snapshot.hostileId,
            name: snapshot.hostileName,
            distance: snapshot.hostileDistance,
        });
    }
    while (listed.length < expectedCount) {
        listed.push({name: 'unknown', distance: snapshot?.hostileDistance});
    }
    return listed;
}

export function weaponPowerFromInventory(inventory = {}) {
    return Object.entries(inventory).reduce((best, [name, count]) => (
        finite(count) > 0 ? Math.max(best, WEAPON_POWER[name] || 0) : best
    ), 0);
}

export function armorPointsFromNames(names = []) {
    return names.reduce((total, name) => total + (ARMOR_POINTS[name] || 0), 0);
}

export function assessCombat(snapshot) {
    const hostiles = normalizeHostiles(snapshot);
    if (!hostiles.length || snapshot?.hostileDistance === null || snapshot?.hostileDistance === undefined) {
        return {
            tactic: 'none',
            reason: 'no_hostiles',
            capacity: 0,
            threat: 0,
            hostileCount: 0,
        };
    }

    const health = finite(snapshot.health, 20);
    const hunger = finite(snapshot.hunger, 20);
    const armorPoints = Math.min(20, Math.max(0, finite(snapshot.armorPoints)));
    const weaponPower = Math.max(
        finite(snapshot.weaponPower),
        snapshot.hasMeleeWeapon ? 4 : 0,
    );
    const hasRangedWeapon = Boolean(snapshot.hasBow && finite(snapshot.arrowCount) > 0);
    if (health <= 6) {
        return {tactic: 'flee', reason: 'critical_health', capacity: health, threat: Infinity, hostileCount: hostiles.length};
    }
    if (hunger <= 4) {
        return {tactic: 'flee', reason: 'critical_hunger', capacity: hunger, threat: Infinity, hostileCount: hostiles.length};
    }
    if (weaponPower <= 0 && !hasRangedWeapon) {
        return {tactic: 'flee', reason: 'unarmed', capacity: health, threat: Infinity, hostileCount: hostiles.length};
    }

    let rawThreat = 0;
    for (const hostile of hostiles) {
        let value = hostileThreat(hostile.name);
        if (snapshot.hasShield && RANGED_HOSTILES.has(String(hostile.name).toLowerCase())) {
            value *= 0.65;
        }
        const distance = finite(hostile.distance, finite(snapshot.hostileDistance, 8));
        if (distance <= 3) value *= 1.12;
        rawThreat += value;
    }
    const groupMultiplier = 1 + Math.min(0.5, Math.max(0, hostiles.length - 1) * 0.08);
    const terrainMultiplier = snapshot.inWater ? 1.35 : 1;
    const threat = rawThreat * groupMultiplier * terrainMultiplier;
    const effectiveHealth = health * (1 + armorPoints * 0.04);
    const hungerBonus = hunger >= 16 ? 4 : (hunger >= 8 ? 1 : -5);
    const capacity = effectiveHealth
        + weaponPower * 2.75
        + (snapshot.hasShield ? 6 : 0)
        + (hasRangedWeapon ? 4 : 0)
        + hungerBonus;
    const tactic = capacity >= threat * 1.1 ? 'fight' : 'flee';
    return {
        tactic,
        reason: tactic === 'fight' ? 'readiness_exceeds_threat' : 'threat_exceeds_readiness',
        capacity: Number(capacity.toFixed(2)),
        threat: Number(threat.toFixed(2)),
        hostileCount: hostiles.length,
    };
}

export function selectCombatTarget(hostiles = []) {
    return [...hostiles]
        .filter((entry) => entry?.entity && entry.entity.isValid !== false)
        .sort((left, right) => {
            const score = (entry) => (
                hostileThreat(entry.entity?.name) * 2
                + Math.max(0, 12 - finite(entry.distance, 12))
            );
            return score(right) - score(left);
        })[0] || null;
}

export function selectCombatMode(snapshot, target) {
    const targetName = String(target?.entity?.name || target?.name || '').toLowerCase();
    const distance = finite(target?.distance, finite(snapshot?.hostileDistance, 0));
    const bowReady = Boolean(snapshot?.hasBow && finite(snapshot?.arrowCount) > 0);
    if (bowReady && (distance > 4.5 || RANGED_HOSTILES.has(targetName) || targetName === 'creeper')) {
        return 'bow';
    }
    return 'melee';
}

export function stopCombatControllers(bot) {
    bot.bowpvp?.stop?.();
    bot.swordpvp?.stop?.();
    bot.pvp?.stop?.();
}

function meleePower(name) {
    return WEAPON_POWER[name] || 0;
}

async function equipBestMeleeWeapon(bot) {
    const best = [...(bot.inventory?.items?.() || [])]
        .filter((item) => meleePower(item?.name) > 0)
        .sort((left, right) => meleePower(right.name) - meleePower(left.name))[0];
    if (!best) return false;
    if (bot.heldItem?.name !== best.name) await bot.equip(best, 'hand');
    if (bot.swordpvp) {
        bot.swordpvp.weaponOfChoice = best.name.endsWith('_axe') ? '_axe' : 'sword';
    }
    return true;
}

function wait(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function fightWithCustomPvp(bot, {
    snapshotProvider,
    hostileProvider,
    timeoutMs = 20000,
} = {}) {
    if (!bot.swordpvp || !bot.bowpvp) {
        return {success: false, reason: 'custom_pvp_unavailable', assessment: null};
    }

    const deadline = Date.now() + timeoutMs;
    let activeTargetId = null;
    let activeMode = null;
    let lastAssessment = null;
    try {
        while (Date.now() < deadline && bot.health > 0 && !bot.interrupt_code) {
            const snapshot = snapshotProvider();
            lastAssessment = assessCombat(snapshot);
            if (lastAssessment.tactic === 'none') {
                return {success: true, reason: 'hostiles_cleared', assessment: lastAssessment};
            }
            if (lastAssessment.tactic !== 'fight') {
                return {success: false, reason: `disengage_${lastAssessment.reason}`, assessment: lastAssessment};
            }

            const target = selectCombatTarget(hostileProvider());
            if (!target) {
                return {success: true, reason: 'hostiles_cleared', assessment: lastAssessment};
            }
            const mode = selectCombatMode(snapshot, target);
            if (target.entity.id !== activeTargetId || mode !== activeMode) {
                stopCombatControllers(bot);
                if (mode === 'bow') {
                    await bot.bowpvp.attack(target.entity, snapshot.rangedWeapon || 'bow');
                } else {
                    if (!await equipBestMeleeWeapon(bot)) {
                        return {success: false, reason: 'melee_weapon_missing', assessment: lastAssessment};
                    }
                    await bot.swordpvp.attack(target.entity);
                }
                activeTargetId = target.entity.id;
                activeMode = mode;
            }
            await wait(COMBAT_TICK_MS);
        }
        return {
            success: false,
            reason: bot.health <= 0
                ? 'bot_dead'
                : (bot.interrupt_code ? 'interrupted' : 'combat_timeout'),
            assessment: lastAssessment,
        };
    } catch (error) {
        return {
            success: false,
            reason: `custom_pvp_error:${error?.message || error}`,
            assessment: lastAssessment,
        };
    } finally {
        stopCombatControllers(bot);
    }
}
