const fs = require("fs");
const http = require("http");
const path = require("path");
const { createRequire } = require("module");

const repoRequire = createRequire(path.resolve(__dirname, "../../../../../package.json"));

function requireFromRepo(name) {
    try {
        return require(name);
    } catch (err) {
        return repoRequire(name);
    }
}

const mineflayer = requireFromRepo("mineflayer");

let express = null;
let bodyParser = null;
try {
    express = requireFromRepo("express");
    bodyParser = requireFromRepo("body-parser");
} catch (err) {
    console.warn(`[mineflayer] express fallback enabled: ${err.message}`);
}

const skills = require("./lib/skillLoader");
const { initCounter, getNextTime } = require("./lib/utils");
const obs = require("./lib/observation/base");
const OnChat = require("./lib/observation/onChat");
const OnError = require("./lib/observation/onError");
const { Voxels, BlockRecords } = require("./lib/observation/voxels");
const Status = require("./lib/observation/status");
const Inventory = require("./lib/observation/inventory");
const OnSave = require("./lib/observation/onSave");
const Chests = require("./lib/observation/chests");
const { plugin: tool } = requireFromRepo("mineflayer-tool");

const REPO_ROOT = path.resolve(__dirname, "../../../../../");
const DEATH_LOG_PATH = path.join(REPO_ROOT, "bot_memory", "voyager_death_events.jsonl");
const HOSTILE_ENTITY_NAMES = new Set([
    "blaze",
    "bogged",
    "cave_spider",
    "creeper",
    "drowned",
    "elder_guardian",
    "enderman",
    "endermite",
    "evoker",
    "ghast",
    "guardian",
    "hoglin",
    "husk",
    "magma_cube",
    "phantom",
    "piglin_brute",
    "pillager",
    "ravager",
    "shulker",
    "silverfish",
    "skeleton",
    "slime",
    "spider",
    "stray",
    "vex",
    "vindicator",
    "warden",
    "witch",
    "wither_skeleton",
    "zoglin",
    "zombie",
    "zombie_villager",
]);

function createMiniApp() {
    const routes = [];
    return {
        use() {},
        post(path, handler) {
            routes.push({ method: "POST", path, handler });
        },
        listen(port, callback) {
            const server = http.createServer((req, res) => {
                const route = routes.find((entry) => entry.method === req.method && entry.path === req.url);
                if (!route) {
                    res.statusCode = 404;
                    res.end("Not found");
                    return;
                }
                let body = "";
                req.on("data", (chunk) => {
                    body += chunk;
                });
                req.on("end", async () => {
                    try {
                        req.body = body ? JSON.parse(body) : {};
                    } catch (err) {
                        req.body = {};
                    }
                    let statusCode = 200;
                    res.status = (code) => {
                        statusCode = code;
                        return res;
                    };
                    res.json = (payload) => {
                        if (!res.writableEnded) {
                            res.statusCode = statusCode;
                            res.setHeader("Content-Type", "application/json; charset=utf-8");
                            res.end(JSON.stringify(payload));
                        }
                    };
                    try {
                        await route.handler(req, res);
                    } catch (err) {
                        if (!res.writableEnded) {
                            res.status(500).json({ error: String(err && err.message ? err.message : err) });
                        }
                    }
                });
            });
            return server.listen(port, callback);
        },
    };
}

let bot = null;
let reconnectTimer = null;

const app = express ? express() : createMiniApp();

if (bodyParser && app.use) {
    app.use(bodyParser.json({ limit: "50mb" }));
    app.use(bodyParser.urlencoded({ limit: "50mb", extended: false }));
}

function isBotConnected() {
    return !!(
        bot &&
        bot.entity &&
        bot._client &&
        !bot._client.ended &&
        bot._client.socket &&
        !bot._client.socket.destroyed
    );
}

function configureBotSession(botInstance, body) {
    botInstance.waitTicks = body.waitTicks;
    botInstance.globalTickCounter = 0;
    botInstance.stuckTickCounter = 0;
    botInstance.stuckPosList = [];
    botInstance.iron_pickaxe = false;
    botInstance._voyagerWindowOpened = false;
    botInstance._voyagerWindowClosed = false;
    botInstance._voyagerChestInteracted = false;
    botInstance._voyagerContainerInteraction = null;
    botInstance._voyagerSessionLive = !!(
        botInstance &&
        botInstance.entity &&
        botInstance._client &&
        !botInstance._client.ended &&
        botInstance._client.socket &&
        !botInstance._client.socket.destroyed
    );
}

function normalizeErrorMessage(err) {
    if (!err) return "Unknown error";
    if (typeof err === "string") return err;
    if (err.message) return err.message;
    return String(err);
}

function cloneStartConfig(body) {
    try {
        return JSON.parse(JSON.stringify(body || {}));
    } catch (err) {
        return { ...(body || {}) };
    }
}

function clearReconnectTimer() {
    if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
    }
}

function rememberStartConfig(botInstance, body) {
    if (!botInstance) return;
    botInstance._voyagerStartConfig = cloneStartConfig(body);
    if (!Number.isFinite(botInstance._voyagerReconnectAttempts)) {
        botInstance._voyagerReconnectAttempts = 0;
    }
    botInstance._voyagerAutoReconnectEnabled = true;
    botInstance._voyagerIntentionalStop = false;
    botInstance._voyagerReconnectScheduled = false;
    if (typeof botInstance._voyagerHasSpawned !== "boolean") {
        botInstance._voyagerHasSpawned = false;
    }
}

function buildBotOptions(startConfig) {
    const botOptions = {
        host: process.env.MINEFLAYER_HOST || "localhost",
        port: startConfig.port,
        username: process.env.MINEFLAYER_USERNAME || "bot",
        checkTimeoutInterval: 60 * 60 * 1000,
    };
    if (process.env.MINEFLAYER_DISABLE_CHAT_SIGNING) {
        botOptions.disableChatSigning = process.env.MINEFLAYER_DISABLE_CHAT_SIGNING.toLowerCase() === "true";
    }
    if (process.env.MINEFLAYER_AUTH) {
        botOptions.auth = process.env.MINEFLAYER_AUTH;
    }
    if (process.env.MINEFLAYER_PASSWORD) {
        botOptions.password = process.env.MINEFLAYER_PASSWORD;
    }
    if (process.env.MINEFLAYER_PROFILES_FOLDER) {
        botOptions.profilesFolder = process.env.MINEFLAYER_PROFILES_FOLDER;
    }
    return botOptions;
}

function attachBotRuntimeHandlers(botInstance) {
    if (!botInstance) {
        return;
    }
    installVoyagerLifecycleHooks(botInstance);
    ensureVoyagerTelemetryTimer(botInstance);
    if (!botInstance._voyagerKickedHandlerInstalled) {
        botInstance.on("kicked", (message) => {
            botInstance._voyagerDisconnectReason = normalizeErrorMessage(message);
        });
        botInstance._voyagerKickedHandlerInstalled = true;
    }
    if (!botInstance._voyagerMountHandlerInstalled) {
        botInstance.on("mount", () => {
            botInstance.dismount();
        });
        botInstance._voyagerMountHandlerInstalled = true;
    }
}

function scheduleBotReconnect(botInstance, reason) {
    if (!botInstance || botInstance._voyagerIntentionalStop || !botInstance._voyagerAutoReconnectEnabled) {
        return;
    }
    if (!botInstance._voyagerHasSpawned) {
        return;
    }
    const startConfig = botInstance._voyagerStartConfig;
    if (!startConfig || typeof startConfig !== "object") {
        return;
    }
    if (botInstance._voyagerReconnectScheduled) {
        return;
    }
    const baseDelayMs = Number.parseInt(process.env.MINEFLAYER_RECONNECT_DELAY_MS || "", 10);
    const delayMs = Number.isFinite(baseDelayMs) ? Math.max(baseDelayMs, 1000) : 5000;
    const maxAttemptsRaw = Number.parseInt(process.env.MINEFLAYER_RECONNECT_MAX_ATTEMPTS || "", 10);
    const maxAttempts = Number.isFinite(maxAttemptsRaw) ? Math.max(maxAttemptsRaw, 0) : 0;
    const nextAttempt = (Number.isFinite(botInstance._voyagerReconnectAttempts) ? botInstance._voyagerReconnectAttempts : 0) + 1;
    if (maxAttempts > 0 && nextAttempt > maxAttempts) {
        botInstance._voyagerReconnectScheduled = false;
        setConnectionState(botInstance, "disconnected", `reconnect limit reached after ${maxAttempts} attempts`);
        refreshVoyagerTelemetry(botInstance);
        return;
    }
    botInstance._voyagerReconnectScheduled = true;
    setConnectionState(botInstance, "reconnecting", `reconnect attempt ${nextAttempt}: ${normalizeErrorMessage(reason)}`);
    refreshVoyagerTelemetry(botInstance);
    clearReconnectTimer();
    reconnectTimer = setTimeout(async () => {
        botInstance._voyagerReconnectScheduled = false;
        if (bot !== botInstance && isBotConnected()) {
            return;
        }
        const reconnectConfig = cloneStartConfig(startConfig);
        try {
            const nextBot = mineflayer.createBot(buildBotOptions(reconnectConfig));
            nextBot._voyagerReconnectAttempts = nextAttempt;
            rememberStartConfig(nextBot, reconnectConfig);
            nextBot._voyagerReconnectAttempts = nextAttempt;
            attachBotRuntimeHandlers(nextBot);
            configureBotSession(nextBot, reconnectConfig);
            setConnectionState(nextBot, "starting", `reconnecting to Minecraft server (attempt ${nextAttempt})`);
            refreshVoyagerTelemetry(nextBot);
            bot = nextBot;
            nextBot.once("spawn", async () => {
                nextBot._voyagerReconnectAttempts = 0;
                try {
                    await applyStartState(nextBot, reconnectConfig);
                } catch (err) {
                    console.log(`[mineflayer] reconnect post-spawn setup failed: ${normalizeErrorMessage(err)}`);
                }
            });
        } catch (err) {
            console.log(`[mineflayer] reconnect spawn failed: ${normalizeErrorMessage(err)}`);
            scheduleBotReconnect(botInstance, err);
        }
    }, delayMs);
    if (typeof reconnectTimer.unref === "function") {
        reconnectTimer.unref();
    }
}

const DEFAULT_CHAT_COMMAND_GAP_TICKS = Math.max(
    Number.parseInt(process.env.MINEFLAYER_CHAT_COMMAND_GAP_TICKS || "", 10) || 10,
    1
);

async function sendChatCommand(botInstance, command, waitTicks = DEFAULT_CHAT_COMMAND_GAP_TICKS) {
    botInstance.chat(command);
    const requestedGap = Number.isFinite(waitTicks) ? waitTicks : DEFAULT_CHAT_COMMAND_GAP_TICKS;
    const appliedGap = Math.max(requestedGap, DEFAULT_CHAT_COMMAND_GAP_TICKS);
    if (appliedGap > 0) {
        await botInstance.waitForTicks(appliedGap);
    }
}

function appendJsonLine(filePath, payload) {
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
    fs.appendFileSync(filePath, `${JSON.stringify(payload)}\n`, "utf8");
}

function setConnectionState(botInstance, state, note = null) {
    if (!botInstance) return;
    botInstance._voyagerConnectionState = state;
    botInstance._voyagerConnectionNote = note || null;
}

function recordVoyagerActionError(botInstance, err) {
    if (!botInstance) return;
    const message = normalizeErrorMessage(err);
    if (Array.isArray(botInstance.obsList)) {
        const onErrorObservation = botInstance.obsList.find((entry) => entry && entry.name === "onError");
        if (onErrorObservation) {
            onErrorObservation.obs = message;
        }
    }
    if (typeof botInstance.event === "function") {
        botInstance.event("onError");
    }
}

function vecToPlain(vec) {
    if (!vec) return null;
    return {
        x: Number(vec.x),
        y: Number(vec.y),
        z: Number(vec.z),
    };
}

function listInventoryItems(botInstance) {
    const inventory = botInstance.currentWindow || botInstance.inventory;
    const result = {};
    for (const item of inventory.items()) {
        if (!item || !item.name || !item.count) continue;
        result[item.name] = (result[item.name] || 0) + item.count;
    }
    return result;
}

function getInventoryUsedSlots(botInstance) {
    if (!botInstance) return 0;
    if (typeof botInstance.inventoryUsed === "function") {
        try {
            const used = botInstance.inventoryUsed();
            if (Number.isFinite(used)) {
                return used;
            }
        } catch (err) {}
    }
    const inventory = botInstance.currentWindow || botInstance.inventory;
    if (!inventory || typeof inventory.items !== "function") {
        return 0;
    }
    return inventory.items().filter((item) => item && item.count > 0).length;
}

function getEquipmentSnapshot(botInstance) {
    const slots = botInstance.inventory.slots;
    const mainHand = botInstance.heldItem;
    const items = slots.slice(5, 9).concat(mainHand, slots[45]);
    return items.map((item) => (item ? item.name : null));
}

function buildInventorySlotSnapshot(botInstance) {
    if (!botInstance || !botInstance.inventory || !Array.isArray(botInstance.inventory.slots)) {
        return [];
    }
    const slots = botInstance.inventory.slots;
    const selectedHotbarSlot = Number.isInteger(botInstance.quickBarSlot) ? botInstance.quickBarSlot : -1;
    const rows = [];
    const pushSlot = (slotIndex, section, sectionIndex, label) => {
        const item = slots[slotIndex];
        rows.push({
            slot: slotIndex,
            section,
            sectionIndex,
            label,
            selected: section === "hotbar" && sectionIndex === selectedHotbarSlot,
            item: item && item.name ? item.name : null,
            count: item && Number.isFinite(item.count) ? item.count : 0,
            displayName: item && item.displayName ? item.displayName : null,
        });
    };
    const armorLabels = ["helmet", "chestplate", "leggings", "boots"];
    for (let slotIndex = 5; slotIndex <= 8; slotIndex += 1) {
        pushSlot(slotIndex, "armor", slotIndex - 5, armorLabels[slotIndex - 5]);
    }
    for (let slotIndex = 9; slotIndex <= 35; slotIndex += 1) {
        pushSlot(slotIndex, "main", slotIndex - 9, String(slotIndex - 8));
    }
    for (let slotIndex = 36; slotIndex <= 44; slotIndex += 1) {
        pushSlot(slotIndex, "hotbar", slotIndex - 36, String(slotIndex - 35));
    }
    pushSlot(45, "offhand", 0, "offhand");
    return rows;
}

function buildVoyagerTelemetry(botInstance) {
    if (!botInstance || !botInstance.entity) {
        return null;
    }
    const sessionLive = botInstance._voyagerSessionLive === true;
    const botConnected = sessionLive && isBotConnected();
    const hasLivePosition = botConnected && !!(botInstance.entity && botInstance.entity.position);
    let connectionState = botInstance._voyagerConnectionState || (botConnected ? "connected" : "disconnected");
    let connectionNote = botInstance._voyagerConnectionNote || null;

    if (!sessionLive || !botConnected) {
        if (connectionState !== "reconnecting") {
            connectionState = "disconnected";
        }
        if (!connectionNote) {
            connectionNote = sessionLive ? "minecraft session unavailable" : "minecraft session not live";
        }
        botInstance._voyagerConnectionState = connectionState;
        botInstance._voyagerConnectionNote = connectionNote;
    } else if (hasLivePosition && connectionState !== "reconnecting" && connectionState !== "disconnected") {
        connectionState = "connected";
        if (connectionNote === "spawned; awaiting observation" || connectionNote === "respawned; awaiting observation") {
            connectionNote = "active telemetry available";
        }
        botInstance._voyagerConnectionState = connectionState;
        botInstance._voyagerConnectionNote = connectionNote;
    }

    return {
        recordedAt: new Date().toISOString(),
        inventory: botConnected ? listInventoryItems(botInstance) : {},
        inventorySlots: botConnected ? buildInventorySlotSnapshot(botInstance) : [],
        status: {
            health: botConnected ? botInstance.health : null,
            food: botConnected ? botInstance.food : null,
            saturation: botConnected ? botInstance.foodSaturation : null,
            oxygen: botConnected ? botInstance.oxygenLevel : null,
            position: botConnected ? vecToPlain(botInstance.entity.position) : null,
            velocity: botConnected ? vecToPlain(botInstance.entity.velocity) : null,
            yaw: botConnected ? botInstance.entity.yaw : null,
            pitch: botConnected ? botInstance.entity.pitch : null,
            onGround: botConnected ? botInstance.entity.onGround : null,
            equipment: botConnected ? getEquipmentSnapshot(botInstance) : [],
            inventoryUsed: botConnected ? getInventoryUsedSlots(botInstance) : null,
            name: botInstance.entity.username,
            isInWater: botConnected ? botInstance.entity.isInWater : null,
            isInLava: botConnected ? botInstance.entity.isInLava : null,
            entities: {},
        },
        connectionState,
        connectionNote,
        lastDeathEvent: botInstance._voyagerLastDeathEvent || null,
        deathEventLogPath: DEATH_LOG_PATH,
        searchExecution: botInstance._voyagerSearchExecution || null,
    };
}

function refreshVoyagerTelemetry(botInstance) {
    if (!botInstance) return null;
    const telemetry = buildVoyagerTelemetry(botInstance);
    botInstance._voyagerTelemetry = telemetry;
    return telemetry;
}

function ensureVoyagerTelemetryTimer(botInstance) {
    if (!botInstance) return;
    if (botInstance._voyagerTelemetryInterval) {
        return;
    }
    refreshVoyagerTelemetry(botInstance);
    botInstance._voyagerTelemetryInterval = setInterval(() => {
        try {
            refreshVoyagerTelemetry(botInstance);
        } catch (err) {}
    }, 1000);
    if (typeof botInstance._voyagerTelemetryInterval.unref === "function") {
        botInstance._voyagerTelemetryInterval.unref();
    }
}

function clearVoyagerTelemetryTimer(botInstance) {
    if (botInstance && botInstance._voyagerTelemetryInterval) {
        clearInterval(botInstance._voyagerTelemetryInterval);
        botInstance._voyagerTelemetryInterval = null;
    }
}

function snapshotHostileEntities(botInstance, maxDistance = 16, limit = 5) {
    if (!botInstance || !botInstance.entities || !botInstance.entity || !botInstance.entity.position) {
        return [];
    }
    return Object.values(botInstance.entities)
        .filter((entity) => {
            if (!entity || entity === botInstance.entity || !entity.position) {
                return false;
            }
            const name = String(entity.name || entity.displayName || "").toLowerCase();
            if (!HOSTILE_ENTITY_NAMES.has(name)) {
                return false;
            }
            return entity.position.distanceTo(botInstance.entity.position) <= maxDistance;
        })
        .map((entity) => ({
            name: entity.name || entity.displayName || "unknown",
            distance: Number(entity.position.distanceTo(botInstance.entity.position).toFixed(2)),
            position: {
                x: Number(entity.position.x.toFixed(2)),
                y: Number(entity.position.y.toFixed(2)),
                z: Number(entity.position.z.toFixed(2)),
            },
        }))
        .sort((a, b) => a.distance - b.distance)
        .slice(0, limit);
}

function parseDeathBroadcast(username, message) {
    if (!username || typeof message !== "string") {
        return null;
    }
    const trimmed = message.trim();
    if (!trimmed.startsWith(`${username} `)) {
        return null;
    }
    const cause = trimmed.slice(username.length + 1);
    if (!cause) {
        return null;
    }
    const lowered = cause.toLowerCase();
    const deathHints = [
        "was slain",
        "was shot",
        "was blown up",
        "drowned",
        "fell",
        "burned",
        "tried to swim in lava",
        "was killed",
        "was fireballed",
        "walked into",
        "starved",
        "froze",
        "was impaled",
        "hit the ground too hard",
    ];
    if (!deathHints.some((hint) => lowered.includes(hint))) {
        return null;
    }
    let killer = null;
    const byMatch = cause.match(/\bby (.+?)(?: using .+)?$/i);
    if (byMatch) {
        killer = byMatch[1];
    } else {
        const escapeMatch = cause.match(/while trying to escape (.+)$/i);
        if (escapeMatch) {
            killer = escapeMatch[1];
        }
    }
    return {
        message: trimmed,
        cause,
        killer,
    };
}

function installVoyagerLifecycleHooks(botInstance) {
    if (!botInstance || botInstance._voyagerLifecycleHooksInstalled) {
        return;
    }
    botInstance._voyagerLastDamageContext = null;
    botInstance._voyagerLastDeathBroadcast = null;
    botInstance._voyagerLastDeathEvent = null;
    botInstance._voyagerPendingRespawn = false;
    botInstance._voyagerSuppressNextDeathLog = false;
    botInstance._voyagerLastKnownHealth = null;
    botInstance._voyagerSessionLive = false;
    setConnectionState(botInstance, "starting", "waiting for spawn");

    botInstance.on("messagestr", (message) => {
        const parsed = parseDeathBroadcast(botInstance.username, message);
        if (parsed) {
            botInstance._voyagerLastDeathBroadcast = {
                ...parsed,
                observed_at: new Date().toISOString(),
            };
        }
    });

    botInstance.on("error", (err) => {
        botInstance._voyagerSessionLive = false;
        botInstance._voyagerDisconnectReason = normalizeErrorMessage(err);
        setConnectionState(botInstance, "reconnecting", normalizeErrorMessage(err));
        refreshVoyagerTelemetry(botInstance);
    });

    botInstance.on("end", () => {
        botInstance._voyagerSessionLive = false;
        const note = botInstance._voyagerDisconnectReason || "bot session ended";
        setConnectionState(botInstance, "disconnected", note);
        refreshVoyagerTelemetry(botInstance);
        clearVoyagerTelemetryTimer(botInstance);
        if (bot === botInstance) {
            scheduleBotReconnect(botInstance, note);
        }
    });

    botInstance.on("health", () => {
        const currentHealth = typeof botInstance.health === "number" ? botInstance.health : null;
        const previousHealth = typeof botInstance._voyagerLastKnownHealth === "number" ? botInstance._voyagerLastKnownHealth : null;
        if (currentHealth !== null && previousHealth !== null && currentHealth < previousHealth) {
            botInstance._voyagerLastDamageContext = {
                recorded_at: new Date().toISOString(),
                from: previousHealth,
                to: currentHealth,
                delta: Number((previousHealth - currentHealth).toFixed(2)),
                position: botInstance.entity && botInstance.entity.position ? {
                    x: Number(botInstance.entity.position.x.toFixed(2)),
                    y: Number(botInstance.entity.position.y.toFixed(2)),
                    z: Number(botInstance.entity.position.z.toFixed(2)),
                } : null,
                nearby_hostiles: snapshotHostileEntities(botInstance, 16, 5),
            };
        }
        if (currentHealth !== null) {
            botInstance._voyagerLastKnownHealth = currentHealth;
        }
    });

    botInstance.on("death", () => {
        if (botInstance._voyagerSuppressNextDeathLog) {
            botInstance._voyagerSuppressNextDeathLog = false;
            return;
        }
        const deathBroadcast = botInstance._voyagerLastDeathBroadcast;
        const damageContext = botInstance._voyagerLastDamageContext;
        const nearbyHostiles = snapshotHostileEntities(botInstance, 24, 6);
        const likelyKiller = deathBroadcast && deathBroadcast.killer
            ? deathBroadcast.killer
            : (nearbyHostiles[0] ? nearbyHostiles[0].name : null);
        const likelyReasonParts = [];
        if (deathBroadcast && deathBroadcast.cause) {
            likelyReasonParts.push(deathBroadcast.cause);
        }
        if (damageContext && damageContext.delta) {
            likelyReasonParts.push(`recent health drop ${damageContext.from} -> ${damageContext.to}`);
        }
        if (nearbyHostiles.length > 0) {
            likelyReasonParts.push(`nearby hostiles: ${nearbyHostiles.map((entry) => `${entry.name}@${entry.distance}`).join(", ")}`);
        }
        const deathEvent = {
            id: `death-${Date.now()}`,
            recorded_at: new Date().toISOString(),
            player: botInstance.username || null,
            pve_focus: true,
            cause: deathBroadcast && deathBroadcast.cause ? deathBroadcast.cause : "death",
            death_message: deathBroadcast && deathBroadcast.message ? deathBroadcast.message : null,
            likely_killer: likelyKiller,
            likely_reason: likelyReasonParts.join(" | ") || "death cause unavailable",
            position: botInstance.entity && botInstance.entity.position ? {
                x: Number(botInstance.entity.position.x.toFixed(2)),
                y: Number(botInstance.entity.position.y.toFixed(2)),
                z: Number(botInstance.entity.position.z.toFixed(2)),
            } : null,
            health: typeof botInstance.health === "number" ? botInstance.health : null,
            hunger: typeof botInstance.food === "number" ? botInstance.food : null,
            nearby_hostiles: nearbyHostiles,
            last_damage: damageContext,
            response_taken: [
                "wait_for_respawn",
                "keep_inventory_expected",
                "resume_observation_after_respawn",
            ],
        };
        botInstance._voyagerLastDeathEvent = deathEvent;
        botInstance._voyagerPendingRespawn = true;
        setConnectionState(botInstance, "awaiting_observation", `death detected: ${deathEvent.cause}`);
        appendJsonLine(DEATH_LOG_PATH, deathEvent);
        botInstance._voyagerLastDeathBroadcast = null;
    });

    botInstance.on("spawn", () => {
        botInstance._voyagerSessionLive = true;
        botInstance._voyagerHasSpawned = true;
        const note = botInstance._voyagerPendingRespawn ? "respawned; awaiting observation" : "spawned; awaiting observation";
        setConnectionState(botInstance, "awaiting_observation", note);
        botInstance._voyagerLastKnownHealth = typeof botInstance.health === "number" ? botInstance.health : botInstance._voyagerLastKnownHealth;
        if (botInstance._voyagerPendingRespawn && botInstance._voyagerLastDeathEvent) {
            const respawnEvent = {
                kind: "respawn_followup",
                death_id: botInstance._voyagerLastDeathEvent.id,
                recorded_at: new Date().toISOString(),
                player: botInstance.username || null,
                response_taken: ["respawn_observed", "continue_after_respawn"],
                position: botInstance.entity && botInstance.entity.position ? {
                    x: Number(botInstance.entity.position.x.toFixed(2)),
                    y: Number(botInstance.entity.position.y.toFixed(2)),
                    z: Number(botInstance.entity.position.z.toFixed(2)),
                } : null,
            };
            botInstance._voyagerLastDeathEvent = {
                ...botInstance._voyagerLastDeathEvent,
                response_taken: [...botInstance._voyagerLastDeathEvent.response_taken, "respawn_observed"],
                respawn_observed_at: respawnEvent.recorded_at,
            };
            appendJsonLine(DEATH_LOG_PATH, respawnEvent);
            botInstance._voyagerPendingRespawn = false;
        }
        botInstance._voyagerLastDamageContext = null;
        botInstance._voyagerLastDeathBroadcast = null;
        refreshVoyagerTelemetry(botInstance);
    });

    botInstance._voyagerLifecycleHooksInstalled = true;
}

function ensureContainerInteraction(botInstance, seed = {}) {
    if (!botInstance._voyagerContainerInteraction || typeof botInstance._voyagerContainerInteraction !== "object") {
        botInstance._voyagerContainerInteraction = {
            kind: null,
            label: null,
            source: null,
            blockName: null,
            status: "idle",
            opened: false,
            closed: false,
            interacted: false,
            blockedAbove: false,
            blockedBy: null,
            error: null,
            attempt: 0,
        };
    }
    Object.assign(botInstance._voyagerContainerInteraction, seed);
    return botInstance._voyagerContainerInteraction;
}

function updateContainerInteraction(botInstance, patch = {}) {
    const interaction = ensureContainerInteraction(botInstance);
    Object.assign(interaction, patch);
    return interaction;
}

function classifyContainerKind(containerBlock, fallbackLabel = "container") {
    const blockName = String(containerBlock && containerBlock.name ? containerBlock.name : fallbackLabel).toLowerCase();
    if (blockName.includes("chest")) {
        return "chest";
    }
    if (blockName.includes("furnace")) {
        return "furnace";
    }
    return "generic";
}

function observeWithVoyagerState(botInstance) {
    const observationJson = botInstance.observe();
    try {
        const parsed = typeof observationJson === "string" ? JSON.parse(observationJson) : observationJson;
        if (Array.isArray(parsed) && parsed.length > 0) {
            if (isBotConnected() && botInstance.entity && botInstance.entity.position) {
                setConnectionState(botInstance, "connected", "active observation available");
            }
            const telemetry = refreshVoyagerTelemetry(botInstance) || botInstance._voyagerTelemetry || null;
            const lastEvent = parsed[parsed.length - 1];
            if (Array.isArray(lastEvent) && lastEvent.length > 1 && lastEvent[1] && typeof lastEvent[1] === "object") {
                if (telemetry && telemetry.status && lastEvent[1].status && typeof lastEvent[1].status === "object") {
                    lastEvent[1].status = {
                        ...lastEvent[1].status,
                        ...telemetry.status,
                    };
                }
                if (telemetry && telemetry.inventory) {
                    lastEvent[1].inventory = telemetry.inventory;
                }
                lastEvent[1].voyagerWindowOpened = !!botInstance._voyagerWindowOpened;
                lastEvent[1].voyagerWindowClosed = !!botInstance._voyagerWindowClosed;
                lastEvent[1].voyagerWindowResult = botInstance.lastVoyagerWindowResult || null;
                lastEvent[1].voyagerChestInteracted = !!botInstance._voyagerChestInteracted;
                lastEvent[1].voyagerContainerInteraction = botInstance._voyagerContainerInteraction || null;
                lastEvent[1].connectionState = (telemetry && telemetry.connectionState) || botInstance._voyagerConnectionState || (isBotConnected() ? "connected" : "disconnected");
                lastEvent[1].connectionNote = (telemetry && telemetry.connectionNote) || botInstance._voyagerConnectionNote || null;
                lastEvent[1].lastDeathEvent = (telemetry && telemetry.lastDeathEvent) || botInstance._voyagerLastDeathEvent || null;
                lastEvent[1].deathEventLogPath = (telemetry && telemetry.deathEventLogPath) || DEATH_LOG_PATH;
                lastEvent[1].searchExecution = (telemetry && telemetry.searchExecution) || botInstance._voyagerSearchExecution || null;
                if (telemetry && telemetry.recordedAt) {
                    lastEvent[1].telemetryRecordedAt = telemetry.recordedAt;
                }
            }
        }
        return JSON.stringify(parsed);
    } catch (err) {
        return observationJson;
    }
}

function ensureBotPlugins(botInstance) {
    if (botInstance._voyagerPluginsInstalled) {
        return;
    }
    const { pathfinder } = requireFromRepo("mineflayer-pathfinder");
    const tool = requireFromRepo("mineflayer-tool").plugin;
    const collectBlock = requireFromRepo("mineflayer-collectblock").plugin;
    botInstance.loadPlugin(pathfinder);
    botInstance.loadPlugin(tool);
    botInstance.loadPlugin(collectBlock);
    try {
        const pvp = requireFromRepo("mineflayer-pvp").plugin;
        botInstance.loadPlugin(pvp);
    } catch (err) {
        console.warn(`[mineflayer] optional plugin mineflayer-pvp unavailable: ${err.message}`);
    }
    try {
        const minecraftHawkEye = requireFromRepo("minecrafthawkeye");
        botInstance.loadPlugin(minecraftHawkEye);
    } catch (err) {
        console.warn(`[mineflayer] optional plugin minecrafthawkeye unavailable: ${err.message}`);
    }

    obs.inject(botInstance, [
        OnChat,
        OnError,
        Voxels,
        Status,
        Inventory,
        OnSave,
        Chests,
        BlockRecords,
    ]);
    skills.inject(botInstance);
    if (!botInstance._voyagerWindowTrackingInstalled) {
        botInstance.on("windowOpen", (window) => {
            botInstance._voyagerWindowOpened = true;
            botInstance.lastVoyagerWindowResult = {
                label: window && (window.title || window.type) ? (window.title || window.type) : "window",
                status: "opened",
                windowType: window ? (window.type || null) : null,
            };
            updateContainerInteraction(botInstance, {
                opened: true,
                status: "opened",
                windowType: window ? (window.type || null) : null,
                label: window && (window.title || window.type) ? (window.title || window.type) : ensureContainerInteraction(botInstance).label,
                error: null,
            });
        });
        botInstance.on("windowClose", (window) => {
            botInstance._voyagerWindowClosed = true;
            botInstance.lastVoyagerWindowResult = {
                label: window && (window.title || window.type) ? (window.title || window.type) : ((botInstance.lastVoyagerWindowResult && botInstance.lastVoyagerWindowResult.label) || "window"),
                status: "closed",
                windowType: window ? (window.type || null) : null,
            };
            updateContainerInteraction(botInstance, {
                closed: true,
                status: "closed",
                windowType: window ? (window.type || null) : null,
            });
        });
        botInstance.on("closeChest", () => {
            botInstance._voyagerChestInteracted = true;
            updateContainerInteraction(botInstance, {
                kind: "chest",
                interacted: true,
                status: "interacted",
                error: null,
            });
        });
        botInstance._voyagerWindowTrackingInstalled = true;
    }
    botInstance._voyagerPluginsInstalled = true;
}

async function applyStartState(botInstance, body) {
    let itemTicks = 1;
    if (body.reset === "hard") {
        await sendChatCommand(
            botInstance,
            "/clear @s",
            Math.max(botInstance.waitTicks, DEFAULT_CHAT_COMMAND_GAP_TICKS)
        );
        botInstance._voyagerSuppressNextDeathLog = true;
        await sendChatCommand(botInstance, "/kill @s", Math.max(botInstance.waitTicks * 2, 20));
        const inventory = body.inventory ? body.inventory : {};
        const equipment = body.equipment
            ? body.equipment
            : [null, null, null, null, null, null];
        for (let key in inventory) {
            await sendChatCommand(botInstance, `/give @s minecraft:${key} ${inventory[key]}`);
            itemTicks += 1;
        }
        const equipmentNames = [
            "armor.head",
            "armor.chest",
            "armor.legs",
            "armor.feet",
            "weapon.mainhand",
            "weapon.offhand",
        ];
        for (let i = 0; i < 6; i++) {
            if (i === 4) continue;
            if (equipment[i]) {
                await sendChatCommand(
                    botInstance,
                    `/item replace entity @s ${equipmentNames[i]} with minecraft:${equipment[i]}`
                );
                itemTicks += 1;
            }
        }
    }

    if (body.position) {
        await sendChatCommand(
            botInstance,
            `/tp @s ${body.position.x} ${body.position.y} ${body.position.z}`,
            Math.max(botInstance.waitTicks, DEFAULT_CHAT_COMMAND_GAP_TICKS)
        );
    }

    ensureBotPlugins(botInstance);
    ensureVoyagerTelemetryTimer(botInstance);
    setConnectionState(botInstance, "awaiting_observation", body.reset === "hard" ? "hard reset completed; awaiting observation" : "session ready; awaiting observation");
    refreshVoyagerTelemetry(botInstance);

    if (body.spread) {
        await sendChatCommand(
            botInstance,
            `/spreadplayers ~ ~ 0 300 under 80 false @s`,
            Math.max(botInstance.waitTicks, DEFAULT_CHAT_COMMAND_GAP_TICKS)
        );
    }

    await botInstance.waitForTicks(Math.max(botInstance.waitTicks * itemTicks, DEFAULT_CHAT_COMMAND_GAP_TICKS));

    if (
        botInstance.inventory.items().find((item) => item.name === "iron_pickaxe")
    ) {
        botInstance.iron_pickaxe = true;
    }

    initCounter(botInstance);
    await sendChatCommand(botInstance, "/gamerule keepInventory true");
    await sendChatCommand(botInstance, "/gamerule doDaylightCycle false");
    return observeWithVoyagerState(botInstance);
}

app.post("/start", async (req, res) => {
    console.log(req.body);

    function onDisconnect(message) {
        if (bot && bot.viewer) {
            bot.viewer.close();
        }
        if (bot) {
            bot._voyagerDisconnectReason = normalizeErrorMessage(message);
            bot._voyagerSessionLive = false;
            setConnectionState(bot, "disconnected", bot._voyagerDisconnectReason);
            refreshVoyagerTelemetry(bot);
            clearVoyagerTelemetryTimer(bot);
            bot.end();
        }
        console.log(message);
        if (message === "Restarting bot") {
            bot = null;
        }
    }

    if (isBotConnected()) {
        clearReconnectTimer();
        rememberStartConfig(bot, req.body);
        bot._voyagerReconnectAttempts = 0;
        configureBotSession(bot, req.body);
        const observation = await applyStartState(bot, req.body);
        res.json(observation);
        return;
    }

    if (bot) {
        bot._voyagerIntentionalStop = true;
        bot._voyagerAutoReconnectEnabled = false;
        onDisconnect("Restarting bot");
    }
    bot = null;
    clearReconnectTimer();
    const startConfig = cloneStartConfig(req.body);
    const botOptions = buildBotOptions(startConfig);
    bot = mineflayer.createBot(botOptions);
    rememberStartConfig(bot, startConfig);
    attachBotRuntimeHandlers(bot);
    setConnectionState(bot, "starting", "connecting to Minecraft server");
    refreshVoyagerTelemetry(bot);

    function onConnectionFailed(e) {
        console.log(e);
        if (bot) {
            bot._voyagerSessionLive = false;
        }
        setConnectionState(bot, "disconnected", normalizeErrorMessage(e));
        bot = null;
        res.status(400).json({ error: e });
    }

    bot.once("error", onConnectionFailed);
    configureBotSession(bot, startConfig);

    bot.once("spawn", async () => {
        bot.removeListener("error", onConnectionFailed);
        clearReconnectTimer();
        bot._voyagerReconnectAttempts = 0;
        try {
            const observation = await applyStartState(bot, startConfig);
            res.json(observation);
        } catch (err) {
            console.log(err);
            res.status(500).json({ error: String(err && err.message ? err.message : err) });
        }
    });
});

app.post("/step", async (req, res) => {
    if (!bot || !isBotConnected()) {
        res.status(400).json({ error: "Bot not spawned" });
        return;
    }
    // import useful package
    let response_sent = false;
    function otherError(err) {
        console.log("Uncaught Error");
        recordVoyagerActionError(bot, handleError(err));
        bot.waitForTicks(bot.waitTicks).then(() => {
            if (!response_sent) {
                response_sent = true;
                res.json(observeWithVoyagerState(bot));
            }
        });
    }

    process.on("uncaughtException", otherError);

    const mcData = require("minecraft-data")(bot.version);
    mcData.itemsByName["leather_cap"] = mcData.itemsByName["leather_helmet"];
    mcData.itemsByName["leather_tunic"] =
        mcData.itemsByName["leather_chestplate"];
    mcData.itemsByName["leather_pants"] =
        mcData.itemsByName["leather_leggings"];
    mcData.itemsByName["leather_boots"] = mcData.itemsByName["leather_boots"];
    mcData.itemsByName["lapis_lazuli_ore"] = mcData.itemsByName["lapis_ore"];
    mcData.blocksByName["lapis_lazuli_ore"] = mcData.blocksByName["lapis_ore"];
    const {
        Movements,
        goals: {
            Goal,
            GoalBlock,
            GoalNear,
            GoalXZ,
            GoalNearXZ,
            GoalY,
            GoalGetToBlock,
            GoalLookAtBlock,
            GoalBreakBlock,
            GoalCompositeAny,
            GoalCompositeAll,
            GoalInvert,
            GoalFollow,
            GoalPlaceBlock,
        },
        pathfinder,
        Move,
        ComputedPath,
        PartiallyComputedPath,
        XZCoordinates,
        XYZCoordinates,
        SafeBlock,
        GoalPlaceBlockOptions,
    } = require("mineflayer-pathfinder");
    const { Vec3 } = require("vec3");

    // Set up pathfinder
    const movements = new Movements(bot, mcData);
    if (mcData.blocksByName.vine) {
        movements.climbables.add(mcData.blocksByName.vine.id);
    }
    bot.pathfinder.setMovements(movements);

    if (!bot._voyagerOriginalPathfinderGoto && typeof bot.pathfinder.goto === "function") {
        bot._voyagerOriginalPathfinderGoto = bot.pathfinder.goto.bind(bot.pathfinder);
    }
    bot._voyagerPathfinderGotoTimeoutMs = 45000;
    if (bot._voyagerOriginalPathfinderGoto) {
        bot.pathfinder.goto = async (...args) => {
            let timeout = null;
            try {
                return await Promise.race([
                    bot._voyagerOriginalPathfinderGoto(...args),
                    new Promise((_, reject) => {
                        timeout = setTimeout(() => {
                            try {
                                bot.pathfinder.setGoal(null);
                            } catch (err) {}
                            const goal = args && args.length > 0 ? args[0] : null;
                            const goalName = goal && goal.constructor && goal.constructor.name
                                ? goal.constructor.name
                                : "goal";
                            reject(new Error(`Timed out while pathfinding to ${goalName}`));
                        }, bot._voyagerPathfinderGotoTimeoutMs);
                    }),
                ]);
            } finally {
                if (timeout) clearTimeout(timeout);
            }
        };
    }

    bot.globalTickCounter = 0;
    bot.stuckTickCounter = 0;
    bot.stuckPosList = [];
    bot.lastVoyagerWindowResult = null;
    bot._voyagerWindowOpened = false;
    bot._voyagerWindowClosed = false;
    bot._voyagerChestInteracted = false;
    bot._voyagerContainerInteraction = null;
    if (!bot._voyagerOriginalOpenContainer && typeof bot.openContainer === "function") {
        bot._voyagerOriginalOpenContainer = bot.openContainer.bind(bot);
    }
    if (!bot._voyagerOriginalOpenFurnace && typeof bot.openFurnace === "function") {
        bot._voyagerOriginalOpenFurnace = bot.openFurnace.bind(bot);
    }
    if (!bot._voyagerOriginalOpenBlock && typeof bot.openBlock === "function") {
        bot._voyagerOriginalOpenBlock = bot.openBlock.bind(bot);
    }

    function onTick() {
        bot.globalTickCounter++;
        if (!bot?.entity?.position || !bot.pathfinder) {
            return;
        }
        if (bot.pathfinder.isMoving()) {
            bot.stuckTickCounter++;
            if (bot.stuckTickCounter >= 100) {
                onStuck(1.5);
                bot.stuckTickCounter = 0;
            }
        }
    }

    bot.on("physicsTick", onTick);

    // initialize fail count
    let _craftItemFailCount = 0;
    let _killMobFailCount = 0;
    let _mineBlockFailCount = 0;
    let _placeItemFailCount = 0;
    let _smeltItemFailCount = 0;

    async function clearActiveWindow() {
        const currentWindow = bot.currentWindow;
        if (!currentWindow) return;
        try {
            if (typeof currentWindow.close === "function") {
                currentWindow.close();
            } else {
                bot.closeWindow(currentWindow);
            }
        } catch (err) {}
        await bot.waitForTicks(4);
    }

    async function settleInteraction() {
        try {
            bot.pathfinder.setGoal(null);
        } catch (err) {}
        try {
            bot.clearControlStates();
        } catch (err) {}
        await clearActiveWindow();
        await bot.waitForTicks(4);
    }

    async function gotoLookAtBlockWithTimeout(block, label, timeoutMs = 20000) {
        if (!block) {
            throw new Error(`${label} block is missing`);
        }
        let timeout = null;
        try {
            await Promise.race([
                bot.pathfinder.goto(new GoalLookAtBlock(block.position, bot.world, {})),
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

    async function withWindowRecovery(kind, label, action, retries = 1, containerBlock = null, source = "helper") {
        let lastError = null;
        ensureContainerInteraction(bot, {
            kind,
            label,
            source,
            blockName: containerBlock && containerBlock.name ? containerBlock.name : null,
            status: "starting",
            opened: false,
            closed: false,
            interacted: false,
            blockedAbove: false,
            blockedBy: null,
            error: null,
            attempt: 0,
        });
        for (let attempt = 0; attempt <= retries; attempt++) {
            try {
                updateContainerInteraction(bot, {
                    kind,
                    label,
                    source,
                    blockName: containerBlock && containerBlock.name ? containerBlock.name : null,
                    status: "attempt",
                    error: null,
                    blockedAbove: false,
                    blockedBy: null,
                    attempt: attempt + 1,
                });
                await settleInteraction();
                const result = await action();
                bot.lastVoyagerWindowResult = {
                    label,
                    status: "success",
                    attempt: attempt + 1,
                };
                updateContainerInteraction(bot, {
                    kind,
                    label,
                    source,
                    status: bot._voyagerWindowOpened ? "opened" : "success",
                    error: null,
                    attempt: attempt + 1,
                });
                return result;
            } catch (err) {
                lastError = err;
                const errorMessage = normalizeErrorMessage(err);
                bot.lastVoyagerWindowResult = {
                    label,
                    status: "error",
                    attempt: attempt + 1,
                    error: errorMessage,
                };
                updateContainerInteraction(bot, {
                    kind,
                    label,
                    source,
                    status: "error",
                    error: errorMessage,
                    attempt: attempt + 1,
                });
                await settleInteraction();
                await bot.waitForTicks(8);
            }
        }
        throw new Error(`${label} failed after recovery: ${normalizeErrorMessage(lastError)}`);
    }

    async function craftWithRecovery(recipe, count, table, label) {
        return withWindowRecovery("crafting", label, async () => {
            await bot.craft(recipe, count, table);
        });
    }

    async function waitForVoyagerWindowOpen(timeoutMs = 8000, label = "container") {
        if (bot.currentWindow) {
            return bot.currentWindow;
        }
        return await new Promise((resolve, reject) => {
            let timeout = null;
            const onOpen = (window) => {
                if (timeout) clearTimeout(timeout);
                resolve(window || bot.currentWindow);
            };
            timeout = setTimeout(() => {
                bot.removeListener("windowOpen", onOpen);
                const error = new Error(`Event windowOpen did not fire within timeout of ${timeoutMs}ms`);
                updateContainerInteraction(bot, {
                    status: "error",
                    error: normalizeErrorMessage(error),
                    label,
                });
                reject(error);
            }, timeoutMs);
            bot.once("windowOpen", onOpen);
        });
    }

    function getConnectedChestBlocks(chestBlock) {
        if (!chestBlock || !chestBlock.position) {
            return [];
        }
        const connected = [chestBlock];
        const offsets = [
            new Vec3(1, 0, 0),
            new Vec3(-1, 0, 0),
            new Vec3(0, 0, 1),
            new Vec3(0, 0, -1),
        ];
        for (const offset of offsets) {
            const neighbor = bot.blockAt(chestBlock.position.plus(offset));
            if (!neighbor || neighbor.name !== chestBlock.name) {
                continue;
            }
            if (!connected.find((block) => block.position.equals(neighbor.position))) {
                connected.push(neighbor);
            }
        }
        return connected;
    }

    function getChestBlocker(chestBlock) {
        for (const block of getConnectedChestBlocks(chestBlock)) {
            const aboveBlock = bot.blockAt(block.position.offset(0, 1, 0));
            if (aboveBlock && aboveBlock.boundingBox === "block") {
                return aboveBlock;
            }
        }
        return null;
    }

    async function openGenericContainerWindowFallback(containerBlock, label = "container", kind = "generic") {
        const directionCandidates = [
            new Vec3(0, 1, 0),
            new Vec3(0, 0, 1),
            new Vec3(0, 0, -1),
            new Vec3(1, 0, 0),
            new Vec3(-1, 0, 0),
        ];
        const cursorCandidates = [
            new Vec3(0.5, 0.5, 0.5),
            new Vec3(0.5, 0.875, 0.5),
            new Vec3(0.5, 0.25, 0.5),
        ];
        let lastError = null;
        await bot.lookAt(containerBlock.position.offset(0.5, 0.5, 0.5), true);
        for (const direction of directionCandidates) {
            for (const cursorPos of cursorCandidates) {
                try {
                    updateContainerInteraction(bot, {
                        kind,
                        label,
                        source: "fallback",
                        blockName: containerBlock && containerBlock.name ? containerBlock.name : null,
                    });
                    if (bot._voyagerOriginalOpenBlock) {
                        return await bot._voyagerOriginalOpenBlock(containerBlock, direction, cursorPos);
                    }
                    await bot.activateBlock(containerBlock, direction, cursorPos);
                    return await waitForVoyagerWindowOpen(8000, label);
                } catch (err) {
                    lastError = err;
                    updateContainerInteraction(bot, {
                        kind,
                        label,
                        source: "fallback",
                        status: "error",
                        error: normalizeErrorMessage(err),
                    });
                    await settleInteraction();
                }
            }
        }
        throw lastError || new Error(`${label} fallback could not open block window`);
    }

    async function openChestWindowFallback(chestBlock, label = "chest") {
        const blocker = getChestBlocker(chestBlock);
        if (blocker) {
            const errorMessage = `Chest is blocked above by ${blocker.name}`;
            updateContainerInteraction(bot, {
                kind: "chest",
                label,
                source: "fallback",
                blockName: chestBlock && chestBlock.name ? chestBlock.name : null,
                status: "error",
                blockedAbove: true,
                blockedBy: blocker.name,
                error: errorMessage,
            });
            throw new Error(errorMessage);
        }
        return openGenericContainerWindowFallback(chestBlock, label, "chest");
    }

    async function openFurnaceWindowFallback(furnaceBlock, label = "furnace") {
        return openGenericContainerWindowFallback(furnaceBlock, label, "furnace");
    }

    async function openDirectContainer(kind, containerBlock, direction, cursorPos, label) {
        updateContainerInteraction(bot, {
            kind,
            label,
            source: "direct",
            blockName: containerBlock && containerBlock.name ? containerBlock.name : null,
            status: "attempt",
            error: null,
            blockedAbove: false,
            blockedBy: null,
        });
        if (kind === "chest") {
            const blocker = getChestBlocker(containerBlock);
            if (blocker) {
                const errorMessage = `Chest is blocked above by ${blocker.name}`;
                updateContainerInteraction(bot, {
                    kind,
                    label,
                    source: "direct",
                    status: "error",
                    blockedAbove: true,
                    blockedBy: blocker.name,
                    error: errorMessage,
                });
                throw new Error(errorMessage);
            }
        }
        if (kind === "furnace") {
            if (!bot._voyagerOriginalOpenFurnace) {
                throw new Error("Original openFurnace is unavailable");
            }
            return await bot._voyagerOriginalOpenFurnace(containerBlock);
        }
        if (!bot._voyagerOriginalOpenContainer) {
            throw new Error("Original openContainer is unavailable");
        }
        return await bot._voyagerOriginalOpenContainer(containerBlock, direction, cursorPos);
    }

    async function openChestWithRecovery(chestBlock, label = "chest") {
        return withWindowRecovery("chest", label, async () => {
            await gotoLookAtBlockWithTimeout(chestBlock, label);
            try {
                return await openDirectContainer("chest", chestBlock, undefined, undefined, label);
            } catch (err) {
                return await openChestWindowFallback(chestBlock, label);
            }
        }, 1, chestBlock);
    }

    async function openFurnaceWithRecovery(furnaceBlock, label = "furnace") {
        return withWindowRecovery("furnace", label, async () => {
            await gotoLookAtBlockWithTimeout(furnaceBlock, label);
            try {
                return await openDirectContainer("furnace", furnaceBlock, undefined, undefined, label);
            } catch (err) {
                return await openFurnaceWindowFallback(furnaceBlock, label);
            }
        }, 1, furnaceBlock);
    }

    async function openGenericContainerWithRecovery(containerBlock, label = "container") {
        return withWindowRecovery("generic", label, async () => {
            await gotoLookAtBlockWithTimeout(containerBlock, label);
            try {
                return await openDirectContainer("generic", containerBlock, undefined, undefined, label);
            } catch (err) {
                return await openGenericContainerWindowFallback(containerBlock, label, "generic");
            }
        }, 1, containerBlock);
    }

    async function openContainerWithRecovery(containerBlock, label = "container") {
        const kind = classifyContainerKind(containerBlock, label);
        if (kind === "chest") {
            return openChestWithRecovery(containerBlock, label);
        }
        if (kind === "furnace") {
            return openFurnaceWithRecovery(containerBlock, label);
        }
        return openGenericContainerWithRecovery(containerBlock, label);
    }

    bot.openContainer = async (containerBlock, direction, cursorPos) => {
        const kind = classifyContainerKind(containerBlock, "container");
        const label = kind === "chest" ? "chest" : kind === "furnace" ? "furnace" : "container";
        try {
            const result = await openDirectContainer(kind, containerBlock, direction, cursorPos, label);
            updateContainerInteraction(bot, {
                kind,
                label,
                source: "direct",
                status: bot._voyagerWindowOpened ? "opened" : "success",
                error: null,
            });
            return result;
        } catch (err) {
            if (containerBlock && containerBlock.position) {
                if (kind === "chest") {
                    return await openChestWindowFallback(containerBlock, label);
                }
                if (kind === "furnace") {
                    return await openFurnaceWindowFallback(containerBlock, label);
                }
                return await openGenericContainerWindowFallback(containerBlock, label, kind);
            }
            throw err;
        }
    };
    bot.openChest = async (containerBlock, direction, cursorPos) => bot.openContainer(containerBlock, direction, cursorPos);
    bot.openDispenser = async (containerBlock, direction, cursorPos) => bot.openContainer(containerBlock, direction, cursorPos);
    bot.openFurnace = async (furnaceBlock) => {
        try {
            const result = await openDirectContainer("furnace", furnaceBlock, undefined, undefined, "furnace");
            updateContainerInteraction(bot, {
                kind: "furnace",
                label: "furnace",
                source: "direct",
                status: bot._voyagerWindowOpened ? "opened" : "success",
                error: null,
            });
            return result;
        } catch (err) {
            if (furnaceBlock && furnaceBlock.position) {
                return await openFurnaceWindowFallback(furnaceBlock, "furnace");
            }
            throw err;
        }
    };

    // Retrieve array form post bod
    const code = req.body.code;
    const programs = req.body.programs;
    bot.cumulativeObs = [];
    await bot.waitForTicks(bot.waitTicks);
    const r = await evaluateCode(code, programs);
    process.off("uncaughtException", otherError);
    if (r !== "success") {
        recordVoyagerActionError(bot, handleError(r));
    }
    await returnItems();
    // wait for last message
    await bot.waitForTicks(bot.waitTicks);
    if (!response_sent) {
        response_sent = true;
        res.json(observeWithVoyagerState(bot));
    }
    bot.removeListener("physicsTick", onTick);

    async function evaluateCode(code, programs) {
        // Echo the code produced for players to see it. Don't echo when the bot code is already producing dialog or it will double echo
        try {
            await eval("(async () => {" + programs + "\n" + code + "})()");
            return "success";
        } catch (err) {
            return err;
        }
    }

    function onStuck(posThreshold) {
        const currentPos = bot?.entity?.position;
        if (!currentPos) {
            return;
        }
        bot.stuckPosList.push(currentPos);

        // Check if the list is full
        if (bot.stuckPosList.length === 5) {
            const oldestPos = bot.stuckPosList[0];
            const posDifference = currentPos.distanceTo(oldestPos);

            if (posDifference < posThreshold) {
                teleportBot(); // execute the function
            }

            // Remove the oldest time from the list
            bot.stuckPosList.shift();
        }
    }

    function teleportBot() {
        const blocks = bot.findBlocks({
            matching: (block) => {
                return block.type === 0;
            },
            maxDistance: 1,
            count: 27,
        });

        if (Array.isArray(blocks) && blocks.length > 0) {
            const randomIndex = Math.floor(Math.random() * blocks.length);
            const block = blocks[randomIndex];
            if (block && Number.isFinite(block.x) && Number.isFinite(block.y) && Number.isFinite(block.z)) {
                bot.chat(`/tp @s ${block.x} ${block.y} ${block.z}`);
                return;
            }
        }
        bot.chat("/tp @s ~ ~1.25 ~");
    }

    function returnItems() {
        bot.chat("/gamerule doTileDrops false");
        const crafting_table = bot.findBlock({
            matching: mcData.blocksByName.crafting_table.id,
            maxDistance: 128,
        });
        if (crafting_table) {
            bot.chat(
                `/setblock ${crafting_table.position.x} ${crafting_table.position.y} ${crafting_table.position.z} air destroy`
            );
            bot.chat("/give @s crafting_table");
        }
        const furnace = bot.findBlock({
            matching: mcData.blocksByName.furnace.id,
            maxDistance: 128,
        });
        if (furnace) {
            bot.chat(
                `/setblock ${furnace.position.x} ${furnace.position.y} ${furnace.position.z} air destroy`
            );
            bot.chat("/give @s furnace");
        }
        if (bot.inventoryUsed() >= 32) {
            // if chest is not in bot's inventory
            if (!bot.inventory.items().find((item) => item.name === "chest")) {
                bot.chat("/give @s chest");
            }
        }
        // if iron_pickaxe not in bot's inventory and bot.iron_pickaxe
        if (
            bot.iron_pickaxe &&
            !bot.inventory.items().find((item) => item.name === "iron_pickaxe")
        ) {
            bot.chat("/give @s iron_pickaxe");
        }
        bot.chat("/gamerule doTileDrops true");
    }

    function handleError(err) {
        let stack = err.stack;
        if (!stack) {
            return err;
        }
        console.log(stack);
        const final_line = stack.split("\n")[1];
        const regex = /<anonymous>:(\d+):\d+\)/;

        const programs_length = programs.split("\n").length;
        let match_line = null;
        for (const line of stack.split("\n")) {
            const match = regex.exec(line);
            if (match) {
                const line_num = parseInt(match[1]);
                if (line_num >= programs_length) {
                    match_line = line_num - programs_length;
                    break;
                }
            }
        }
        if (!match_line) {
            return err.message;
        }
        let f_line = final_line.match(
            /\((?<file>.*):(?<line>\d+):(?<pos>\d+)\)/
        );
        if (f_line && f_line.groups && fs.existsSync(f_line.groups.file)) {
            const { file, line, pos } = f_line.groups;
            const f = fs.readFileSync(file, "utf8").split("\n");
            // let filename = file.match(/(?<=node_modules\\)(.*)/)[1];
            let source = file + `:${line}\n${f[line - 1].trim()}\n `;

            const code_source =
                "at " +
                code.split("\n")[match_line - 1].trim() +
                " in your code";
            return source + err.message + "\n" + code_source;
        } else if (
            f_line &&
            f_line.groups &&
            f_line.groups.file.includes("<anonymous>")
        ) {
            const { file, line, pos } = f_line.groups;
            let source =
                "Your code" +
                `:${match_line}\n${code.split("\n")[match_line - 1].trim()}\n `;
            let code_source = "";
            if (line < programs_length) {
                source =
                    "In your program code: " +
                    programs.split("\n")[line - 1].trim() +
                    "\n";
                code_source = `at line ${match_line}:${code
                    .split("\n")
                    [match_line - 1].trim()} in your code`;
            }
            return source + err.message + "\n" + code_source;
        }
        return err.message;
    }
});

app.post("/stop", (req, res) => {
    clearReconnectTimer();
    if (bot) {
        bot._voyagerIntentionalStop = true;
        bot._voyagerAutoReconnectEnabled = false;
        clearVoyagerTelemetryTimer(bot);
        bot.end();
        bot = null;
    }
    res.json({
        message: "Bot stopped",
    });
});

app.post("/telemetry", (req, res) => {
    if (!bot) {
        res.status(400).json({ error: "Bot not spawned" });
        return;
    }
    const telemetry = refreshVoyagerTelemetry(bot);
    res.json(telemetry || {});
});

app.post("/pause", (req, res) => {
    if (!bot) {
        res.status(400).json({ error: "Bot not spawned" });
        return;
    }
    bot.chat("/pause");
    bot.waitForTicks(bot.waitTicks).then(() => {
        res.json({ message: "Success" });
    });
});

// Server listening to PORT 3000

const DEFAULT_PORT = 3000;
const PORT = process.argv[2] || DEFAULT_PORT;
app.listen(PORT, () => {
    console.log(`Server started on port ${PORT}`);
});
