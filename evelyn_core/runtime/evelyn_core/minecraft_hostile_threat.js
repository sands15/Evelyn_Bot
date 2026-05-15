'use strict';

const DEFAULT_THRESHOLDS = Object.freeze({
  emergency: 85,
  nearInterrupt: 65,
  track: 25,
  prepare: 50,
  nearInterruptDistance: 8,
});

function clamp(value, min, max) {
  const n = Number(value);
  if (!Number.isFinite(n)) return min;
  return Math.max(min, Math.min(max, n));
}

function classifySpaceContext(obs = {}) {
  const position = obs.position || {};
  const y = Number(position.y ?? obs.bot_y ?? 0);
  const skyVisible = !!obs.sky_visible;
  const nearbyAir = Number(obs.nearby_air_count || 0);
  const caveOpeningScore = Number(obs.cave_opening_score || 0);
  const tunnelScore = Number(obs.tunnel_score || 0);
  const branchMiningScore = Number(obs.branch_mining_score || 0);

  if (skyVisible && y >= 60) return 'surface';
  if (branchMiningScore >= 0.75) return 'branch_mining';
  if (tunnelScore >= 0.7) return 'tunnel';
  if (caveOpeningScore >= 0.6 || nearbyAir >= 80) return 'open_cave';
  return 'underground_small_space';
}

function getDetectionRadius(context, obs = {}) {
  const isDay = obs.is_day;
  if (context === 'surface') return isDay === false ? 24 : 16;
  if (context === 'open_cave') return 32;
  if (context === 'tunnel') return 10;
  if (context === 'branch_mining') return 8;
  return 16;
}

function surfaceHostileFilter(botPos = {}, hostile = {}) {
  const hostilePos = hostile.position || {};
  const dy = Number(hostilePos.y ?? 0) - Number(botPos.y ?? 0);
  if (dy <= -4) return 'ignore';
  if (Math.abs(dy) > 6) return 'ignore';
  if (!hostile.line_of_sight && !hostile.path_reachable) return 'ignore';
  return 'consider';
}

function normalizeVector(vec = {}) {
  const x = Number(vec.x || 0);
  const y = Number(vec.y || 0);
  const z = Number(vec.z || 0);
  const len = Math.sqrt(x * x + y * y + z * z);
  if (!Number.isFinite(len) || len <= 0) return null;
  return { x: x / len, y: y / len, z: z / len };
}

function vectorAngleDeg(a, b) {
  const na = normalizeVector(a);
  const nb = normalizeVector(b);
  if (!na || !nb) return 180;
  const dot = clamp(na.x * nb.x + na.y * nb.y + na.z * nb.z, -1, 1);
  return Math.acos(dot) * 180 / Math.PI;
}

function isInForwardCone(bot = {}, hostile = {}, maxAngleDeg = 45) {
  const botPos = bot.position || {};
  const hostilePos = hostile.position || {};
  const forward = bot.look_vector || bot.lookVector;
  if (!forward) return false;
  const toHostile = {
    x: Number(hostilePos.x || 0) - Number(botPos.x || 0),
    y: Number(hostilePos.y || 0) - Number(botPos.y || 0),
    z: Number(hostilePos.z || 0) - Number(botPos.z || 0),
  };
  return vectorAngleDeg(forward, toHostile) <= Number(maxAngleDeg || 45);
}

function calculateThreatScore(obs = {}, hostile = {}, context = null) {
  const resolvedContext = context || obs.space_context || classifySpaceContext(obs);
  const botPos = obs.position || {};
  const hostilePos = hostile.position || {};
  const distance = Number(hostile.distance ?? Infinity);
  const dy = Number(hostilePos.y ?? 0) - Number(botPos.y ?? 0);
  let score = 0;

  if (distance <= 4) score += 60;
  else if (distance <= 8) score += 40;
  else if (distance <= 16) score += 20;
  else score += 5;

  if (hostile.line_of_sight) score += 30;
  else score -= 25;

  if (hostile.path_reachable) score += 25;
  else score -= 20;

  if (resolvedContext === 'surface' && dy <= -4) score -= 80;
  if (resolvedContext === 'surface' && Math.abs(dy) > 6) score -= 50;

  if (resolvedContext === 'open_cave') score += 15;
  if (resolvedContext === 'tunnel' || resolvedContext === 'branch_mining') {
    if (hostile.in_forward_cone) score += 20;
    else score -= 30;
  }

  if (resolvedContext === 'surface' && surfaceHostileFilter(botPos, hostile) === 'ignore') {
    score = Math.min(score, 20);
  }

  return clamp(score, 0, 100);
}

function classifyThreatAction(score, hostile = {}, thresholds = DEFAULT_THRESHOLDS) {
  const distance = Number(hostile.distance ?? Infinity);
  if (score >= thresholds.emergency) return 'emergency_retreat';
  if (score >= thresholds.nearInterrupt && distance <= thresholds.nearInterruptDistance) return 'interrupt_near_hostile';
  if (score >= thresholds.prepare) return 'prepare_or_avoid';
  if (score >= thresholds.track) return 'track_only';
  return 'ignore';
}

function shouldFreezeOrInterrupt(obs = {}, hostile = {}, context = null, thresholds = DEFAULT_THRESHOLDS) {
  const score = Number(hostile.threat_score ?? calculateThreatScore(obs, hostile, context));
  const distance = Number(hostile.distance ?? Infinity);
  if (score >= thresholds.emergency) return { interrupt: true, reason: 'immediate_danger', score };
  if (score >= thresholds.nearInterrupt && distance <= thresholds.nearInterruptDistance) {
    return { interrupt: true, reason: 'near_reachable_hostile', score };
  }
  return { interrupt: false, reason: 'track_only', score };
}

function buildThreatAssessment(obs = {}, hostileSnapshots = [], thresholds = DEFAULT_THRESHOLDS) {
  const context = obs.space_context || classifySpaceContext(obs);
  const radius = getDetectionRadius(context, obs);
  const botForCone = { position: obs.position, look_vector: obs.look_vector };
  const considered = [];

  for (const raw of hostileSnapshots || []) {
    if (!raw || !raw.position) continue;
    const distance = Number(raw.distance ?? Infinity);
    if (!Number.isFinite(distance) || distance > radius) continue;
    const enriched = { ...raw };
    if (enriched.in_forward_cone == null) {
      enriched.in_forward_cone = isInForwardCone(botForCone, enriched, context === 'tunnel' ? 50 : 45);
    }
    enriched.threat_score = calculateThreatScore({ ...obs, space_context: context }, enriched, context);
    enriched.threat_action = classifyThreatAction(enriched.threat_score, enriched, thresholds);
    enriched.interrupt_decision = shouldFreezeOrInterrupt({ ...obs, space_context: context }, enriched, context, thresholds);
    considered.push(enriched);
  }

  considered.sort((a, b) => {
    const scoreDelta = Number(b.threat_score || 0) - Number(a.threat_score || 0);
    if (scoreDelta) return scoreDelta;
    return Number(a.distance || Infinity) - Number(b.distance || Infinity);
  });

  const highest = considered[0] || null;
  return {
    version: 1,
    space_context: context,
    detection_radius: radius,
    thresholds: { ...thresholds },
    highest_threat_score: highest ? Number(highest.threat_score || 0) : 0,
    highest_threat: highest,
    interrupt: highest ? highest.interrupt_decision : { interrupt: false, reason: 'no_hostile', score: 0 },
    threat_hostiles_nearby: considered.filter((item) => Number(item.threat_score || 0) >= thresholds.prepare).length,
    tracked_hostiles_nearby: considered.filter((item) => Number(item.threat_score || 0) >= thresholds.track).length,
    hostiles: considered,
  };
}

module.exports = {
  DEFAULT_THRESHOLDS,
  classifySpaceContext,
  getDetectionRadius,
  surfaceHostileFilter,
  isInForwardCone,
  calculateThreatScore,
  classifyThreatAction,
  shouldFreezeOrInterrupt,
  buildThreatAssessment,
};
