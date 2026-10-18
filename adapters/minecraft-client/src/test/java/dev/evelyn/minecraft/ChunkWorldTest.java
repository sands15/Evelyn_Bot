package dev.evelyn.minecraft;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.UUID;

/** Pure synthetic world state; no packet, socket, account or game initialization. */
public final class ChunkWorldTest {
    private static int checks;
    public static void main(String[] args) {
        knownUnknownAndCoordinates(); bundleAtomicity(); invalidationAndClose(); eventAndClockFences();
        inputFailureClears(); chunkAndByteBudgets(); bundleAndSampleBudgets(); patchWorkingStorageBudget();
        System.out.println("CHUNK_WORLD_PASS " + checks);
    }
    private static final class Harness {
        final long origin;
        final ChunkWorld world;
        int generation = 1, event;
        Harness() { this(-9_000_000_000L); }
        Harness(long origin) { this.origin = origin; world = new ChunkWorld(new UUID(0, 71), origin); }
        ChunkWorld.Event next() { return new ChunkWorld.Event(generation, ++event, origin + event * 1_000_000L); }
        void bind(int minY, int height) { world.bind(next(), "minecraft:overworld", minY, height); }
        void invalidate() { generation++; world.invalidate(next()); }
    }
    private static int[][] sections(int count, int state) {
        int[][] result = new int[count][4096];
        for (int[] section : result) Arrays.fill(section, state);
        return result;
    }
    private static Integer state(ChunkWorld world, int x, int y, int z) {
        return world.sample(List.of(new ChunkWorld.BlockPos(x, y, z))).blocks().getFirst().stateId();
    }
    private static void knownUnknownAndCoordinates() {
        Harness h = new Harness();
        check(!h.world.snapshot().available() && h.world.snapshot().generation() == 1, "initial world is unbound generation one");
        check(state(h.world, 0, 0, 0) == null, "unknown cannot mean raw state zero");
        h.bind(-64, 32);
        int[][] input = sections(2, 1); input[0][4095] = 0;
        h.world.replaceChunk(h.next(), -1, -2, input);
        check(state(h.world, -1, -49, -17) == 0, "negative coordinates must floor to the right chunk and preserve known zero");
        check(state(h.world, -16, -64, -32) == 1, "chunk minimum coordinates use first cell");
        check(state(h.world, -17, -64, -32) == null, "neighboring unloaded chunk remains unknown");
        check(state(h.world, -1, -65, -17) == null && state(h.world, -1, -32, -17) == null, "dimension bounds are exclusive above and unknown outside");
        input[0][4095] = 88; input[1] = null;
        check(state(h.world, -1, -49, -17) == 0 && state(h.world, -16, -48, -32) == 1, "caller arrays cannot alter owned chunk state");
        h.world.updateBlock(h.next(), -16, -48, -32, 7);
        check(state(h.world, -16, -48, -32) == 7 && state(h.world, -15, -48, -32) == 1, "single update changes exactly its cell");
        h.world.updateSection(h.next(), -1, -4, -2, new int[]{0x234, 0x234}, new int[]{8, 9});
        check(state(h.world, -14, -60, -29) == 9, "wire local x:z:y packing is translated to y:z:x storage and duplicate positions are last-wins");
        h.world.updateBlock(h.next(), -1, -65, -17, 12);
        check(state(h.world, -1, -65, -17) == null && h.world.snapshot().loadedChunks() == 1, "out-of-height update cannot manufacture known cells");
        ChunkWorld.Sample sample = h.world.sample(List.of(new ChunkWorld.BlockPos(-14, -60, -29)));
        try { sample.blocks().clear(); throw new AssertionError("sample list leaked mutability"); }
        catch (UnsupportedOperationException expected) { checks++; }
        h.world.replaceChunk(h.next(), -1, -2, sections(2, 22));
        check(state(h.world, -14, -60, -29) == 22 && sample.blocks().getFirst().stateId() == 9, "small retained samples are immutable values, not a view of mutable storage");
        h.world.unload(h.next(), -1, -2); h.world.updateBlock(h.next(), -14, -60, -29, 33);
        h.world.updateSection(h.next(), -1, -4, -2, new int[]{0x234}, new int[]{44});
        check(state(h.world, -14, -60, -29) == null && h.world.snapshot().loadedChunks() == 0, "late updates cannot revive an unloaded chunk");
        check(h.world.snapshot().stateBytes() == 0, "unload releases owned published state bytes");
    }
    private static void bundleAtomicity() {
        Harness h = new Harness(); h.bind(0, 16); h.world.replaceChunk(h.next(), 0, 0, sections(1, 1));
        ChunkWorld.Snapshot before = h.world.snapshot(); h.world.beginBundle();
        h.world.updateBlock(h.next(), 0, 0, 0, 2); h.world.replaceChunk(h.next(), 1, 0, sections(1, 3));
        check(h.world.snapshot().equals(before), "bundle must keep all published metadata unchanged");
        check(state(h.world, 0, 0, 0) == 1 && state(h.world, 16, 0, 0) == null, "sample must read only the previously published bundle");
        h.world.endBundle();
        check(state(h.world, 0, 0, 0) == 2 && state(h.world, 16, 0, 0) == 3, "bundle changes become visible together");
        check(h.world.snapshot().sequence() == before.sequence() + 1 && h.world.snapshot().eventSequence() == h.event, "bundle has one publication with the last accepted source event");
        before = h.world.snapshot(); h.world.beginBundle(); h.world.endBundle();
        check(h.world.snapshot().equals(before), "empty bundle does not invent a new observation");
        h.world.beginBundle(); h.world.unload(h.next(), 0, 0);
        check(state(h.world, 0, 0, 0) == 2, "bundle unload is also staged");
        h.world.endBundle(); check(state(h.world, 0, 0, 0) == null, "committed unload becomes unknown");
    }
    private static void invalidationAndClose() {
        Harness h = new Harness(); h.bind(0, 16); h.world.replaceChunk(h.next(), 0, 0, sections(1, 4));
        h.world.beginBundle(); h.world.updateBlock(h.next(), 0, 0, 0, 5); h.invalidate();
        check(!h.world.snapshot().available() && !h.world.snapshot().closed() && h.world.snapshot().generation() == 2, "EOF/reconfiguration invalidation discards both published and staged data immediately");
        check(state(h.world, 0, 0, 0) == null && h.world.snapshot().stateBytes() == 0, "invalidated data cannot remain queryable");
        h.bind(-16, 16); h.world.replaceChunk(h.next(), 0, 0, sections(1, 6));
        check(state(h.world, 0, -16, 0) == 6 && state(h.world, 0, 0, 0) == null, "new generation requires a new dimension and full chunk");
        h.generation++; ChunkWorld.Event close = h.next(); h.world.close(close); h.world.close(close);
        check(h.world.snapshot().closed() && h.world.snapshot().generation() == 3 && state(h.world, 0, -16, 0) == null, "close is latched, clearing, and idempotent");
        fails(h.world, () -> h.world.bind(h.next(), "minecraft:overworld", 0, 16));
        Harness stale = new Harness(); stale.bind(0, 16); stale.invalidate(); stale.bind(0, 16);
        fails(stale.world, () -> stale.world.updateBlock(new ChunkWorld.Event(1, ++stale.event, stale.origin + stale.event * 1_000_000L), 0, 0, 0, 1));
        stale.generation++; stale.world.close(stale.next());
        check(stale.world.snapshot().closed() && stale.world.snapshot().generation() == 3, "host close may align cleared failure metadata with the shared generation without reopening it");
    }
    private static void eventAndClockFences() {
        Harness h = new Harness(Long.MAX_VALUE - 2_000_000L); h.bind(0, 16); h.event += 5;
        h.world.replaceChunk(h.next(), 0, 0, sections(1, 0));
        check(h.world.snapshot().observedAtNanos() < 0 && h.world.snapshot().eventSequence() == 7, "event gaps and short signed nanoTime wrap are valid");
        h.world.updateBlock(new ChunkWorld.Event(1, ++h.event, h.world.snapshot().observedAtNanos()), 0, 0, 0, 2);
        check(state(h.world, 0, 0, 0) == 2, "equal source time with increasing event is valid");
        long last = h.world.snapshot().observedAtNanos();
        fails(h.world, () -> h.world.updateBlock(new ChunkWorld.Event(1, ++h.event, last - 1), 0, 0, 0, 3));
        Harness duplicate = new Harness(); duplicate.bind(0, 16);
        fails(duplicate.world, () -> duplicate.world.unload(new ChunkWorld.Event(1, 1, duplicate.origin + 2_000_000L), 0, 0));
    }
    private static void inputFailureClears() {
        for (String failure : new String[]{"section-size", "negative-id", "position", "length", "dimension", "height", "duplicate-bind", "unbound"}) {
            Harness h = new Harness();
            if (!failure.equals("unbound") && !failure.equals("dimension") && !failure.equals("height")) { h.bind(0, 16); h.world.replaceChunk(h.next(), 0, 0, sections(1, 7)); }
            switch (failure) {
                case "section-size" -> fails(h.world, () -> h.world.replaceChunk(h.next(), 0, 0, new int[][]{new int[4095]}));
                case "negative-id" -> fails(h.world, () -> h.world.updateSection(h.next(), 0, 0, 0, new int[]{0, 1}, new int[]{8, -1}));
                case "position" -> fails(h.world, () -> h.world.updateSection(h.next(), 0, 0, 0, new int[]{4096}, new int[]{8}));
                case "length" -> fails(h.world, () -> h.world.updateSection(h.next(), 0, 0, 0, new int[]{0}, new int[0]));
                case "dimension" -> fails(h.world, () -> h.world.bind(h.next(), "Not Canonical", 0, 16));
                case "height" -> fails(h.world, () -> h.world.bind(h.next(), "minecraft:overworld", Integer.MAX_VALUE - 15, 32));
                case "duplicate-bind" -> fails(h.world, () -> h.world.bind(h.next(), "minecraft:the_nether", 0, 16));
                case "unbound" -> fails(h.world, () -> h.world.updateBlock(h.next(), 0, 0, 0, 1));
            }
        }
    }
    private static void chunkAndByteBudgets() {
        Harness chunks = new Harness(); chunks.bind(0, 16); int[][] small = sections(1, 0);
        for (int i = 0; i < 256; i++) chunks.world.replaceChunk(chunks.next(), i, 0, small);
        check(chunks.world.snapshot().loadedChunks() == 256 && chunks.world.snapshot().stateBytes() == 256L * 4096 * 4, "256 chunks fit with explicit raw-state byte accounting");
        fails(chunks.world, () -> chunks.world.replaceChunk(chunks.next(), 256, 0, small));
        Harness bytes = new Harness(); bytes.bind(0, 4096); int[][] tall = sections(256, 0);
        for (int i = 0; i < 8; i++) bytes.world.replaceChunk(bytes.next(), i, 0, tall);
        check(bytes.world.snapshot().stateBytes() == 32L * 1024 * 1024, "exactly 32 MiB of owned raw arrays fits");
        fails(bytes.world, () -> bytes.world.replaceChunk(bytes.next(), 8, 0, tall));
        Harness staged = new Harness(); staged.bind(0, 4096);
        for (int i = 0; i < 7; i++) staged.world.replaceChunk(staged.next(), i, 0, tall);
        staged.world.beginBundle(); staged.world.replaceChunk(staged.next(), 0, 0, tall);
        check(staged.world.snapshot().stateBytes() == 28L * 1024 * 1024, "staging must not alter published byte count");
        fails(staged.world, () -> staged.world.updateBlock(staged.next(), 0, 0, 0, 1));
    }
    private static void bundleAndSampleBudgets() {
        Harness bundle = new Harness(); bundle.bind(0, 16); bundle.world.beginBundle();
        for (int i = 0; i < 4096; i++) bundle.world.updateBlock(bundle.next(), 0, 0, 0, 0);
        check(bundle.world.snapshot().eventSequence() == 1, "4096 staged updates remain unpublished");
        fails(bundle.world, () -> bundle.world.updateBlock(bundle.next(), 0, 0, 0, 0));
        Harness sample = new Harness(); List<ChunkWorld.BlockPos> positions = new ArrayList<>();
        for (int i = 0; i < 64; i++) positions.add(new ChunkWorld.BlockPos(i, 0, 0));
        check(sample.world.sample(positions).blocks().size() == 64, "sample accepts at most 64 immutable value results");
        positions.add(new ChunkWorld.BlockPos(64, 0, 0)); fails(sample.world, () -> sample.world.sample(positions));
        Harness nested = new Harness(); nested.world.beginBundle(); fails(nested.world, nested.world::beginBundle);
    }
    private static void patchWorkingStorageBudget() {
        Harness h = new Harness(); h.bind(0, 4096); int[][] tall = sections(256, 0);
        for (int i = 0; i < 7; i++) h.world.replaceChunk(h.next(), i, 0, tall);
        h.world.beginBundle();
        for (int y = 0; y < 255; y++) h.world.updateBlock(h.next(), 0, y * 16, 0, 1);
        // One final section would fit, but its private patch copies also consume owned raw-array bytes.
        fails(h.world, () -> h.world.updateSection(h.next(), 0, 255, 0, new int[]{0}, new int[]{2}));
    }
    private static void fails(ChunkWorld world, Runnable operation) {
        try { operation.run(); throw new AssertionError("invalid operation succeeded"); }
        catch (ChunkWorld.Failure expected) {
            check(expected.code() != null && world.snapshot().closed() && !world.snapshot().available(), "failure must clear and latch the model");
            check(world.snapshot().loadedChunks() == 0 && world.snapshot().stateBytes() == 0 && state(world, 0, 0, 0) == null, "failed state must not leak published or staged chunks");
        }
    }
    private static void check(boolean condition, String message) { checks++; if (!condition) throw new AssertionError(message); }
}
