package dev.evelyn.minecraft;

import java.util.Objects;
import java.util.UUID;

/**
 * Read-only player state. The connection owns packet decoding, event numbering and
 * the post-invalidation callback fence. All methods synchronize so a socket deadline
 * thread may invalidate while the network owner is between updates.
 * No world readiness, velocity, inventory, entity state or action capability is inferred.
 */
public final class PlayerObservation {
    public record Position(double x, double y, double z, float yaw, float pitch) {
        public Position {
            if (!Double.isFinite(x) || !Double.isFinite(y) || !Double.isFinite(z)
                    || !Float.isFinite(yaw) || !Float.isFinite(pitch) || pitch < -90 || pitch > 90)
                throw new IllegalArgumentException("invalid_position");
        }
    }
    public record Health(float health, int food, float saturation) {
        public Health {
            if (!Float.isFinite(health) || health < 0 || food < 0
                    || !Float.isFinite(saturation) || saturation < 0)
                throw new IllegalArgumentException("invalid_health");
        }
    }
    /**
     * connectionId is a newly generated connection identifier, never the account UUID.
     * available means a play-login binding was observed, not that the world is ready.
     * Null position/health mean unknown. Timestamps are the local signed nanoTime clock.
     * sequence counts publications; eventSequence is supplied by the connection owner.
     */
    public record Snapshot(UUID connectionId, int generation, int sequence, int eventSequence,
                           long observedAtNanos, boolean available, String dimension,
                           Position position, Health health, ChunkWorld.Sample terrain) {
        public Snapshot(UUID connectionId, int generation, int sequence, int eventSequence,
                        long observedAtNanos, boolean available, String dimension, Position position, Health health) {
            this(connectionId, generation, sequence, eventSequence, observedAtNanos, available, dimension, position, health, null);
        }
        public Snapshot {
            Objects.requireNonNull(connectionId, "connection_id");
            if (generation < 1 || sequence < 1 || eventSequence < 0)
                throw new IllegalArgumentException("invalid_snapshot_counter");
            if (available) validateDimension(dimension);
            else if (dimension != null || position != null || health != null)
                throw new IllegalArgumentException("unavailable_snapshot_has_data");
            if (terrain != null) {
                ChunkWorld.Snapshot world = terrain.snapshot();
                if (!available || !world.available() || world.closed() || !connectionId.equals(world.connectionId())
                        || generation != world.generation() || !dimension.equals(world.dimension())
                        || world.eventSequence() < 0 || world.eventSequence() > eventSequence || observedAtNanos - world.observedAtNanos() < 0)
                    throw new IllegalArgumentException("terrain_binding_mismatch");
                if (world.minY() < -2032 || world.minY() > 2031 || world.minY() % 16 != 0
                        || world.height() < 16 || world.height() > 4064 || world.height() % 16 != 0
                        || world.minY() + world.height() > 2032 || world.loadedChunks() < 0 || world.loadedChunks() > ChunkWorld.MAX_CHUNKS
                        || terrain.blocks().size() > ChunkWorld.MAX_SAMPLE)
                    throw new IllegalArgumentException("invalid_terrain");
                java.util.Set<ChunkWorld.BlockPos> seen = new java.util.HashSet<>();
                for (ChunkWorld.Block block : terrain.blocks()) {
                    ChunkWorld.BlockPos p = block.position(); Integer id = block.stateId();
                    if (p == null || !seen.add(p) || p.x() < -33554432 || p.x() > 33554431 || p.z() < -33554432 || p.z() > 33554431
                            || p.y() < -2048 || p.y() > 2047 || id != null && (id < 0 || world.loadedChunks() == 0
                            || p.y() < world.minY() || p.y() >= world.minY() + world.height()))
                        throw new IllegalArgumentException("invalid_terrain_block");
                }
            }
        }
    }

    private static final int MAX_BUNDLE_UPDATES = 4096;
    private final long originNanos;
    private Snapshot published;
    private Snapshot staged;
    private boolean bundling;
    private int bundleUpdates;
    private int lastEventSequence;
    private long lastObservedNanos;

    public PlayerObservation(UUID connectionId, long nowNanos) {
        originNanos = nowNanos;
        lastObservedNanos = nowNanos;
        published = new Snapshot(connectionId, 1, 1, 0, nowNanos, false, null, null, null);
    }
    public synchronized Snapshot snapshot() { return published; }

    /** One binding per generation. A generation starts unknown and requires fresh data. */
    public synchronized void player(int eventSequence, long nowNanos, String dimension) {
        checkEvent(eventSequence, nowNanos);
        validateDimension(dimension);
        Snapshot base = current();
        if (base.available()) throw new IllegalStateException("player_already_bound");
        store(new Snapshot(base.connectionId(), base.generation(), nextSequence(), eventSequence,
                nowNanos, true, dimension, null, null));
    }
    /** The source is a decoded server correction, not a claim of local movement success. */
    public synchronized void position(int eventSequence, long nowNanos, double x, double y, double z,
                                      float yaw, float pitch) {
        checkEvent(eventSequence, nowNanos);
        Snapshot base = requirePlayer();
        Position position = new Position(x, y, z, yaw, pitch);
        store(new Snapshot(base.connectionId(), base.generation(), nextSequence(), eventSequence,
                nowNanos, true, base.dimension(), position, base.health()));
    }
    /** 26.3 CB 0x6A: FLOAT health, VAR_INT food, FLOAT saturation; no max-health=20 assumption. */
    public synchronized void health(int eventSequence, long nowNanos, float health, int food, float saturation) {
        checkEvent(eventSequence, nowNanos);
        Snapshot base = requirePlayer();
        Health vitals = new Health(health, food, saturation);
        store(new Snapshot(base.connectionId(), base.generation(), nextSequence(), eventSequence,
                nowNanos, true, base.dimension(), base.position(), vitals));
    }
    public synchronized void beginBundle() {
        if (bundling) throw new IllegalStateException("bundle_already_open");
        bundling = true;
        staged = published;
        bundleUpdates = 0;
    }
    /** World-only packets share the same publication boundary as player updates. */
    public synchronized void touch(int eventSequence, long nowNanos) {
        checkEvent(eventSequence, nowNanos);
        Snapshot base = requirePlayer();
        store(new Snapshot(base.connectionId(), base.generation(), nextSequence(), eventSequence,
                nowNanos, true, base.dimension(), base.position(), base.health()));
    }
    /** No intermediate update becomes observable, and an empty bundle does not invent an event. */
    public synchronized void endBundle() {
        if (!bundling) throw new IllegalStateException("bundle_not_open");
        if (bundleUpdates != 0) published = staged;
        staged = null;
        bundling = false;
        bundleUpdates = 0;
    }
    /**
     * Close, reconfiguration and respawn all use this boundary. It bypasses the bundle
     * update budget, discards the uncommitted bundle, and publishes a fresh unknown generation.
     * The caller must block callbacks from the retired connection/world before invoking it.
     */
    public synchronized void invalidate(int eventSequence, long nowNanos) {
        checkClockAndEvent(eventSequence, nowNanos);
        int generation = increment(published.generation(), "generation_exhausted");
        Snapshot cleared = new Snapshot(published.connectionId(), generation, nextSequence(), eventSequence,
                nowNanos, false, null, null, null);
        published = cleared;
        staged = null;
        bundling = false;
        bundleUpdates = 0;
        lastEventSequence = eventSequence;
        lastObservedNanos = nowNanos;
    }
    private Snapshot current() { return bundling ? staged : published; }
    private Snapshot requirePlayer() {
        Snapshot base = current();
        if (!base.available()) throw new IllegalStateException("player_unavailable");
        return base;
    }
    private void checkClockAndEvent(int eventSequence, long nowNanos) {
        if (eventSequence <= lastEventSequence) throw new IllegalArgumentException("event_not_increasing");
        // Subtraction supports nanoTime wrapping through Long.MAX_VALUE. The connection's
        // bounded lifetime is far shorter than the clock's half-range (~292 years).
        if (nowNanos - originNanos < 0 || nowNanos - lastObservedNanos < 0)
            throw new IllegalArgumentException("observation_clock_reversed");
    }
    private void checkEvent(int eventSequence, long nowNanos) {
        checkClockAndEvent(eventSequence, nowNanos);
        if (bundling && bundleUpdates == MAX_BUNDLE_UPDATES)
            throw new IllegalStateException("bundle_update_budget");
    }
    private int nextSequence() { return increment(published.sequence(), "sequence_exhausted"); }
    private static int increment(int value, String code) {
        if (value == Integer.MAX_VALUE) throw new IllegalStateException(code);
        return value + 1;
    }
    private void store(Snapshot next) {
        // All validation/record construction precedes this commit; rejected updates do not
        // alter any counters, timestamps, pending fields or the published observation.
        if (bundling) { staged = next; bundleUpdates++; }
        else published = next;
        lastEventSequence = next.eventSequence();
        lastObservedNanos = next.observedAtNanos();
    }
    private static void validateDimension(String dimension) {
        if (dimension == null || dimension.length() > 128
                || !dimension.matches("[a-z0-9_.-]+:[a-z0-9/._-]+"))
            throw new IllegalArgumentException("invalid_dimension");
    }
}
