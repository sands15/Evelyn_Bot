#!/usr/bin/env node
'use strict';

const readline = require('readline');

let mineflayer = null;
let bot = null;
try {
  mineflayer = require('mineflayer');
} catch (err) {
  mineflayer = null;
}

const config = {
  host: process.env.MINEFLAYER_HOST || '127.0.0.1',
  port: Number(process.env.MINEFLAYER_PORT || 25565),
  username: process.env.MINEFLAYER_USERNAME || 'EvelynBot',
  password: process.env.MINEFLAYER_PASSWORD || undefined,
  version: process.env.MINEFLAYER_VERSION || false,
};

const state = {
  connected: false,
  active: false,
  environment: 'minecraft',
  health: 20,
  hunger: 20,
  hostiles_nearby: 0,
  inventory: {},
  held_item: null,
  players: {},
  position: null,
  last_action: null,
  last_error: null,
  capabilities: ['retreat', 'heal_or_regroup', 'find_food_source', 'consume_food', 'gather_logs', 'craft_basic_tools', 'gather_basic_resources'],
};

function emit(id, ok, payload) {
  process.stdout.write(JSON.stringify(ok ? { id, ok: true, result: payload } : { id, ok: false, error: String(payload) }) + '\n');
}

function snapshotPlayers() {
  if (!bot || !bot.players) return {};
  const out = {};
  for (const [name, info] of Object.entries(bot.players)) {
    out[name] = {
      username: name,
      ping: info?.ping ?? null,
      entityId: info?.entity?.id ?? null,
      heldItem: info?.entity?.heldItem ? info.entity.heldItem.name : null,
      position: info?.entity?.position ? {
        x: info.entity.position.x,
        y: info.entity.position.y,
        z: info.entity.position.z,
      } : null,
    };
  }
  return out;
}

function snapshotInventory() {
  if (!bot || !bot.inventory || !Array.isArray(bot.inventory.items())) return {};
  const counts = {};
  for (const item of bot.inventory.items()) {
    counts[item.name] = (counts[item.name] || 0) + (item.count || 1);
  }
  return counts;
}

function updateState() {
  if (!bot) return;
  state.connected = !!bot.player;
  state.active = !!bot.player;
  state.health = bot.health ?? state.health;
  state.hunger = bot.food ?? state.hunger;
  state.inventory = snapshotInventory();
  state.players = snapshotPlayers();
  state.held_item = bot.heldItem ? bot.heldItem.name : null;
  state.position = bot.entity?.position ? {
    x: bot.entity.position.x,
    y: bot.entity.position.y,
    z: bot.entity.position.z,
  } : null;
  state.hostiles_nearby = 0;
  if (bot.entities) {
    for (const entity of Object.values(bot.entities)) {
      if (entity?.type === 'mob' && entity?.position && bot.entity?.position) {
        const dist = bot.entity.position.distanceTo(entity.position);
        if (dist <= 12) state.hostiles_nearby += 1;
      }
    }
  }
}

async function ensureConnected() {
  if (bot || !mineflayer) return;
  bot = mineflayer.createBot(config);
  bot.once('spawn', () => {
    updateState();
  });
  bot.on('health', updateState);
  bot.on('physicTick', updateState);
  bot.on('playerJoined', updateState);
  bot.on('playerLeft', updateState);
  bot.on('kicked', (reason) => {
    state.connected = false;
    state.active = false;
    state.last_error = `kicked:${reason}`;
  });
  bot.on('error', (err) => {
    state.last_error = String(err && err.message || err);
  });
  bot.on('end', () => {
    state.connected = false;
    state.active = false;
  });
}

async function executeMinecraftStep(step) {
  state.last_action = step;
  if (!bot) {
    return { status: 'blocked', reason: mineflayer ? 'bot_not_spawned' : 'mineflayer_not_installed' };
  }

  const action = step.action;
  if (action === 'retreat') {
    return { status: 'ok', note: 'retreat_placeholder', executed: step };
  }
  if (action === 'heal_or_regroup') {
    return { status: 'ok', note: 'heal_or_regroup_placeholder', executed: step };
  }
  if (action === 'find_food_source') {
    return { status: 'ok', note: 'find_food_source_placeholder', executed: step };
  }
  if (action === 'consume_food') {
    return { status: 'ok', note: 'consume_food_placeholder', held_item: state.held_item, executed: step };
  }
  if (action === 'gather_logs') {
    return { status: 'ok', note: 'gather_logs_placeholder', targetCount: step.count || 0, executed: step };
  }
  if (action === 'craft_basic_tools') {
    return { status: 'ok', note: 'craft_basic_tools_placeholder', executed: step };
  }
  if (action === 'gather_basic_resources') {
    return { status: 'ok', note: 'gather_basic_resources_placeholder', targets: step.targets || [], executed: step };
  }
  return { status: 'blocked', reason: `unsupported_action:${action}`, executed: step };
}

async function handle(method, params) {
  if (method === 'observe') {
    if (mineflayer && !bot) {
      await ensureConnected();
    }
    updateState();
    return {
      ...state,
      mineflayer_available: !!mineflayer,
      timestamp: Date.now(),
    };
  }
  if (method === 'execute_step') {
    const step = (params && params.step) || {};
    if (mineflayer && !bot) {
      await ensureConnected();
    }
    updateState();
    return await executeMinecraftStep(step);
  }
  throw new Error(`unsupported_method:${method}`);
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on('line', async (line) => {
  let msg;
  try {
    msg = JSON.parse(line);
    const result = await handle(msg.method, msg.params || {});
    emit(msg.id, true, result);
  } catch (err) {
    emit(msg && msg.id, false, err && err.message || err);
  }
});
