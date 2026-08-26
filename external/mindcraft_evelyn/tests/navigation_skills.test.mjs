import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import minecraftData from 'minecraft-data';
import Vec3 from 'vec3';

const registry = minecraftData('1.21.11');
const skillsUrl = process.env.MINDCRAFT_SKILLS_PATH
    ? pathToFileURL(process.env.MINDCRAFT_SKILLS_PATH).href
    : 'file:///app/mindcraft/src/agent/library/skills.js';
const mcdataUrl = process.env.MINDCRAFT_MCDATA_PATH
    ? pathToFileURL(process.env.MINDCRAFT_MCDATA_PATH).href
    : 'file:///app/mindcraft/src/utils/mcdata.js';
const require = createRequire(import.meta.url);
const mineflayer = require('mineflayer');
const {goals} = require('mineflayer-pathfinder');
const collectBlockModule = process.env.MINDCRAFT_COLLECTBLOCK_PATH || 'mineflayer-collectblock';
const {CollectBlock} = require(collectBlockModule);
const originalCreateBot = mineflayer.createBot;
const initializationBot = new EventEmitter();
Object.assign(initializationBot, {
    version: '1.21.11',
    registry,
    _client: Object.assign(new EventEmitter(), {write: () => {}}),
    loadPlugin: () => {},
    acceptResourcePack: () => {},
});
mineflayer.createBot = () => initializationBot;
try {
    const mcdata = await import(mcdataUrl);
    mcdata.initBot('navigation-skills-test').emit('login');
} finally {
    mineflayer.createBot = originalCreateBot;
}
const {craftRecipe, goToGoal, goToNearestBlock, goToNearestEntity, goToPlayer, moveAway, placeBlock, settleCraftOutput} = await import(skillsUrl);
const worldUrl = process.env.MINDCRAFT_WORLD_PATH
    ? pathToFileURL(process.env.MINDCRAFT_WORLD_PATH).href
    : 'file:///app/mindcraft/src/agent/library/world.js';
const {getNearestFreeSpace} = await import(worldUrl);

function createBot() {
    const bot = new EventEmitter();
    Object.assign(bot, {
        registry,
        entity: {position: new Vec3(0, 64, 0)},
        output: '',
        modes: {isOn: () => false, pause: () => {}, unpause: () => {}},
        targetDigBlock: null,
        blockAt: () => null,
        activateBlock: () => {},
        waitForTicks: async () => {},
    });
    return bot;
}

test('collectBlock vein discovery deduplicates fresh block instances by coordinate', () => {
    class Block {
        constructor(position, type) {
            this.position = position;
            this.type = type;
        }
    }

    const bot = createBot();
    const logType = registry.blocksByName.oak_log.id;
    const airType = registry.blocksByName.air.id;
    const key = ({x, y, z}) => `${x},${y},${z}`;
    const logPositions = new Set(['0,64,0', '0,65,0', '0,66,0']);
    bot.blockAt = (position) => new Block(
        position.clone(),
        logPositions.has(key(position)) ? logType : airType,
    );
    const collector = new CollectBlock(bot);

    const vein = collector.findFromVein(bot.blockAt(new Vec3(0, 66, 0)), 3, 4, 1);

    assert.equal(vein.length, 3);
    assert.deepEqual(vein.map(({position}) => position.y).sort(), [64, 65, 66]);
});

test('collectBlock blocks-first batch mines every block before following a drop', async () => {
    class Block {
        constructor(position) {
            this.position = position;
            this.type = registry.blocksByName.oak_log.id;
        }

        canHarvest() { return true; }
    }

    class Entity {
        constructor(position) {
            this.position = position;
            this.isValid = true;
        }
    }

    const bot = createBot();
    bot.entity.position = new Vec3(7.5, 100, 0.5);
    bot.inventory = {emptySlotCount: () => 1, items: () => []};
    bot.heldItem = null;
    const positions = [102, 101, 100].map((y) => new Vec3(8, y, 0));
    const key = ({x, y, z}) => `${x},${y},${z}`;
    const liveBlocks = new Set(positions.map(key));
    const events = [];
    bot.canDigBlock = () => true;
    bot.canSeeBlock = () => true;
    bot.blockAt = (position) => liveBlocks.has(key(position))
        ? new Block(position.clone())
        : {type: registry.blocksByName.air.id, position: position.clone()};
    bot.tool = {equipForBlock: async () => {}};
    bot.pathfinder = {
        movements: null,
        goal: null,
        setMovements(movements) { this.movements = movements; },
        stop: () => assert.fail('fresh batch must not be cancelled'),
        async goto(goal) {
            assert.equal(goal.constructor.name, 'GoalFollow');
            events.push('entity');
            this.goal = goal;
            goal.entity.isValid = false;
            bot.emit('entityGone', goal.entity);
        },
        setGoal(goal) { this.goal = goal; },
    };
    const digAttempts = new Map();
    bot.dig = async (block) => {
        const attempt = (digAttempts.get(key(block.position)) || 0) + 1;
        digAttempts.set(key(block.position), attempt);
        events.push(`block:${block.position.y}`);
        if (block.position.y === 100 && attempt === 1) return;
        liveBlocks.delete(key(block.position));
        const item = new Entity(block.position.offset(0.5, 0.5, 0.5));
        bot.emit('itemDrop', item);
        item.position = new Vec3(9.1, 100, 0.1);
        setImmediate(() => {
            for (let tick = 0; tick < 10; tick++) bot.emit('physicsTick');
        });
    };
    const collector = new CollectBlock(bot);
    collector.movements.safeToBreak = () => true;

    await collector.collect(positions.map((position) => new Block(position)), {blocksFirst: true});

    assert.deepEqual(events.slice(0, 4), ['block:100', 'block:100', 'block:101', 'block:102']);
    assert.equal(digAttempts.get('8,100,0'), 2);
    assert.equal(digAttempts.get('8,101,0'), 1);
    assert.equal(digAttempts.get('8,102,0'), 1);
    assert.equal(events.filter((event) => event === 'entity').length, 3);
});

test('collectBlock blocks-first retries one unconfirmed dig and keeps legacy collection single-shot', async (t) => {
    class Block {
        constructor(position) {
            this.position = position;
            this.type = registry.blocksByName.oak_log.id;
        }

        canHarvest() { return true; }
    }

    const createHarness = () => {
        const bot = createBot();
        const block = new Block(new Vec3(8, 100, 0));
        let digs = 0;
        const waits = [];
        bot.entity.position = new Vec3(7.5, 100, 0.5);
        bot.inventory = {emptySlotCount: () => 1, items: () => []};
        bot.heldItem = null;
        bot.canDigBlock = () => true;
        bot.canSeeBlock = () => true;
        bot.blockAt = () => block;
        bot.tool = {equipForBlock: async () => {}};
        bot.pathfinder = {
            movements: null,
            setMovements(movements) { this.movements = movements; },
            goto: async () => assert.fail('direct block must not path'),
            stop: () => assert.fail('fresh collection must not be cancelled'),
        };
        bot.dig = async () => { digs++; };
        bot.waitForTicks = async (ticks) => { waits.push(ticks); };
        const collector = new CollectBlock(bot);
        collector.movements.safeToBreak = () => true;
        return {block, bot, collector, digs: () => digs, waits};
    };

    await t.test('blocks-first fails closed after exactly two attempts', async () => {
        const harness = createHarness();
        await assert.rejects(
            harness.collector.collect(harness.block, {blocksFirst: true}),
            (error) => error?.name === 'Timeout',
        );
        assert.equal(harness.digs(), 2);
        assert.deepEqual(harness.waits, [10, 1, 10]);
        assert.equal(harness.collector.targets.empty, true);
        assert.equal(harness.bot.listenerCount('itemDrop'), 0);
    });

    await t.test('legacy collection remains one attempt', async () => {
        const harness = createHarness();
        await harness.collector.collect(harness.block);
        assert.equal(harness.digs(), 1);
        assert.deepEqual(harness.waits, [10]);
    });
});

test('collectBlock blocks-first batch stops before the next target after an interrupt', async () => {
    class Block {
        constructor(position) {
            this.position = position;
            this.type = registry.blocksByName.oak_log.id;
        }

        canHarvest() { return true; }
    }

    const bot = createBot();
    const positions = [102, 101, 100].map((y) => new Vec3(8, y, 0));
    const liveBlocks = new Set(positions.map(({x, y, z}) => `${x},${y},${z}`));
    const dug = [];
    bot.entity.position = new Vec3(7.5, 102, 0.5);
    bot.inventory = {emptySlotCount: () => 1, items: () => []};
    bot.heldItem = null;
    bot.canDigBlock = () => true;
    bot.canSeeBlock = () => true;
    bot.blockAt = (position) => liveBlocks.has(`${position.x},${position.y},${position.z}`)
        ? new Block(position.clone())
        : {type: registry.blocksByName.air.id, position: position.clone()};
    bot.tool = {equipForBlock: async () => {}};
    bot.pathfinder = {
        movements: null,
        goal: null,
        setMovements(movements) { this.movements = movements; },
        stop: () => assert.fail('fresh batch must not be cancelled'),
        goto: async () => assert.fail('direct blocks must not path'),
        setGoal(goal) { this.goal = goal; },
    };
    bot.dig = async (block) => {
        dug.push(block.position.y);
        liveBlocks.delete(`${block.position.x},${block.position.y},${block.position.z}`);
        bot.interrupt_code = true;
        setImmediate(() => {
            for (let tick = 0; tick < 10; tick++) bot.emit('physicsTick');
        });
    };
    const collector = new CollectBlock(bot);
    collector.movements.safeToBreak = () => true;

    await collector.collect(positions.map((position) => new Block(position)), {blocksFirst: true});

    assert.deepEqual(dug, [102]);
    assert.equal(collector.targets.empty, true);
});

async function collectSingleBlock({botPosition, directDiggable, visible}) {
    class Block {
        constructor(position) {
            this.position = position;
            this.type = registry.blocksByName.oak_log.id;
        }

        canHarvest() { return true; }
    }

    class Entity {
        constructor(position) {
            this.position = position;
            this.isValid = true;
        }
    }

    const bot = createBot();
    const block = new Block(new Vec3(8, 100, 0));
    const currentBlock = new Block(block.position);
    const collateral = [
        {block: new Block(block.position.offset(0, 1, 0)), present: true},
        {block: new Block(block.position.offset(0, 2, 0)), present: true},
    ];
    const item = new Entity(block.position.offset(0.5, 0.5, 0.5));
    bot.entity.position = botPosition;
    bot.world = {raycast: () => visible ? {position: block.position} : null};
    bot.canDigBlock = (target) => {
        assert.equal(target, block);
        return directDiggable;
    };
    bot.canSeeBlock = (target) => {
        assert.equal(target, block);
        return visible;
    };
    bot.inventory = {emptySlotCount: () => 1, items: () => []};
    bot.heldItem = null;
    let blockPresent = true;
    let blockGotos = 0;
    const blockGotoCanDig = [];
    let itemGotos = 0;
    let mines = 0;
    let minedTarget = null;
    bot.blockAt = (position) => {
        if (position.equals(block.position)) return blockPresent ? currentBlock : {type: 0};
        const candidate = collateral.find(({block: target}) => position.equals(target.position));
        return candidate?.present ? candidate.block : {type: 0};
    };
    bot.tool = {
        async equipForBlock(target) { assert.equal(target, currentBlock); },
    };
    bot.pathfinder = {
        movements: null,
        goal: null,
        setMovements(movements) { this.movements = movements; },
        stop: () => assert.fail('fresh collection must not be cancelled'),
        async goto(goal) {
            this.goal = goal;
            if (goal.constructor.name === 'GoalLookAtBlock') {
                blockGotos++;
                blockGotoCanDig.push(this.movements.canDig);
                if (this.movements.canDig) {
                    for (const candidate of collateral) candidate.present = false;
                }
                bot.entity.position = block.position.offset(0, 0, -1);
                return;
            }
            assert.equal(goal.constructor.name, 'GoalFollow');
            assert.equal(goal.entity, item);
            assert.equal(this.movements.canDig, false);
            itemGotos++;
            bot.emit('entityGone', item);
        },
        setGoal(goal) { this.goal = goal; },
    };
    bot.dig = async (target) => {
        assert.equal(target, currentBlock);
        assert.equal(blockPresent, true);
        assert.equal(bot.pathfinder.movements.canDig, true);
        mines++;
        minedTarget = target;
        blockPresent = false;
        bot.emit('itemDrop', item);
        setImmediate(() => {
            for (let tick = 0; tick < 10; tick++) bot.emit('physicsTick');
        });
    };

    const collector = new CollectBlock(bot);
    collector.movements.safeToBreak = (target) => collector.movements.canDig && target === currentBlock;
    const currentPosition = bot.entity.position.floored();
    const floorGoal = new goals.GoalLookAtBlock(block.position, bot.world);
    const floorGoalReached = floorGoal.isEnd(currentPosition) || floorGoal.isEnd(currentPosition.offset(0, 1, 0));
    await collector.collect(block);
    return {
        bot,
        blockGotos,
        blockGotoCanDig,
        collectorCanDig: collector.movements.canDig,
        collateralPresent: collateral.map(({present}) => present),
        floorGoalReached,
        itemGotos,
        minedFreshBlock: minedTarget === currentBlock,
        mines,
    };
}

test('collectBlock skips a false floored goal when the block is directly visible and diggable', async () => {
    const result = await collectSingleBlock({
        botPosition: new Vec3(8.5, 100, -4.01),
        directDiggable: true,
        visible: true,
    });

    assert.equal(result.floorGoalReached, false);
    assert.equal(result.blockGotos, 0);
    assert.equal(result.mines, 1);
    assert.equal(result.minedFreshBlock, true);
    assert.equal(result.itemGotos, 1);
    assert.deepEqual(result.collateralPresent, [true, true]);
    assert.equal(result.collectorCanDig, true);
    assert.equal(result.bot.listenerCount('itemDrop'), 0);
    assert.equal(result.bot.listenerCount('physicsTick'), 0);
    assert.equal(result.bot.listenerCount('entityGone'), 0);
});

test('collectBlock uses a no-dig approach for an occluded block and restores mining', async () => {
    const result = await collectSingleBlock({
        botPosition: new Vec3(8.5, 100, -0.3183946637),
        directDiggable: true,
        visible: false,
    });

    assert.equal(result.floorGoalReached, false);
    assert.equal(result.blockGotos, 1);
    assert.deepEqual(result.blockGotoCanDig, [false]);
    assert.equal(result.mines, 1);
    assert.equal(result.minedFreshBlock, true);
    assert.equal(result.itemGotos, 1);
    assert.deepEqual(result.collateralPresent, [true, true]);
    assert.equal(result.collectorCanDig, true);
});

test('collectBlock accepts pickup when the adjacent goal rejects after entityGone', async () => {
    class Entity {
        constructor(position) {
            this.position = position;
            this.isValid = true;
        }
    }

    const bot = createBot();
    const item = new Entity(new Vec3(21.43, 100, 0.85));
    bot.entity.position = new Vec3(20, 100, 0);
    bot.inventory = {emptySlotCount: () => 1, items: () => []};
    bot.tool = {};
    let selectedGoal = null;
    let ownedGoalClears = 0;
    bot.pathfinder = {
        movements: null,
        setMovements(movements) { this.movements = movements; },
        stop: () => assert.fail('fresh collection must not be cancelled'),
        async goto(goal) {
            selectedGoal = goal;
            this.goal = goal;
            assert.equal(this.movements.canDig, false);
            assert.equal(new goal.constructor(item, 0).isEnd(bot.entity.position), false);
            assert.equal(goal.rangeSq, 1);
            assert.equal(goal.isEnd(bot.entity.position), true);
            bot.emit('entityGone', item);
            const error = new Error('item disappeared during pathing');
            error.name = 'GoalChanged';
            throw error;
        },
        setGoal(goal) {
            if (goal === null) ownedGoalClears++;
            this.goal = goal;
        },
    };

    const collector = new CollectBlock(bot);
    await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => reject(new Error('collectBlock did not resolve after pickup')), 500);
        collector.collect(item).then(
            () => { clearTimeout(timeout); resolve(); },
            (error) => { clearTimeout(timeout); reject(error); },
        );
    });

    assert.equal(selectedGoal.constructor.name, 'GoalFollow');
    assert.equal(selectedGoal.entity, item);
    assert.equal(ownedGoalClears, 1);
    assert.equal(bot.pathfinder.goal, null);
    assert.equal(collector.movements.canDig, true);
    assert.equal(bot.listenerCount('entityGone'), 0);
});

test('collectBlock advances from an adjacent item cell to the exact pickup cell', async () => {
    class Entity {
        constructor(position) {
            this.position = position;
            this.isValid = true;
        }
    }

    const bot = createBot();
    const item = new Entity(new Vec3(21.43, 100, 0.85));
    bot.entity.position = new Vec3(20, 100, 0);
    bot.inventory = {emptySlotCount: () => 1, items: () => []};
    bot.tool = {};
    const selectedGoals = [];
    let ownedGoalClears = 0;
    bot.pathfinder = {
        movements: null,
        setMovements(movements) { this.movements = movements; },
        stop: () => assert.fail('fresh collection must not be cancelled'),
        async goto(goal) {
            selectedGoals.push(goal);
            this.goal = goal;
            assert.equal(this.movements.canDig, false);
            if (goal.rangeSq === 1) return;
            assert.equal(goal.rangeSq, 0);
            item.isValid = false;
            bot.emit('entityGone', item);
        },
        setGoal(goal) {
            if (goal === null) ownedGoalClears++;
            this.goal = goal;
        },
    };

    const collector = new CollectBlock(bot);
    await collector.collect(item);

    assert.deepEqual(selectedGoals.map((goal) => goal.rangeSq), [1, 0]);
    assert.equal(ownedGoalClears, 1);
    assert.equal(bot.pathfinder.goal, null);
    assert.equal(collector.movements.canDig, true);
    assert.equal(bot.listenerCount('entityGone'), 0);
});

test('collectBlock exact item timeout cleans its listener and preserves a foreign goal', async () => {
    class Entity {
        constructor(position) {
            this.position = position;
            this.isValid = true;
        }
    }

    const bot = createBot();
    const item = new Entity(new Vec3(8, 64, 0));
    const foreignGoal = {owner: 'emergency'};
    bot.inventory = {emptySlotCount: () => 1, items: () => []};
    bot.tool = {};
    const selectedGoals = [];
    let forcedClears = 0;
    bot.pathfinder = {
        movements: null,
        setMovements(movements) { this.movements = movements; },
        stop: () => assert.fail('fresh collection must not be cancelled'),
        goto(goal) {
            selectedGoals.push(goal);
            assert.equal(this.movements.canDig, false);
            if (goal.rangeSq === 1) {
                this.goal = goal;
                return Promise.resolve();
            }
            assert.equal(goal.rangeSq, 0);
            this.goal = foreignGoal;
            return new Promise(() => {});
        },
        setGoal(goal) {
            if (goal === null) forcedClears++;
            this.goal = goal;
        },
    };
    const collector = new CollectBlock(bot);
    const originalSetTimeout = globalThis.setTimeout;
    let timeoutMs = null;
    globalThis.setTimeout = (callback, delay) => {
        timeoutMs = delay;
        return originalSetTimeout(callback, 0);
    };
    try {
        await assert.rejects(collector.collect(item), {name: 'Timeout'});
    } finally {
        globalThis.setTimeout = originalSetTimeout;
    }

    assert.equal(timeoutMs, 8000);
    assert.deepEqual(selectedGoals.map((goal) => goal.rangeSq), [1, 0]);
    assert.equal(collector.targets.empty, true);
    assert.equal(collector.movements.canDig, true);
    assert.equal(bot.listenerCount('entityGone'), 0);
    assert.equal(bot.pathfinder.goal, foreignGoal);
    assert.equal(forcedClears, 0);
});

test('crafting-slot residue waits for output to reach storage', async () => {
    const bot = createBot();
    const slots = Array(46).fill(null);
    slots[0] = {name: 'stick', count: 4};
    slots[45] = {name: 'stick', count: 4};
    bot.inventory = {slots, items: () => slots.slice(9, 45).filter(Boolean)};
    let closes = 0;
    bot.closeWindow = (window) => {
        assert.equal(window, bot.inventory);
        closes++;
    };
    const delays = [];
    const originalSetTimeout = globalThis.setTimeout;
    globalThis.setTimeout = (resolve, delay) => {
        delays.push(delay);
        if (delays.length === 2) slots[9] = {name: 'stick', count: 4};
        resolve();
    };
    try {
        assert.equal(await settleCraftOutput(bot, 'stick', 0, null), true);
    } finally {
        globalThis.setTimeout = originalSetTimeout;
    }
    assert.equal(closes, 1);
    assert.deepEqual(delays, [100, 100]);
});

test('permanent crafting-slot residue never satisfies the output postcondition', async () => {
    const bot = createBot();
    const slots = Array(46).fill(null);
    slots[0] = {name: 'stick', count: 4};
    slots[45] = {name: 'stick', count: 4};
    bot.inventory = {slots, items: () => slots.slice(9, 45).filter(Boolean)};
    let closes = 0;
    bot.closeWindow = () => { closes++; };
    const delays = [];
    const originalSetTimeout = globalThis.setTimeout;
    globalThis.setTimeout = (resolve, delay) => {
        delays.push(delay);
        resolve();
    };
    try {
        assert.equal(await settleCraftOutput(bot, 'stick', 0, null), false);
    } finally {
        globalThis.setTimeout = originalSetTimeout;
    }
    assert.equal(closes, 1);
    assert.deepEqual(delays, Array(10).fill(100));
});

test('zero requested crafts fail before touching the crafting API', async () => {
    const bot = createBot();
    const slots = Array(46).fill(null);
    slots[9] = {name: 'oak_planks', count: 2};
    bot.inventory = {slots, items: () => slots.slice(9, 45).filter(Boolean)};
    const recipe = {inShape: [[{id: registry.itemsByName.oak_planks.id, count: 1}]]};
    bot.recipesFor = () => [recipe];
    let crafts = 0;
    bot.craft = async () => { crafts++; };
    bot.armorManager = {equipAll: () => {}};

    assert.equal(await craftRecipe(bot, 'stick', 0), false);
    assert.equal(crafts, 0);
});

test('placed crafting table is cleaned up once when crafting throws', async () => {
    const bot = createBot();
    const target = new Vec3(3, 64, 0);
    const slots = Array(46).fill(null);
    slots[9] = {name: 'crafting_table', count: 1};
    slots[10] = {name: 'oak_planks', count: 3};
    bot.inventory = {
        slots,
        items: () => slots.slice(9, 45).filter(Boolean),
        findInventoryItem: (name) => slots.find((item) => item?.name === name) || null,
    };
    bot.game = {gameMode: 'survival'};
    let tablePlaced = false;
    let cleanups = 0;
    bot.findBlocks = ({maxDistance, count}) => {
        if (maxDistance === 6 && count === 1000) return [target];
        return tablePlaced ? [target] : [];
    };
    bot.blockAt = (position) => {
        if (position.x === target.x && position.y === target.y - 1 && position.z === target.z) {
            return {name: 'stone', position, drops: [1], diggable: true};
        }
        if (position.equals(target)) {
            if (tablePlaced) return {name: 'crafting_table', position, canHarvest: () => true};
            return {name: 'air', position, drops: [], diggable: true};
        }
        return {name: 'air', position, drops: [], diggable: true};
    };
    bot.equip = async () => {};
    bot.lookAt = async () => {};
    bot.placeBlock = async () => { tablePlaced = true; };
    bot.tool = {equipForBlock: async () => {}};
    bot.collectBlock = {collect: async () => { cleanups++; tablePlaced = false; }};
    bot.heldItem = null;
    const recipe = {inShape: [[{id: registry.itemsByName.oak_planks.id, count: 1}]]};
    bot.recipesFor = (_item, _metadata, _count, table) => table ? [recipe] : [];
    bot.craft = async () => { throw new Error('craft failed'); };
    const originalSetTimeout = globalThis.setTimeout;
    globalThis.setTimeout = (resolve) => resolve();
    try {
        await assert.rejects(craftRecipe(bot, 'wooden_pickaxe'), /craft failed/);
    } finally {
        globalThis.setTimeout = originalSetTimeout;
    }
    assert.equal(cleanups, 1);
    assert.equal(tablePlaced, false);
});

test('free-space search skips the bot occupied placement radius', () => {
    const bot = createBot();
    const positions = [
        new Vec3(0, 64, 0),
        new Vec3(1, 64, 0),
        new Vec3(2, 64, 0),
        new Vec3(3, 64, 0),
    ];
    bot.findBlocks = () => positions;
    bot.blockAt = (position) => {
        if (position.y === 63) return {name: 'stone', drops: [1], diggable: true};
        if (position.x === 2) return {name: 'vine', drops: [], diggable: true};
        return {name: 'air', drops: [], diggable: true};
    };

    assert.deepEqual(getNearestFreeSpace(bot, 1, 6, 2), positions[3]);
});

test('block placement uses bounded composite relocation when the target overlaps the bot', async () => {
    const bot = createBot();
    const target = new Vec3(0, 64, 0);
    let movementGoal = null;
    let placements = 0;
    bot.game = {gameMode: 'survival'};
    bot.inventory = {
        findInventoryItem: () => ({name: 'crafting_table'}),
    };
    bot.blockAt = (position) => {
        if (position.y === 63) return {name: 'stone', position, drops: [1], diggable: true};
        return {name: 'air', position, drops: [], diggable: true};
    };
    bot.pathfinder = {
        getPathTo: () => assert.fail('supplied relocation movements must skip preview'),
        setMovements: () => {},
        async goto(goal) {
            movementGoal = goal;
            bot.entity.position = new Vec3(2, 64, 0);
        },
    };
    bot.equip = async () => {};
    bot.lookAt = async () => {};
    bot.placeBlock = async () => { placements++; };

    assert.equal(await placeBlock(bot, 'crafting_table', target.x, target.y, target.z), true);
    assert.equal(movementGoal.constructor.name, 'GoalCompositeAny');
    assert.equal(placements, 1);
});

test('failed owned navigation is stopped and cannot leak into later work', async () => {
    const bot = createBot();
    const goal = {};
    let stops = 0;
    bot.pathfinder = {
        goal,
        getPathTo: () => ({status: 'success'}),
        setMovements: () => {},
        async goto() {
            const error = new Error('timed out');
            error.name = 'Timeout';
            throw error;
        },
        stop() {
            stops++;
            this.goal = null;
        },
    };

    await assert.rejects(goToGoal(bot, goal), {name: 'Timeout'});
    assert.equal(stops, 1);
    assert.equal(bot.pathfinder.goal, null);
    assert.equal(bot.listenerCount('path_reset'), 0);
});

test('far block placement converts a path timeout into a clean failure', async () => {
    const bot = createBot();
    const target = new Vec3(6, 64, 0);
    let stops = 0;
    let placements = 0;
    bot.game = {gameMode: 'survival'};
    bot.inventory = {findInventoryItem: () => ({name: 'crafting_table'})};
    bot.blockAt = (position) => position.y === 63
        ? {name: 'stone', position, drops: [1], diggable: true}
        : {name: 'air', position, drops: [], diggable: true};
    bot.pathfinder = {
        goal: null,
        getPathTo: () => ({status: 'success'}),
        setMovements: () => {},
        async goto(goal) {
            this.goal = goal;
            const error = new Error('timed out');
            error.name = 'Timeout';
            throw error;
        },
        stop() {
            stops++;
            this.goal = null;
        },
    };
    bot.equip = async () => {};
    bot.lookAt = async () => {};
    bot.placeBlock = async () => { placements++; };

    assert.equal(await placeBlock(bot, 'crafting_table', target.x, target.y, target.z), false);
    assert.equal(stops, 1);
    assert.equal(placements, 0);
    assert.equal(bot.listenerCount('path_reset'), 0);
});

test('partial non-destructive preview remains non-destructive', async () => {
    const bot = createBot();
    const previewed = [];
    let selected = null;
    bot.pathfinder = {
        getPathTo(movements) {
            previewed.push(movements);
            return {status: previewed.length === 1 ? 'partial' : 'success'};
        },
        setMovements(movements) {
            selected = movements;
        },
        async goto() {},
    };

    assert.equal(await goToGoal(bot, {}), true);
    assert.equal(previewed.length, 1);
    assert.equal(selected, previewed[0]);
    assert.match(bot.output, /Found non-destructive path/);
});

test('partial preview retries NoPath and Timeout once with destructive movements', async (t) => {
    for (const errorName of ['NoPath', 'Timeout']) {
        await t.test(errorName, async () => {
            const bot = createBot();
            const goal = {};
            const selected = [];
            let gotoCalls = 0;
            bot.pathfinder = {
                goal,
                getPathTo: () => ({status: errorName === 'NoPath' ? 'partial' : 'success'}),
                setMovements: (movements) => { selected.push(movements); },
                async goto() {
                    if (++gotoCalls === 1) {
                        const error = new Error(errorName);
                        error.name = errorName;
                        throw error;
                    }
                },
            };

            assert.equal(await goToGoal(bot, goal), true);
            assert.equal(gotoCalls, 2);
            assert.equal(selected.length, 2);
            assert.notEqual(selected[0], selected[1]);
            assert.match(bot.output, /retrying with destructive movements/);
        });
    }
});

test('goal preemption never triggers the destructive retry', async () => {
    const bot = createBot();
    const goal = {};
    let gotoCalls = 0;
    bot.pathfinder = {
        goal,
        getPathTo: () => ({status: 'partial'}),
        setMovements: () => {},
        async goto() {
            gotoCalls++;
            this.goal = {};
            const error = new Error('superseded');
            error.name = 'GoalChanged';
            throw error;
        },
        stop: () => assert.fail('a superseding goal must not be stopped'),
    };

    await assert.rejects(goToGoal(bot, goal), {name: 'GoalChanged'});
    assert.equal(gotoCalls, 1);
});

test('two stuck resets stop only the owned goal and remove the listener', async () => {
    const bot = createBot();
    const goal = {};
    let rejectGoto;
    let stopCalls = 0;
    bot.pathfinder = {
        goal,
        getPathTo: () => assert.fail('supplied movements must skip preview'),
        setMovements: (movements) => { assert.equal(movements, suppliedMovements); },
        goto: () => new Promise((_, reject) => { rejectGoto = reject; }),
        stop() {
            stopCalls++;
            this.goal = null;
            rejectGoto(new Error('stopped after repeated stuck resets'));
        },
    };

    const suppliedMovements = {};
    const pending = goToGoal(bot, goal, suppliedMovements);
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(bot.listenerCount('path_reset'), 1);
    bot.emit('path_reset', 'stuck');
    assert.equal(stopCalls, 0);
    bot.emit('path_reset', 'stuck');
    await assert.rejects(pending, /repeated stuck/);
    assert.equal(stopCalls, 1);
    assert.equal(bot.listenerCount('path_reset'), 0);
});

test('setMovements failure removes the stuck listener', async () => {
    const bot = createBot();
    bot.pathfinder = {
        getPathTo: () => ({status: 'success'}),
        setMovements: () => { throw new Error('set movements failed'); },
        goto: () => assert.fail('goto must not run'),
    };

    await assert.rejects(goToGoal(bot, {}), /set movements failed/);
    assert.equal(bot.listenerCount('path_reset'), 0);
});

test('legacy goToPlayer boolean does not become a movements object', async () => {
    const bot = createBot();
    const player = {position: new Vec3(5, 64, 0)};
    bot.username = 'evelyn';
    bot.players = {alex: {entity: player}};
    let selected = null;
    bot.pathfinder = {
        getPathTo: () => ({status: 'success'}),
        setMovements: (movements) => { selected = movements; },
        async goto() { bot.entity.position = player.position.clone(); },
    };

    await goToPlayer(bot, 'alex', 2);
    assert.equal(typeof selected, 'object');
    assert.notEqual(selected, true);
});

test('stuck resets from a superseding goal do not stop its owner', async () => {
    const bot = createBot();
    const goal = {};
    let resolveGoto;
    let stopCalls = 0;
    bot.pathfinder = {
        goal,
        getPathTo: () => ({status: 'success'}),
        setMovements: () => {},
        goto: () => new Promise((resolve) => { resolveGoto = resolve; }),
        stop: () => { stopCalls++; },
    };

    const pending = goToGoal(bot, goal);
    await new Promise((resolve) => setImmediate(resolve));
    bot.pathfinder.goal = {};
    bot.emit('path_reset', 'stuck');
    bot.emit('path_reset', 'stuck');
    assert.equal(stopCalls, 0);
    resolveGoto();
    await pending;
    assert.equal(bot.listenerCount('path_reset'), 0);
});

test('nearest block tries four source candidates and reports failed movement', async () => {
    const bot = createBot();
    const positions = [
        new Vec3(8, 64, 0),
        new Vec3(12, 64, 0),
        new Vec3(16, 64, 0),
        new Vec3(20, 64, 0),
    ];
    let requestedCount = 0;
    let selectedGoal = null;
    bot.findBlocks = ({matching, count}) => {
        requestedCount = count;
        assert.equal(matching({name: 'water', metadata: 0}), true);
        assert.equal(matching({name: 'water', metadata: 1}), false);
        return positions;
    };
    bot.blockAt = (position) => ({name: 'water', metadata: 0, position});
    bot.pathfinder = {
        getPathTo: () => ({status: 'success'}),
        setMovements: () => {},
        async goto(goal) { selectedGoal = goal; },
    };

    assert.equal(await goToNearestBlock(bot, 'water'), false);
    assert.equal(requestedCount, 32);
    assert.equal(selectedGoal.constructor.name, 'GoalCompositeAny');
    assert.equal(selectedGoal.goals.length, 4);
    assert.match(bot.output, /Unable to reach any water candidate/);
});

test('nearest block keeps a separated fifth candidate and can reach it', async () => {
    const bot = createBot();
    const positions = [
        new Vec3(8, 64, 0),
        new Vec3(8, 64, 1),
        new Vec3(9, 64, 0),
        new Vec3(9, 64, 1),
        new Vec3(20, 64, 0),
    ];
    bot.findBlocks = () => positions;
    bot.blockAt = (position) => ({name: 'water', metadata: 0, position});
    let selectedGoal = null;
    bot.pathfinder = {
        getPathTo: () => ({status: 'success'}),
        setMovements: () => {},
        async goto(goal) {
            selectedGoal = goal;
            bot.entity.position = positions[4].clone();
        },
    };

    assert.equal(await goToNearestBlock(bot, 'water'), true);
    assert.equal(selectedGoal.goals.length, 2);
    assert.equal(selectedGoal.goals[1].x, positions[4].x);
});

test('nearest entity follows the live entity and reports failed movement', async () => {
    const bot = createBot();
    const entity = {name: 'cow', position: new Vec3(8, 64, 0)};
    let selectedGoal = null;
    bot.nearestEntity = (predicate) => predicate(entity) ? entity : null;
    bot.pathfinder = {
        getPathTo: () => ({status: 'success'}),
        setMovements: () => {},
        async goto(goal) {
            selectedGoal = goal;
            entity.position = new Vec3(12, 64, 0);
            assert.equal(goal.hasChanged(), true);
        },
    };

    assert.equal(await goToNearestEntity(bot, 'cow'), false);
    assert.equal(selectedGoal.constructor.name, 'GoalFollow');
    assert.equal(selectedGoal.entity, entity);
    assert.match(bot.output, /Unable to reach cow/);
});

test('moveAway uses one composite goal and enforces requested distance', async () => {
    for (const distance of [2, 5, 64]) {
        const bot = createBot();
        let selectedGoal = null;
        let gotoCalls = 0;
        bot.pathfinder = {
            getPathTo: () => assert.fail('supplied safe movements must skip preview'),
            setMovements: () => {},
            async goto(goal) {
                gotoCalls++;
                selectedGoal = goal;
                bot.entity.position = new Vec3(distance, 64, 0);
            },
        };

        assert.equal(await moveAway(bot, distance), true);
        assert.equal(gotoCalls, 1);
        assert.equal(selectedGoal.constructor.name, 'GoalCompositeAny');
        assert.equal(selectedGoal.goals.length, 8);
        assert.ok(selectedGoal.goals.some((goal) => goal.x === distance + 2));
    }
    assert.equal(await moveAway(createBot(), 0), true);
});

test('moveAway supplied movements still abort after two owned stuck resets', async () => {
    const bot = createBot();
    let rejectGoto;
    let stopCalls = 0;
    bot.pathfinder = {
        goal: null,
        getPathTo: () => assert.fail('supplied safe movements must skip preview'),
        setMovements: () => {},
        goto(goal) {
            this.goal = goal;
            return new Promise((_, reject) => { rejectGoto = reject; });
        },
        stop() {
            stopCalls++;
            this.goal = null;
            rejectGoto(new Error('moveAway stopped after repeated stuck resets'));
        },
    };

    const pending = moveAway(bot, 5);
    await new Promise((resolve) => setImmediate(resolve));
    bot.emit('path_reset', 'stuck');
    bot.emit('path_reset', 'stuck');
    await assert.rejects(pending, /repeated stuck/);
    assert.equal(stopCalls, 1);
    assert.equal(bot.listenerCount('path_reset'), 0);
});

test('moveAway rejects a short move instead of weakening the distance contract', async () => {
    const bot = createBot();
    bot.pathfinder = {
        getPathTo: () => assert.fail('supplied safe movements must skip preview'),
        setMovements: () => {},
        async goto() { bot.entity.position = new Vec3(8, 64, 0); },
    };

    await assert.rejects(moveAway(bot, 20), /8\.0 of 20 requested/);
});
