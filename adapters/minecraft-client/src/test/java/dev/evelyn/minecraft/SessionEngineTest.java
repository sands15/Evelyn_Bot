package dev.evelyn.minecraft;

import com.google.gson.JsonObject;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.concurrent.atomic.AtomicLong;

/** Deterministic effects, clock and output sink around the real admission/tick state machine. */
public final class SessionEngineTest {
    private static int checks;
    public static void main(String[] args) throws Exception {
        if (args.length == 1) {
            switch (args[0]) {
                case "world" -> worldReceiptsCannotChangeOwnership();
                case "executor" -> executorReturnFences();
                case "capabilities" -> capabilityBoundary();
                default -> throw new IllegalArgumentException("unknown_test_case");
            }
            System.out.println("SESSION_CASE_PASS " + checks); return;
        }
        try {
            normalAndDuplicate(); cancelBeforeStart(); freshnessAndDeadlines();
            retentionAndGap(); disconnectAndWorld(); failedStop(); executorFailureSingleResult(); queueAndThreadBounds();
            executorReturnFences(); worldReceiptsCannotChangeOwnership(); capabilityBoundary(); observerMode(); typedObservations(); wrappedObservations();
        } catch (UnsupportedOperationException missing) {
            throw new AssertionError("tick state machine is not implemented", missing);
        }
        System.out.println("SESSION_PASS " + checks);
    }
    static final class Effects implements DirectClient.Executor {
        int starts, stops, polls; boolean stopFails, startFails, pollFails; String result;
        Runnable onStart = () -> { }; Runnable onPoll = () -> { };
        public void start(String action) { starts++; onStart.run(); if (startFails) throw new IllegalStateException("synthetic"); }
        public String poll() { polls++; onPoll.run(); if (pollFails) throw new IllegalStateException("synthetic"); return result; }
        public void stop() { stops++; if (stopFails) throw new IllegalStateException("synthetic"); }
    }
    static final class Harness {
        final AtomicLong now = new AtomicLong(1_000_000_000L);
        final Effects effects = new Effects();
        final List<JsonObject> output = new ArrayList<>();
        final SessionEngine engine = new SessionEngine(Set.of("probe", "hold"), effects, now::get, output::add);
        Harness() {
            engine.setWorld("w1"); engine.observe("ready"); connect(); output.clear();
        }
        void connect() { engine.transportOpened(); send(base("welcome")); engine.tick(); }
        JsonObject base(String type) {
            JsonObject hello = engine.hello(); JsonObject value = new JsonObject();
            value.addProperty("v", 1); value.addProperty("type", type);
            value.add("session", hello.get("session")); value.add("world", hello.get("world")); return value;
        }
        JsonObject command(int seq, String action) {
            JsonObject value = base("command"); value.addProperty("request_id", "request-" + seq);
            value.addProperty("seq", seq); value.addProperty("action", action);
            value.addProperty("observation_seq", engine.hello().getAsJsonObject("observation").get("seq").getAsInt());
            value.addProperty("max_age_ms", 1000); value.addProperty("run_ms", 1000); return value;
        }
        JsonObject request(String type, int seq, String id) {
            JsonObject value = base(type); value.addProperty("seq", seq); value.addProperty("request_id", id); return value;
        }
        void send(JsonObject value) { check(engine.offer(Protocol.decode(value.toString())), "mailbox unexpectedly full"); }
        JsonObject response(String id) {
            return output.stream().filter(x -> x.has("request_id") && !x.get("request_id").isJsonNull() && x.get("request_id").getAsString().equals(id))
                .reduce((a, b) -> b).orElseThrow(() -> new AssertionError("missing response " + id));
        }
        String status(String id) { return response(id).get("status").getAsString(); }
        String code(String id) { return response(id).get("code").getAsString(); }
    }
    static void normalAndDuplicate() {
        Harness h = new Harness(); JsonObject command = h.command(1, "probe"); h.send(command); h.engine.tick();
        check(h.effects.starts == 1, "first command must execute once"); check(h.status("request-1").equals("accepted"), "admission response must remain accepted");
        h.effects.result = "succeeded"; h.engine.tick();
        command.addProperty("request_id", "duplicate"); h.send(command); h.engine.tick();
        check(h.status("duplicate").equals("succeeded") && h.effects.starts == 1, "same payload duplicate must return outcome without effect");
        command.addProperty("action", "hold"); command.addProperty("request_id", "conflict"); h.send(command); h.engine.tick();
        check(h.code("conflict").equals("sequence_conflict"), "changed duplicate must conflict");
        h.send(h.request("query", 1, "query-a")); h.engine.tick(); h.send(h.request("query", 1, "query-b")); h.engine.tick();
        check(h.status("query-a").equals("succeeded") && h.status("query-b").equals("succeeded"), "queries must not consume receipts");
    }
    static void cancelBeforeStart() {
        Harness h = new Harness(); h.send(h.command(1, "hold")); h.send(h.request("cancel", 1, "stop")); h.engine.tick();
        check(h.effects.starts == 0 && h.effects.stops == 0, "cancel queued command before any start");
        check(h.status("stop").equals("cancelled"), "cancel response must identify terminal result");
        h.send(h.command(2, "hold")); h.engine.tick(); h.send(h.request("cancel", 2, "running-stop")); h.engine.tick();
        check(h.effects.starts == 1 && h.effects.stops == 1 && h.status("running-stop").equals("cancelled"), "running cancellation must stop exactly once");
    }
    static void freshnessAndDeadlines() {
        Harness h = new Harness(); JsonObject stale = h.command(1, "probe"); h.now.addAndGet(1_001_000_000L); h.send(stale); h.engine.tick();
        check(h.effects.starts == 0 && h.code("request-1").equals("observation_stale"), "receipt-time cannot refresh source age");
        h.engine.observe("ready"); h.send(h.command(2, "hold")); h.engine.tick(); h.now.addAndGet(1_001_000_000L); h.engine.tick();
        check(h.effects.stops == 1 && h.engine.hello().get("active").isJsonNull(), "runtime monotonic deadline must stop active effect");
    }
    static void retentionAndGap() {
        Harness h = new Harness(); h.send(h.command(2, "probe")); h.engine.tick();
        check(h.code("request-2").equals("sequence_gap") && h.engine.hello().get("next_seq").getAsInt() == 1, "gap cannot allocate a sequence");
        JsonObject first = h.command(1, "unsupported");
        for (int seq = 1; seq <= 70; seq++) { h.send(h.command(seq, "unsupported")); h.engine.tick(); }
        check(h.engine.hello().getAsJsonArray("receipts").size() == 64, "receipt retention must be bounded");
        first.addProperty("request_id", "expired"); h.send(first); h.engine.tick();
        check(h.code("expired").equals("receipt_expired") && h.effects.starts == 0, "evicted sequence must never execute again");
    }
    static void disconnectAndWorld() {
        Harness h = new Harness(); JsonObject command = h.command(1, "hold"); h.send(command); h.engine.tick();
        h.engine.transportLost(); check(h.effects.stops == 0, "network callback cannot call game executor");
        h.engine.tick(); check(h.effects.stops == 1, "tick must honor disconnect latch"); h.connect();
        command.addProperty("request_id", "replay"); h.send(command); h.engine.tick();
        check(h.effects.starts == 1 && h.status("replay").equals("cancelled"), "reconnect cannot replay old command");
        h.send(h.command(2, "hold")); h.engine.tick(); h.engine.setWorld("w2");
        check(h.effects.stops == 2 && h.engine.hello().get("observation").isJsonNull(), "world change invalidates active action and observation");
        h.engine.observe("ready"); h.connect(); h.send(command); h.engine.tick();
        check(h.code("replay").equals("stale_binding"), "old world requests cannot match current receipts");
    }
    static void failedStop() {
        Harness h = new Harness(); h.effects.stopFails = true; h.send(h.command(1, "hold")); h.engine.tick();
        h.send(h.request("cancel", 1, "bad-stop")); h.engine.tick();
        check(h.status("bad-stop").equals("unknown"), "failed cleanup cannot claim cancellation");
        h.send(h.command(2, "probe")); h.engine.tick();
        check(h.effects.starts == 1 && h.code("request-2").equals("executor_blocked"), "cleanup failure permanently fences further effects");
    }
    static void executorFailureSingleResult() {
        for (boolean failStart : new boolean[]{true, false}) {
            Harness h = new Harness(); h.effects.startFails = failStart; h.effects.pollFails = !failStart;
            h.send(h.command(1, "hold")); h.engine.tick();
            long terminals = h.output.stream().filter(x -> x.has("status") && Set.of("failed", "cancelled", "unknown", "succeeded").contains(x.get("status").getAsString())).count();
            check(terminals == 1, "executor error must emit one definitive terminal result");
            check(h.effects.stops == 1 && h.engine.hello().getAsJsonArray("receipts").get(0).getAsJsonObject().get("status").getAsString().equals("failed"), "executor error must clean up before failure");
        }
    }
    static void queueAndThreadBounds() throws Exception {
        Harness h = new Harness();
        for (int i = 0; i < 64; i++) check(h.engine.offer(h.request("query", 1, "q" + i)), "bounded inbox must accept capacity");
        check(!h.engine.offer(h.request("query", 1, "overflow")), "inbox cannot grow unbounded");
        final Throwable[] failure = new Throwable[1];
        Thread thread = new Thread(() -> { try { h.engine.observe("wrong-thread"); } catch (Throwable bad) { failure[0] = bad; } });
        thread.start(); thread.join(); check(failure[0] instanceof IllegalStateException, "off-thread observation must be rejected");
    }
    static void capabilityBoundary() {
        Set<String> supported = new java.util.HashSet<>();
        for (int i = 0; i < 16; i++) supported.add("action-" + i);
        SessionEngine accepted = new SessionEngine(supported, new Effects(), System::nanoTime, output -> { });
        check(accepted.hello().getAsJsonArray("capabilities").size() == 16, "shared maximum capability set must be usable");
        supported.add("action-16");
        try { new SessionEngine(supported, new Effects(), System::nanoTime, output -> { }); }
        catch (IllegalArgumentException expected) { checks++; return; }
        throw new AssertionError("capability set exceeds Python wire limit");
    }
    static void executorReturnFences() {
        for (String boundary : new String[]{"deadline", "disconnect", "cancel", "world"}) {
            for (boolean duringStart : new boolean[]{false, true}) {
                Harness h = new Harness(); h.effects.result = "succeeded";
                Runnable change = switch (boundary) {
                    case "deadline" -> () -> h.now.addAndGet(2_000_000_000L);
                    case "disconnect" -> h.engine::transportLost;
                    case "cancel" -> () -> h.send(h.request("cancel", 1, "interleaved-cancel"));
                    case "world" -> () -> h.engine.setWorld("w2");
                    default -> throw new AssertionError("unknown test boundary");
                };
                if (duringStart) h.effects.onStart = change; else h.effects.onPoll = change;
                h.send(h.command(1, "hold")); h.engine.tick();
                check(h.effects.stops == 1, boundary + " during executor " + (duringStart ? "start" : "poll") + " must stop before publishing completion");
                check(h.effects.polls == (duringStart ? 0 : 1), "no poll is allowed after start crossed a stop fence");
                check(h.output.stream().noneMatch(x -> x.has("status") && x.get("status").getAsString().equals("succeeded")), "late result must not overrule " + boundary);
                h.engine.tick(); check(h.effects.stops == 1, "cleanup is not repeated after already confirmed stop");
            }
        }
    }
    static void worldReceiptsCannotChangeOwnership() {
        Harness h = new Harness(); h.effects.result = "succeeded"; h.send(h.command(1, "probe")); h.engine.tick();
        check(h.effects.starts == 1 && h.engine.hello().getAsJsonArray("receipts").size() == 1, "old world must have a real completed command");
        h.engine.setWorld("w2"); h.engine.observe("ready"); h.connect();
        check(h.engine.hello().get("next_seq").getAsInt() == 2, "world change must preserve sequence highwater");
        check(h.engine.hello().getAsJsonArray("receipts").isEmpty(), "old-world result must not be projected under new-world hello");
        h.send(h.request("query", 1, "new-world-query")); h.engine.tick();
        check(h.status("new-world-query").equals("unknown"), "new world query cannot claim old world success");
        h.send(h.request("cancel", 1, "new-world-cancel")); h.engine.tick();
        check(h.status("new-world-cancel").equals("unknown"), "new world cancel cannot claim old world result");
        h.send(h.command(1, "probe")); h.engine.tick();
        check(h.status("request-1").equals("unknown") && h.effects.starts == 1, "same sequence in new world cannot repeat or inherit effect");
    }
    static void check(boolean value, String message) { checks++; if (!value) throw new AssertionError(message); }

    static void observerMode() throws Exception {
        java.lang.reflect.Constructor<SessionEngine> constructor;
        try { constructor = SessionEngine.class.getDeclaredConstructor(java.util.function.LongSupplier.class, java.util.function.Consumer.class); }
        catch (NoSuchMethodException missing) { throw new AssertionError("read-only session mode is missing", missing); }
        List<JsonObject> output = new ArrayList<>();
        SessionEngine engine = constructor.newInstance((java.util.function.LongSupplier) System::nanoTime, (java.util.function.Consumer<JsonObject>) output::add);
        JsonObject hello = engine.hello();
        check(hello.get("v").getAsInt() == 2 && hello.getAsJsonArray("capabilities").isEmpty(), "observer must advertise only v2 and no actions");
        check(hello.get("active").isJsonNull() && hello.getAsJsonArray("receipts").isEmpty(), "observer cannot inherit action state");
        for (String type : new String[]{"command", "query", "cancel"}) {
            JsonObject request = new JsonObject(); request.addProperty("v", 2); request.addProperty("type", type);
            request.add("session", hello.get("session")); request.add("world", hello.get("world"));
            check(!engine.offer(request), "engine defense must reject " + type + " before field access");
        }
        try { engine.observe("json-in-a-string"); throw new AssertionError("v2 accepted v1 string observation"); }
        catch (IllegalArgumentException expected) { checks++; }
        try { engine.setWorld("invented-world"); throw new AssertionError("v2 accepted caller-invented binding"); }
        catch (IllegalArgumentException expected) { checks++; }
    }

    static void typedObservations() throws Exception {
        java.lang.reflect.Method observe;
        try { observe = SessionEngine.class.getDeclaredMethod("observe", PlayerObservation.Snapshot.class, long.class); }
        catch (NoSuchMethodException missing) { throw new AssertionError("typed observer publication is missing", missing); }
        long origin = -9_000_000_000L;
        PlayerObservation model = new PlayerObservation(java.util.UUID.fromString("00000000-0000-0000-0000-000000000001"), origin);
        List<JsonObject> output = new ArrayList<>();
        SessionEngine engine = new SessionEngine(System::nanoTime, output::add);
        observe.invoke(engine, model.snapshot(), origin);
        JsonObject first = output.getLast();
        check(first.get("type").getAsString().equals("hello") && first.get("world").getAsString().endsWith(":1"), "initial typed binding must publish atomically with hello");
        JsonObject observation = first.getAsJsonObject("observation");
        check(observation.get("observed_at_ms").getAsLong() == 0 && !observation.get("available").getAsBoolean(), "signed origin must yield zero elapsed and unavailable initial state");
        check(observation.keySet().equals(Set.of("seq", "connection_id", "generation", "event_seq", "observed_at_ms", "source", "available", "dimension", "position", "health", "world_ready", "coverage")), "v2 observation has an exact typed schema");
        check(observation.get("position").isJsonNull() && observation.get("health").isJsonNull() && observation.get("dimension").isJsonNull(), "initial unknown values must remain explicit null");
        int count = output.size(); observe.invoke(engine, model.snapshot(), origin);
        check(output.size() == count, "unchanged publication must not refresh timestamps or send another frame");
        model.player(1, origin + 1_000_000L, "minecraft:overworld");
        model.position(2, origin + 2_000_000L, 1, 2, 3, 4, 5);
        model.health(3, origin + 3_000_000L, 20, 25, 4);
        observe.invoke(engine, model.snapshot(), origin);
        JsonObject current = output.getLast().getAsJsonObject("observation");
        check(output.getLast().get("type").getAsString().equals("observe") && current.get("seq").getAsInt() == 4, "same world publication preserves model sequence");
        check(current.getAsJsonObject("position").get("source").getAsString().equals("server_correction") && current.get("source").getAsString().equals("server_observed"), "position evidence must not claim continuous server movement confirmation");
        check(current.get("observed_at_ms").getAsLong() == 3 && !current.get("world_ready").getAsBoolean(), "source time and unknown world coverage must be preserved");
        check(current.getAsJsonObject("health").get("food").getAsInt() == 25, "wire food bound must not invent a 20-point cap");
        PlayerObservation.Snapshot previous = model.snapshot();
        rejectsObserve(observe, engine, new PlayerObservation.Snapshot(previous.connectionId(), previous.generation(), previous.sequence(), previous.eventSequence(), previous.observedAtNanos() + 1, previous.available(), previous.dimension(), previous.position(), previous.health()), origin);
        rejectsObserve(observe, engine, new PlayerObservation.Snapshot(previous.connectionId(), previous.generation(), previous.sequence() + 1, previous.eventSequence() - 1, previous.observedAtNanos(), previous.available(), previous.dimension(), previous.position(), previous.health()), origin);
        rejectsObserve(observe, engine, new PlayerObservation.Snapshot(previous.connectionId(), previous.generation(), previous.sequence() + 1, previous.eventSequence(), previous.observedAtNanos() - 1, previous.available(), previous.dimension(), previous.position(), previous.health()), origin);
        model.invalidate(4, origin + 4_000_000L); observe.invoke(engine, model.snapshot(), origin);
        check(output.getLast().get("type").getAsString().equals("hello") && output.getLast().get("world").getAsString().endsWith(":2"), "new generation must travel with its own unavailable snapshot in one hello");
        check(output.getLast().getAsJsonObject("observation").get("position").isJsonNull(), "new generation cannot inherit old coordinates");
        rejectsObserve(observe, engine, previous, origin);
        rejectsObserve(observe, engine, model.snapshot(), origin + 1);
        PlayerObservation other = new PlayerObservation(java.util.UUID.randomUUID(), origin);
        rejectsObserve(observe, engine, other.snapshot(), origin);
        engine.transportLost();
        check(engine.hello().get("observation").isJsonNull(), "network loss invalidates retained observation before an owner tick");
        rejectsObserve(observe, engine, model.snapshot(), origin);
        SessionEngine overflow = new SessionEngine(System::nanoTime, output::add);
        rejectsObserve(observe, overflow, new PlayerObservation(java.util.UUID.randomUUID(), Long.MAX_VALUE).snapshot(), Long.MIN_VALUE);
        rejectsObserve(observe, new Harness().engine, previous, origin);
    }

    static void rejectsObserve(java.lang.reflect.Method observe, SessionEngine engine, PlayerObservation.Snapshot snapshot, long origin) throws Exception {
        try { observe.invoke(engine, snapshot, origin); }
        catch (java.lang.reflect.InvocationTargetException expected) {
            if (!(expected.getCause() instanceof IllegalArgumentException || expected.getCause() instanceof IllegalStateException)) throw expected;
            checks++; return;
        }
        throw new AssertionError("invalid or stopped observation was accepted");
    }

    static void wrappedObservations() throws Exception {
        long origin = Long.MAX_VALUE - 2_000_000L;
        PlayerObservation model = new PlayerObservation(java.util.UUID.randomUUID(), origin);
        List<JsonObject> output = new ArrayList<>();
        SessionEngine engine = new SessionEngine(System::nanoTime, output::add);
        engine.observe(model.snapshot(), origin);
        model.player(1, origin + 1_000_000L, "minecraft:overworld"); engine.observe(model.snapshot(), origin);
        check(output.getLast().getAsJsonObject("observation").get("observed_at_ms").getAsLong() == 1, "pre-wrap elapsed time must be measured from the connection origin");
        model.position(2, origin + 4_000_000L, 1, 2, 3, 4, 5);
        PlayerObservation.Snapshot wrapped = model.snapshot();
        check(wrapped.observedAtNanos() < 0, "fixture must cross the signed nanoTime boundary");
        check(Protocol.observation(wrapped, origin).get("observed_at_ms").getAsLong() == 4, "wire elapsed time must accept a short forward interval across signed wrap");
        engine.observe(wrapped, origin);
        check(output.getLast().getAsJsonObject("observation").get("seq").getAsInt() == 3, "forward wrapped publication must advance normally");
        java.lang.reflect.Method observe = SessionEngine.class.getDeclaredMethod("observe", PlayerObservation.Snapshot.class, long.class);
        PlayerObservation.Snapshot reversed = new PlayerObservation.Snapshot(wrapped.connectionId(), 1, 4, 3,
                origin + 1_500_000L, true, wrapped.dimension(), wrapped.position(), wrapped.health());
        rejectsObserve(observe, engine, reversed, origin);
        check(engine.hello().getAsJsonObject("observation").get("seq").getAsInt() == 3, "rejected cross-wrap regression must preserve the published snapshot");
        model.invalidate(3, origin + 5_000_000L); engine.observe(model.snapshot(), origin);
        check(output.getLast().get("type").getAsString().equals("hello")
                && output.getLast().getAsJsonObject("observation").get("observed_at_ms").getAsLong() == 5,
                "generation invalidation after wrap preserves increasing elapsed wire time");
    }
}
