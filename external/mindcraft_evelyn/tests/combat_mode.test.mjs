import test from 'node:test';
import assert from 'node:assert/strict';
import {
    armorPointsFromNames,
    assessCombat,
    fightWithCustomPvp,
    selectCombatMode,
    selectCombatTarget,
    weaponPowerFromInventory,
} from '../src/agent/evelyn_combat.js';

function hostiles(name, count, distance = 3) {
    return Array.from({length: count}, (_, index) => ({
        id: index + 1,
        name,
        distance: distance + index * 0.2,
    }));
}

function snapshot(overrides = {}) {
    return {
        health: 20,
        hunger: 20,
        hostileDistance: 3,
        hostileName: 'zombie',
        hostileId: 1,
        hostileCount: 1,
        hostiles: hostiles('zombie', 1),
        hasMeleeWeapon: true,
        weaponPower: 6,
        armorPoints: 0,
        hasShield: false,
        hasBow: false,
        rangedWeapon: null,
        arrowCount: 0,
        inWater: false,
        ...overrides,
    };
}

test('published custom-PvP execution is gated by a data-driven readiness score', () => {
    assert.equal(assessCombat(snapshot()).tactic, 'fight');
    assert.equal(assessCombat(snapshot({
        hasMeleeWeapon: false,
        weaponPower: 0,
    })).reason, 'unarmed');
    assert.equal(assessCombat(snapshot({health: 6})).reason, 'critical_health');
});

test('equipment can make a six-zombie engagement viable without a mob-count exception', () => {
    const crowd = {
        hostileCount: 6,
        hostiles: hostiles('zombie', 6),
    };
    assert.equal(assessCombat(snapshot({
        ...crowd,
        weaponPower: 6,
        armorPoints: 0,
        hasShield: false,
    })).tactic, 'flee');
    assert.equal(assessCombat(snapshot({
        ...crowd,
        weaponPower: 6,
        armorPoints: 15,
        hasShield: true,
    })).tactic, 'fight');
});

test('ranged crowds and boss-grade threats still exceed ordinary equipment', () => {
    assert.equal(assessCombat(snapshot({
        hostileName: 'skeleton',
        hostileCount: 6,
        hostiles: hostiles('skeleton', 6),
        weaponPower: 6,
        armorPoints: 15,
        hasShield: true,
    })).tactic, 'flee');
    assert.equal(assessCombat(snapshot({
        hostileName: 'warden',
        hostileCount: 1,
        hostiles: hostiles('warden', 1),
        weaponPower: 8,
        armorPoints: 20,
        hasShield: true,
    })).tactic, 'flee');
});

test('target selection prioritizes danger and ranged mode keeps distance', () => {
    const entries = [
        {entity: {id: 1, name: 'zombie', isValid: true}, distance: 2},
        {entity: {id: 2, name: 'skeleton', isValid: true}, distance: 6},
        {entity: {id: 3, name: 'creeper', isValid: true}, distance: 5},
    ];
    const target = selectCombatTarget(entries);
    assert.equal(target.entity.name, 'creeper');
    assert.equal(selectCombatMode(snapshot({hasBow: true, arrowCount: 16}), target), 'bow');
});

test('loadout scoring reads actual item and equipped armor names', () => {
    assert.equal(weaponPowerFromInventory({iron_sword: 1, wooden_axe: 1}), 7);
    assert.equal(armorPointsFromNames([
        'iron_helmet',
        'iron_chestplate',
        'iron_leggings',
        'iron_boots',
    ]), 15);
});

test('combat loop starts custom melee control and always releases it after victory', async () => {
    let attacks = 0;
    let swordStops = 0;
    let bowStops = 0;
    let snapshots = 0;
    const sword = {name: 'iron_sword'};
    const bot = {
        health: 20,
        interrupt_code: false,
        heldItem: sword,
        inventory: {items: () => [sword]},
        equip: async () => {},
        pvp: {stop: () => {}},
        swordpvp: {
            weaponOfChoice: 'sword',
            attack: async () => { attacks += 1; },
            stop: () => { swordStops += 1; },
        },
        bowpvp: {
            attack: async () => {},
            stop: () => { bowStops += 1; },
        },
    };
    const result = await fightWithCustomPvp(bot, {
        snapshotProvider: () => {
            snapshots += 1;
            return snapshots === 1
                ? snapshot()
                : snapshot({
                    hostileDistance: null,
                    hostileName: null,
                    hostileId: null,
                    hostileCount: 0,
                    hostiles: [],
                });
        },
        hostileProvider: () => [{
            entity: {id: 1, name: 'zombie', isValid: true},
            distance: 3,
        }],
        timeoutMs: 1000,
    });

    assert.equal(result.success, true);
    assert.equal(result.reason, 'hostiles_cleared');
    assert.equal(attacks, 1);
    assert.ok(swordStops >= 1);
    assert.ok(bowStops >= 1);
});
