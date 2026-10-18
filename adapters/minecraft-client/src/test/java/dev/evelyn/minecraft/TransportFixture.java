package dev.evelyn.minecraft;

import com.google.gson.JsonObject;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.Set;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

/** Synthetic host only. No Fabric/Minecraft/runtime configuration is loaded. */
public final class TransportFixture {
    private static final class Effects implements DirectClient.Executor {
        int starts, stops, probes;
        boolean failStop;
        String action;
        public void start(String action) { this.action = action; starts++; if (action.equals("probe")) probes++; }
        public String poll() { return "probe".equals(action) ? "succeeded" : null; }
        public void stop() { stops++; action = null; if (failStop) throw new IllegalStateException("synthetic_stop_failure"); }
    }

    public static void main(String[] args) throws Exception {
        String endpoint = System.getenv("EVELYN_TEST_ENDPOINT");
        String token = System.getenv("EVELYN_TEST_TOKEN");
        long duration = setting("EVELYN_TEST_DURATION_MS", 15000, 100, 120000);
        long tickMillis = setting("EVELYN_TEST_TICK_MS", 20, 5, 1000);
        Effects effects = new Effects(); AtomicInteger connectionFailures = new AtomicInteger();
        ArrayBlockingQueue<String> controls = new ArrayBlockingQueue<>(32);
        Thread input = new Thread(() -> {
            try {
                BufferedReader reader = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
                String line;
                while ((line = reader.readLine()) != null) {
                    if (line.length() > 160 || !controls.offer(line)) break;
                }
            } catch (Exception ignored) { /* Test control EOF is not game input. */ }
        }, "synthetic-fixture-input");
        input.setDaemon(true); input.start();
        DirectClient client = null; int ticks = 0; int fixtureErrors = 0;
        try {
            client = new DirectClient(URI.create(endpoint), token, Set.of("probe", "hold"), effects);
            client.setWorld("synthetic-world"); client.observe("ready");
            client.connect().exceptionally(error -> { connectionFailures.incrementAndGet(); return null; });
            long start = System.nanoTime(); long lastObservation = start;
            boolean paused = false; boolean quit = false;
            while (!quit && System.nanoTime() - start < TimeUnit.MILLISECONDS.toNanos(duration)) {
                String command;
                while ((command = controls.poll()) != null) {
                    switch (command) {
                        case "pause" -> paused = true;
                        case "resume" -> paused = false;
                        case "disconnect" -> client.disconnect();
                        case "reconnect" -> {
                            try { client.connect().exceptionally(error -> { connectionFailures.incrementAndGet(); return null; }); }
                            catch (IllegalStateException unavailable) { connectionFailures.incrementAndGet(); }
                        }
                        case "fail-stop" -> effects.failStop = true;
                        case "quit" -> quit = true;
                        default -> {
                            if (command.startsWith("world:")) { client.setWorld(command.substring(6)); client.observe("ready"); }
                            else fixtureErrors++;
                        }
                    }
                }
                if (!paused) {
                    client.tick(); ticks++;
                    if (System.nanoTime() - lastObservation >= TimeUnit.MILLISECONDS.toNanos(200)) {
                        client.observe("ready"); lastObservation = System.nanoTime();
                    }
                }
                Thread.sleep(tickMillis);
            }
        } catch (Exception failure) { fixtureErrors++; }
        finally {
            if (client != null) { client.close(); client.tick(); }
            JsonObject summary = new JsonObject(); summary.addProperty("synthetic", true);
            summary.addProperty("starts", effects.starts); summary.addProperty("stops", effects.stops);
            summary.addProperty("probes", effects.probes); summary.addProperty("ticks", ticks);
            summary.addProperty("connection_failures", connectionFailures.get()); summary.addProperty("fixture_errors", fixtureErrors);
            System.out.println(summary);
        }
        if (fixtureErrors != 0) System.exit(2);
    }

    private static long setting(String name, long fallback, long min, long max) {
        String value = System.getenv(name);
        long result = value == null ? fallback : Long.parseLong(value);
        if (result < min || result > max) throw new IllegalArgumentException("invalid_fixture_setting");
        return result;
    }
}
