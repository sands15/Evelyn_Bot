package dev.evelyn.minecraft;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;

/**
 * Bounded read-only raw block-state observations for one server connection.
 * Full chunks establish knowledge; updates never make an unknown chunk known.
 * Metadata and at most 64 sampled values escape this model, never world arrays.
 * All methods are serialized; the host additionally binds player/world events.
 */
public final class ChunkWorld {
    public static final int MAX_CHUNKS = 256;
    public static final long MAX_STATE_BYTES = 32L * 1024 * 1024;
    public static final int MAX_HEIGHT = 4096;
    public static final int MAX_SAMPLE = 64;
    private static final int SECTION_CELLS = 4096;
    private static final long SECTION_BYTES = SECTION_CELLS * (long) Integer.BYTES;
    private static final int MAX_BUNDLE_UPDATES = 4096;

    public record Event(int generation, int eventSequence, long observedAtNanos) {}
    public record Snapshot(UUID connectionId, int generation, int sequence, int eventSequence,
                           long observedAtNanos, boolean available, String dimension,
                           int minY, int height, int loadedChunks, long stateBytes, boolean closed) {}
    public record BlockPos(int x, int y, int z) {}
    /** A null stateId means unknown; zero is a known raw state ID. */
    public record Block(BlockPos position, Integer stateId) {}
    public record Sample(Snapshot snapshot, List<Block> blocks) {
        public Sample { blocks = List.copyOf(blocks); }
    }
    public static final class Failure extends IllegalStateException {
        private final String code;
        private Failure(String code) { super(code); this.code = code; }
        public String code() { return code; }
    }

    private record Dimension(String name, int minY, int height) {}
    private record Chunk(int[][] sections) {}
    private record State(Snapshot snapshot, Dimension dimension, Map<Long, Chunk> chunks) {}
    private final UUID connectionId;
    private final long originNanos;
    private State published;
    private State staged;
    private int lastEvent;
    private long lastObserved;
    private int bundleUpdates;
    private boolean bundling;
    private boolean closed;

    public ChunkWorld(UUID connectionId, long originNanos) {
        this.connectionId = Objects.requireNonNull(connectionId, "connectionId");
        this.originNanos = originNanos;
        lastObserved = originNanos;
        published = empty(1, 1, 0, originNanos, false);
    }

    public synchronized void bind(Event event, String dimension, int minY, int height) {
        checkEvent(event, false);
        if (working().dimension != null) throw fail("already_bound");
        if (dimension == null || dimension.length() > 128
                || !dimension.matches("[a-z0-9_.-]+:[a-z0-9/._-]+")
                || minY % 16 != 0 || height <= 0 || height > MAX_HEIGHT || height % 16 != 0
                || (long) minY + height > Integer.MAX_VALUE) throw fail("invalid_dimension");
        commit(event, new Dimension(dimension, minY, height), Map.of());
    }

    /** Copies all sections. Input ordering is section Y, then (localY << 8 | localZ << 4 | localX). */
    public synchronized void replaceChunk(Event event, int chunkX, int chunkZ, int[][] sections) {
        State state = bound(event);
        if (sections == null || sections.length != state.dimension.height / 16) throw fail("invalid_chunk");
        long key = key(chunkX, chunkZ);
        if (!state.chunks.containsKey(key) && state.chunks.size() == MAX_CHUNKS) throw fail("chunk_limit");
        reserve(sections.length * SECTION_BYTES);
        int[][] owned = new int[sections.length][];
        for (int s = 0; s < owned.length; s++) {
            int[] source = sections[s];
            if (source == null || source.length != SECTION_CELLS) throw fail("invalid_chunk");
            int[] copy = new int[SECTION_CELLS];
            for (int i = 0; i < copy.length; i++) {
                int id = source[i];
                if (id < 0) throw fail("invalid_state");
                copy[i] = id;
            }
            owned[s] = copy;
        }
        Map<Long, Chunk> chunks = new HashMap<>(state.chunks);
        chunks.put(key, new Chunk(owned));
        commit(event, state.dimension, Map.copyOf(chunks));
    }

    public synchronized void updateBlock(Event event, int x, int y, int z, int stateId) {
        State state = bound(event);
        if (stateId < 0) throw fail("invalid_state");
        long key = key(x >> 4, z >> 4);
        Chunk chunk = state.chunks.get(key);
        int section = sectionIndex(state.dimension, y);
        if (chunk == null || section < 0) { commit(event, state.dimension, state.chunks); return; }
        reserve(SECTION_BYTES);
        int[][] sections = chunk.sections.clone();
        sections[section] = sections[section].clone();
        sections[section][(y & 15) << 8 | (z & 15) << 4 | (x & 15)] = stateId;
        replaceSection(event, state, key, sections);
    }

    /** Wire positions are (localX << 8 | localZ << 4 | localY); duplicate entries are last-wins. */
    public synchronized void updateSection(Event event, int sectionX, int sectionY, int sectionZ,
                                           int[] positions, int[] stateIds) {
        State state = bound(event);
        if (positions == null || stateIds == null || positions.length != stateIds.length
                || positions.length > SECTION_CELLS) throw fail("invalid_section_update");
        long key = key(sectionX, sectionZ);
        Chunk chunk = state.chunks.get(key);
        long offset = (long) sectionY - (state.dimension.minY >> 4);
        boolean applies = chunk != null && offset >= 0 && offset < state.dimension.height / 16 && positions.length != 0;
        // Reserve patch copies and the replacement section before allocating any owned raw arrays.
        if (applies) reserve(SECTION_BYTES + positions.length * 2L * Integer.BYTES);
        int[] localPositions = applies ? positions.clone() : positions;
        int[] ids = applies ? stateIds.clone() : stateIds;
        for (int i = 0; i < localPositions.length; i++) {
            if (localPositions[i] < 0 || localPositions[i] >= SECTION_CELLS || ids[i] < 0)
                throw fail("invalid_section_update");
        }
        if (!applies) {
            commit(event, state.dimension, state.chunks); return;
        }
        int[][] sections = chunk.sections.clone();
        int[] cells = sections[(int) offset].clone();
        sections[(int) offset] = cells;
        for (int i = 0; i < localPositions.length; i++) {
            int p = localPositions[i];
            cells[(p & 15) << 8 | (p & 0xf0) | (p >> 8)] = ids[i];
        }
        replaceSection(event, state, key, sections);
    }

    private void replaceSection(Event event, State state, long key, int[][] sections) {
        Map<Long, Chunk> chunks = new HashMap<>(state.chunks);
        chunks.put(key, new Chunk(sections));
        commit(event, state.dimension, Map.copyOf(chunks));
    }

    public synchronized void unload(Event event, int chunkX, int chunkZ) {
        State state = bound(event);
        Map<Long, Chunk> chunks = new HashMap<>(state.chunks);
        chunks.remove(key(chunkX, chunkZ));
        commit(event, state.dimension, Map.copyOf(chunks));
    }

    public synchronized void beginBundle() {
        if (closed) throw fail("closed");
        if (bundling) throw fail("nested_bundle");
        staged = published; bundling = true; bundleUpdates = 0;
    }

    public synchronized void endBundle() {
        if (closed) throw fail("closed");
        if (!bundling) throw fail("no_bundle");
        if (bundleUpdates != 0) published = staged;
        discardBundle();
    }

    /** Reconfiguration/EOF discards a partial bundle immediately, before a later new binding. */
    public synchronized void invalidate(Event event) { clear(event, false); }

    /** Permanent close; a later host close event may align already-cleared failure metadata. */
    public synchronized void close(Event event) {
        Snapshot current = published.snapshot;
        if (closed && event != null && event.generation == current.generation
                && event.eventSequence == current.eventSequence && event.observedAtNanos == current.observedAtNanos) return;
        clear(event, true);
    }

    private void clear(Event event, boolean closing) {
        if (closed && !closing) throw fail("closed");
        checkEvent(event, true);
        int sequence = nextSequence();
        lastEvent = event.eventSequence; lastObserved = event.observedAtNanos;
        closed |= closing;
        published = empty(event.generation, sequence, lastEvent, lastObserved, closed);
        discardBundle();
    }

    public synchronized Snapshot snapshot() { return published.snapshot; }

    /** Sampling does not retain backing chunk storage and never reads an uncommitted bundle. */
    public synchronized Sample sample(List<BlockPos> positions) {
        if (positions == null || positions.size() > MAX_SAMPLE) throw fail("sample_limit");
        List<Block> blocks = new ArrayList<>(positions.size());
        for (BlockPos p : positions) {
            if (p == null || blocks.size() == MAX_SAMPLE) throw fail("invalid_sample");
            Integer id = null;
            if (published.dimension != null) {
                int section = sectionIndex(published.dimension, p.y);
                Chunk chunk = published.chunks.get(key(p.x >> 4, p.z >> 4));
                if (chunk != null && section >= 0)
                    id = chunk.sections[section][(p.y & 15) << 8 | (p.z & 15) << 4 | (p.x & 15)];
            }
            blocks.add(new Block(p, id));
        }
        return new Sample(published.snapshot, blocks);
    }

    private State working() { return bundling ? staged : published; }
    private State bound(Event event) {
        checkEvent(event, false);
        State state = working();
        if (state.dimension == null) throw fail("unbound");
        return state;
    }
    private void checkEvent(Event event, boolean nextGeneration) {
        if (closed && !nextGeneration) throw fail("closed");
        long expected = (long) published.snapshot.generation + (nextGeneration ? 1 : 0);
        if (event == null || event.generation != expected || event.eventSequence <= lastEvent
                || event.observedAtNanos - originNanos < 0 || event.observedAtNanos - lastObserved < 0)
            throw fail("stale_event");
        if (bundling && !nextGeneration && bundleUpdates == MAX_BUNDLE_UPDATES) throw fail("bundle_limit");
    }
    private void commit(Event event, Dimension dimension, Map<Long, Chunk> chunks) {
        int sequence = nextSequence();
        Snapshot meta = new Snapshot(connectionId, event.generation, sequence, event.eventSequence,
                event.observedAtNanos, true, dimension.name, dimension.minY, dimension.height,
                chunks.size(), chunks.size() * (long) (dimension.height / 16) * SECTION_BYTES, false);
        State state = new State(meta, dimension, chunks);
        lastEvent = event.eventSequence; lastObserved = event.observedAtNanos;
        if (bundling) { staged = state; bundleUpdates++; } else published = state;
    }
    private int nextSequence() {
        if (published.snapshot.sequence == Integer.MAX_VALUE) throw fail("sequence_exhausted");
        return published.snapshot.sequence + 1;
    }
    private void reserve(long candidateBytes) {
        IdentityHashMap<int[], Boolean> arrays = new IdentityHashMap<>();
        collectArrays(published, arrays);
        if (bundling) collectArrays(staged, arrays);
        if (candidateBytes > MAX_STATE_BYTES - arrays.size() * SECTION_BYTES) throw fail("state_byte_limit");
    }
    private static void collectArrays(State state, IdentityHashMap<int[], Boolean> arrays) {
        for (Chunk chunk : state.chunks.values()) for (int[] section : chunk.sections) arrays.put(section, Boolean.TRUE);
    }
    private static int sectionIndex(Dimension dimension, int y) {
        long offset = (long) y - dimension.minY;
        return offset < 0 || offset >= dimension.height ? -1 : (int) (offset >> 4);
    }
    private static long key(int x, int z) { return ((long) x << 32) | (z & 0xffffffffL); }
    private State empty(int generation, int sequence, int event, long observed, boolean isClosed) {
        return new State(new Snapshot(connectionId, generation, sequence, event, observed,
                false, null, 0, 0, 0, 0, isClosed), null, Map.of());
    }
    private void discardBundle() { staged = null; bundling = false; bundleUpdates = 0; }
    private Failure fail(String code) {
        if (!closed) {
            int sequence = published.snapshot.sequence;
            published = empty(published.snapshot.generation, sequence == Integer.MAX_VALUE ? sequence : sequence + 1,
                    lastEvent, lastObserved, true);
            closed = true;
            discardBundle();
        }
        return new Failure(code);
    }
}
