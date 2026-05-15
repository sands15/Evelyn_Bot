const WOOD_LOG_NAMES = [
    "oak_log",
    "spruce_log",
    "birch_log",
    "jungle_log",
    "acacia_log",
    "dark_oak_log",
    "mangrove_log",
    "cherry_log",
];

const WOOD_LEAF_NAMES = [
    "oak_leaves",
    "spruce_leaves",
    "birch_leaves",
    "jungle_leaves",
    "acacia_leaves",
    "dark_oak_leaves",
    "mangrove_leaves",
    "cherry_leaves",
    "azalea_leaves",
    "flowering_azalea_leaves",
];

const SEARCH_PROFILES = {
    generic: {
        domain: "adaptive",
        searchRadii: [24, 48, 72],
        timeBudgetSec: 12,
        progressTimeoutSec: 8,
        preferredDirections: [
            { x: 1, y: 0, z: 0 },
            { x: 0, y: 0, z: 1 },
            { x: -1, y: 0, z: 0 },
            { x: 0, y: 0, z: -1 },
        ],
    },
    wood: {
        domain: "surface",
        searchRadii: [32, 64, 96, 128],
        timeBudgetSec: 14,
        progressTimeoutSec: 10,
        preferredDirections: [
            { x: 1, y: 0, z: 1 },
            { x: 1, y: 0, z: 0 },
            { x: 0, y: 0, z: 1 },
            { x: -1, y: 0, z: 1 },
        ],
    },
    food: {
        domain: "surface",
        searchRadii: [24, 48, 96, 128],
        timeBudgetSec: 14,
        progressTimeoutSec: 10,
        preferredDirections: [
            { x: 1, y: 0, z: 0 },
            { x: 0, y: 0, z: 1 },
            { x: -1, y: 0, z: 0 },
            { x: 0, y: 0, z: -1 },
        ],
    },
    ore: {
        domain: "underground",
        searchRadii: [16, 32, 48],
        timeBudgetSec: 12,
        progressTimeoutSec: 8,
        preferredDirections: [
            { x: 1, y: 0, z: 0 },
            { x: -1, y: 0, z: 0 },
            { x: 0, y: 0, z: 1 },
            { x: 0, y: 0, z: -1 },
        ],
    },
    recovery: {
        domain: "escape",
        searchRadii: [16, 32, 48, 64],
        timeBudgetSec: 12,
        progressTimeoutSec: 8,
        preferredDirections: [{ x: 0, y: 1, z: 0 }],
    },
};

const ORE_BLOCK_NAMES = ["coal_ore", "iron_ore", "copper_ore", "gold_ore", "diamond_ore", "deepslate_iron_ore", "deepslate_coal_ore", "deepslate_copper_ore", "deepslate_gold_ore", "deepslate_diamond_ore"];

const SEARCH_TARGETS = {
    wood: {
        blockNames: WOOD_LOG_NAMES,
        entityNames: [],
    },
    food: {
        blockNames: ["wheat", "carrots", "potatoes", "beetroots", "melon", "pumpkin"],
        entityNames: ["cow", "pig", "chicken", "sheep", "rabbit"],
    },
    ore: {
        blockNames: ORE_BLOCK_NAMES,
        entityNames: [],
    },
    recovery: {
        blockNames: [],
        entityNames: [],
    },
    generic: {
        blockNames: [],
        entityNames: [],
    },
};

function _searchPolicyFor(bot, goalType) {
    if (!bot || !bot._voyagerSearchPolicy || typeof bot._voyagerSearchPolicy !== "object") {
        return null;
    }
    const bucket = bot._voyagerSearchPolicy[goalType];
    return bucket && typeof bucket === "object" ? bucket : null;
}

function _applyProfilePolicy(profile, policy = null) {
    const nextProfile = {
        ...profile,
        searchRadii: Array.isArray(profile.searchRadii) ? [...profile.searchRadii] : [32],
        preferredDirections: Array.isArray(profile.preferredDirections) ? [...profile.preferredDirections] : [],
    };
    if (!policy) {
        return nextProfile;
    }
    const radiusBonus = Math.max(0, Number(policy.radiusBonus || 0));
    const timeBudgetScale = Math.min(1.75, Math.max(0.75, Number(policy.timeBudgetScale || 1)));
    const progressTimeoutScale = Math.min(1.5, Math.max(0.5, Number(policy.progressTimeoutScale || 1)));
    const forceDomain = typeof policy.forceDomain === "string" && policy.forceDomain ? policy.forceDomain : null;
    if (radiusBonus > 0) {
        nextProfile.searchRadii = nextProfile.searchRadii.map((radius) => {
            const base = Number(radius || 0);
            const step = nextProfile.domain === "underground" ? 8 : 16;
            return Math.round(base + step * radiusBonus);
        });
    }
    nextProfile.timeBudgetSec = Math.round(Number(nextProfile.timeBudgetSec || 12) * timeBudgetScale * 100) / 100;
    nextProfile.progressTimeoutSec = Math.round(Number(nextProfile.progressTimeoutSec || 8) * progressTimeoutScale * 100) / 100;
    if (forceDomain) {
        nextProfile.domain = forceDomain;
    }
    nextProfile.policy = {
        radiusBonus,
        timeBudgetScale,
        progressTimeoutScale,
        forceDomain,
        consecutiveFailures: Math.max(0, Number(policy.consecutiveFailures || 0)),
        lastFailureCategory: policy.lastFailureCategory || null,
        lastFailureReason: policy.lastFailureReason || null,
    };
    return nextProfile;
}

function _resolveProfile(bot, goalType, override = {}) {
    const key = SEARCH_PROFILES[goalType] ? goalType : "generic";
    const policy = _searchPolicyFor(bot, key);
    return {
        ..._applyProfilePolicy(SEARCH_PROFILES[key], policy),
        ...(override || {}),
    };
}

function _resolveTargets(goalType) {
    return SEARCH_TARGETS[goalType] || SEARCH_TARGETS.generic;
}
