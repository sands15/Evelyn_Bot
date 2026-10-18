package dev.evelyn.minecraft;

import java.io.InputStreamReader;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

/** Synthetic v2 loopback host. It never connects a game socket or invokes authentication. */
public final class ObserverFixture {
    public static void main(String[] args) {
        boolean passed;
        try { passed = run(); }
        catch (Exception | AssertionError failure) { passed = false; }
        if (!passed) { marker("OBSERVER_FAILED"); System.exit(2); }
    }

    private static boolean run() throws Exception {
        String endpoint = System.getenv("EVELYN_TEST_ENDPOINT");
        String token = System.getenv("EVELYN_TEST_TOKEN");
        String setting = System.getenv("EVELYN_TEST_DURATION_MS");
        String terrainSetting = System.getenv("EVELYN_TEST_TERRAIN");
        boolean terrainMode = "1".equals(terrainSetting);
        long duration = setting == null ? 25000 : Long.parseLong(setting);
        if (endpoint == null || token == null || duration < 100 || duration > 25000
                || (terrainSetting != null && !terrainMode))
            throw new IllegalArgumentException("invalid_fixture_setting");

        AtomicBoolean disconnected = new AtomicBoolean();
        AtomicBoolean connectionFailed = new AtomicBoolean();
        AtomicBoolean invalidInput = new AtomicBoolean();
        AtomicInteger callbacks = new AtomicInteger();
        ArrayBlockingQueue<String> controls = new ArrayBlockingQueue<>(8);
        VanillaConnection owned = new VanillaConnection(new UUID(0, 1), "Synthetic", hash -> {
            throw new AssertionError("authentication_must_not_run");
        });
        long origin = System.nanoTime();
        PlayerObservation model = new PlayerObservation(UUID.randomUUID(), origin);
        ChunkWorld terrain = terrainMode ? new ChunkWorld(model.snapshot().connectionId(), origin) : null;
        int event = 0;
        long now = System.nanoTime();
        model.player(++event, now, "evelyn:synthetic");
        if (terrain != null) terrain.bind(new ChunkWorld.Event(1, event, now), "evelyn:synthetic", -64, 256);
        model.position(++event, System.nanoTime(), 1, 64, 2, 90, 0);
        model.health(++event, System.nanoTime(), 20, 20, 5);
        if (terrain != null) {
            now = System.nanoTime();
            terrain.replaceChunk(new ChunkWorld.Event(1, ++event, now), -1, -1, new int[16][4096]);
            model.touch(event, now);
        }

        Thread input = new Thread(() -> readControls(controls, invalidInput), "synthetic-observer-input");
        input.setDaemon(true);
        DirectClient client = null;
        boolean readySeen = false, quit = false, failed = false, terrainBundle = false;
        try {
            client = DirectClient.observer(URI.create(endpoint), token, () -> {
                callbacks.incrementAndGet();
                disconnected.set(true);
                owned.close();
            });
            publish(client, model, terrain, origin);
            client.connect().exceptionally(error -> { connectionFailed.set(true); return null; });
            input.start();
            while (!quit && !disconnected.get()
                    && System.nanoTime() - origin < TimeUnit.MILLISECONDS.toNanos(duration)) {
                if (invalidInput.get()) { failed = true; break; }
                client.tick();
                if (!readySeen && client.ready()) { readySeen = true; marker("READY"); }
                String command;
                while (!disconnected.get() && (command = controls.poll()) != null) {
                    switch (command) {
                        case "health" -> {
                            if (!model.snapshot().available()) throw new IllegalStateException("unbound_synthetic_player");
                            float value = model.snapshot().health().health() == 20 ? 19 : 20;
                            model.health(++event, System.nanoTime(), value, 20, 5);
                            publish(client, model, terrain, origin);
                            marker("UPDATE");
                        }
                        case "terrain" -> {
                            requireTerrain(terrain);
                            now = System.nanoTime();
                            terrain.updateBlock(new ChunkWorld.Event(model.snapshot().generation(), ++event, now), -1, 64, -1, 17);
                            model.touch(event, now);
                            publish(client, model, terrain, origin);
                            marker("UPDATE");
                        }
                        case "bundle_start" -> {
                            requireTerrain(terrain);
                            if (terrainBundle) throw new IllegalStateException("synthetic_bundle_already_open");
                            terrain.beginBundle(); model.beginBundle(); terrainBundle = true;
                            now = System.nanoTime();
                            terrain.updateBlock(new ChunkWorld.Event(model.snapshot().generation(), ++event, now), -1, 64, -1, 23);
                            model.touch(event, now);
                            model.health(++event, System.nanoTime(), 18, 20, 5);
                            // Deliberately attempt publication while both models still expose their previous snapshot.
                            publish(client, model, terrain, origin);
                            marker("BUNDLE_STAGED");
                        }
                        case "bundle_end" -> {
                            requireTerrain(terrain);
                            if (!terrainBundle) throw new IllegalStateException("synthetic_bundle_not_open");
                            terrain.endBundle(); model.endBundle(); terrainBundle = false;
                            publish(client, model, terrain, origin);
                            marker("UPDATE");
                        }
                        case "unload" -> {
                            requireTerrain(terrain);
                            now = System.nanoTime();
                            terrain.unload(new ChunkWorld.Event(model.snapshot().generation(), ++event, now), -1, -1);
                            model.touch(event, now);
                            publish(client, model, terrain, origin);
                            marker("UPDATE");
                        }
                        case "world" -> {
                            now = System.nanoTime();
                            model.invalidate(++event, now);
                            if (terrain != null) terrain.invalidate(new ChunkWorld.Event(model.snapshot().generation(), event, now));
                            terrainBundle = false;
                            publish(client, model, terrain, origin);
                            marker("UPDATE");
                        }
                        case "quit" -> quit = true;
                        default -> throw new IllegalArgumentException("invalid_fixture_control");
                    }
                    if (quit) break;
                }
                if (!quit && !disconnected.get()) Thread.sleep(20);
            }
            if (!quit && !disconnected.get()) failed = true;
        } catch (IllegalStateException failure) {
            // A peer close can fence an observation between the owner check and publication.
            if (!"observer_stopped".equals(failure.getMessage())) failed = true;
        } catch (Exception | AssertionError failure) {
            failed = true;
        } finally {
            if (client != null) { client.close(); client.tick(); }
            owned.close();
            if (terrain != null) {
                now = System.nanoTime();
                model.invalidate(++event, now);
                terrain.close(new ChunkWorld.Event(model.snapshot().generation(), event, now));
            }
        }
        boolean closed = callbacks.get() == 1 && disconnected.get()
                && owned.snapshot().stage() == VanillaConnection.Stage.CLOSED
                && !owned.observation().available()
                && (terrain == null || (terrain.snapshot().closed() && terrain.snapshot().loadedChunks() == 0
                                        && terrain.sample(points()).blocks().stream().allMatch(block -> block.stateId() == null)));
        if (closed) marker("CLOSED");
        if (failed || connectionFailed.get() || !readySeen || !closed) return false;
        marker("OBSERVER_CLOSED");
        return true;
    }

    private static void readControls(ArrayBlockingQueue<String> controls, AtomicBoolean invalid) {
        try {
            InputStreamReader reader = new InputStreamReader(System.in, StandardCharsets.UTF_8);
            StringBuilder line = new StringBuilder();
            int character;
            while ((character = reader.read()) != -1) {
                if (character == '\r') continue;
                if (character == '\n') {
                    String command = line.toString();
                    if (!(command.equals("health") || command.equals("world") || command.equals("quit")
                            || command.equals("terrain") || command.equals("bundle_start")
                            || command.equals("bundle_end") || command.equals("unload"))
                            || !controls.offer(command)) { invalid.set(true); return; }
                    line.setLength(0);
                } else {
                    if (line.length() == 16) { invalid.set(true); return; }
                    line.append((char) character);
                }
            }
            if (!line.isEmpty()) invalid.set(true);
        } catch (Exception failure) { invalid.set(true); }
    }

    private static List<ChunkWorld.BlockPos> points() {
        return List.of(new ChunkWorld.BlockPos(-1, 64, -1), new ChunkWorld.BlockPos(-17, 64, -1),
                       new ChunkWorld.BlockPos(-1, -65, -1));
    }

    private static void requireTerrain(ChunkWorld terrain) {
        if (terrain == null) throw new IllegalStateException("terrain_fixture_not_enabled");
    }

    private static void publish(DirectClient client, PlayerObservation model, ChunkWorld terrain, long origin) {
        PlayerObservation.Snapshot self = model.snapshot();
        if (terrain != null && self.available()) {
            self = new PlayerObservation.Snapshot(self.connectionId(), self.generation(), self.sequence(), self.eventSequence(),
                    self.observedAtNanos(), true, self.dimension(), self.position(), self.health(), terrain.sample(points()));
        }
        client.observe(self, origin);
    }

    private static void marker(String value) { System.out.println(value); System.out.flush(); }
}
