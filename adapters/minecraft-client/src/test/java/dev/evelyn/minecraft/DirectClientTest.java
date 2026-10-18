package dev.evelyn.minecraft;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.net.URI;
import java.net.http.WebSocket;
import java.nio.ByteBuffer;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

public final class DirectClientTest {
    public static void main(String[] args) throws Exception {
        if (args.length == 1 && args[0].equals("observer")) { observerLifecycle(); return; }
        if (args.length == 1 && args[0].equals("welcome")) { orderedObserverWelcomes(); observerHelloBounds(); return; }
        Constructor<?> constructor;
        try { constructor = DirectClient.class.getConstructor(URI.class, String.class, Set.class, DirectClient.Executor.class); }
        catch (ReflectiveOperationException missing) { throw new AssertionError("authenticated loopback client is not implemented", missing); }
        DirectClient.Executor noEffects = new DirectClient.Executor() {
            public void start(String action) { throw new AssertionError("constructor caused an effect"); }
            public String poll() { throw new AssertionError("constructor polled"); }
            public void stop() { throw new AssertionError("constructor stopped"); }
        };
        int checks = 0;
        for (String bad : new String[]{"ws://example.com:1234/minecraft", "http://127.0.0.1:1234/minecraft", "ws://localhost:1234/minecraft", "ws://127.0.0.1:1234/minecraft?token=x", "ws://user@127.0.0.1:1234/minecraft", "ws://127.0.0.1:1234/minecraft#x", "ws://127.0.0.1:1234/other"}) {
            try { constructor.newInstance(URI.create(bad), "a".repeat(32), Set.of("probe"), noEffects); }
            catch (InvocationTargetException expected) {
                if (!(expected.getCause() instanceof IllegalArgumentException)) throw expected;
                checks++; continue;
            }
            throw new AssertionError("unsafe endpoint admitted");
        }
        try { constructor.newInstance(URI.create("ws://127.0.0.1:1234/minecraft"), "too-short", Set.of("probe"), noEffects); throw new AssertionError("weak credential admitted"); }
        catch (InvocationTargetException expected) { if (!(expected.getCause() instanceof IllegalArgumentException)) throw expected; checks++; }
        for (String badToken : new String[]{"a".repeat(257), " " + "a".repeat(31), "a".repeat(31) + " ", "a".repeat(31) + "\t", "a".repeat(31) + "\u007f", "a".repeat(31) + "é"}) {
            Object unexpected;
            try { unexpected = constructor.newInstance(URI.create("ws://127.0.0.1:1234/minecraft"), badToken, Set.of("probe"), noEffects); }
            catch (InvocationTargetException expected) { if (!(expected.getCause() instanceof IllegalArgumentException)) throw expected; checks++; continue; }
            ((DirectClient) unexpected).close(); ((DirectClient) unexpected).tick();
            throw new AssertionError("credential outside shared wire contract admitted");
        }
        DirectClient longest = (DirectClient) constructor.newInstance(URI.create("ws://127.0.0.1:1234/minecraft"), "a".repeat(256), Set.of("probe"), noEffects);
        longest.close(); longest.tick(); checks++;
        Object client = constructor.newInstance(URI.create("ws://127.0.0.1:1234/minecraft"), "a".repeat(32), Set.of("probe"), noEffects);
        DirectClient.class.getMethod("close").invoke(client);
        DirectClient.class.getMethod("tick").invoke(client);
        System.out.println("CLIENT_CONFIG_PASS " + (checks + 1));
        stalledWriterBounds();
        deadlineStopsOnlyOnOwnerTick(false);
        deadlineStopsOnlyOnOwnerTick(true);
        System.out.println("CLIENT_WRITER_PASS " + writerChecks);
        observerLifecycle();
        orderedObserverWelcomes();
        observerHelloBounds();
    }

    private static void observerLifecycle() throws Exception {
        int initialChecks = writerChecks;
        Method factory;
        try { factory = DirectClient.class.getMethod("observer", URI.class, String.class, Runnable.class); }
        catch (NoSuchMethodException missing) { throw new AssertionError("observer transport factory is missing", missing); }
        java.util.concurrent.atomic.AtomicInteger callbacks = new java.util.concurrent.atomic.AtomicInteger();
        try { factory.invoke(null, URI.create("ws://example.com:1234/minecraft"), "a".repeat(32), (Runnable) callbacks::incrementAndGet); }
        catch (InvocationTargetException expected) { if (!(expected.getCause() instanceof IllegalArgumentException)) throw expected; }
        check(callbacks.get() == 1, "factory failure must close its host once");
        callbacks.set(0);
        DirectClient observer = (DirectClient) factory.invoke(null, URI.create("ws://127.0.0.1:1234/minecraft"), "a".repeat(32), (Runnable) callbacks::incrementAndGet);
        SessionEngine engine = (SessionEngine) get(observer, "engine");
        check(engine.hello().get("v").getAsInt() == 2, "observer factory must select v2");
        Method ready;
        try { ready = DirectClient.class.getMethod("ready"); }
        catch (NoSuchMethodException missing) { observer.close(); throw new AssertionError("transport readiness is missing", missing); }
        check(!(boolean) ready.invoke(observer), "new observer is not welcomed");
        PlayerObservation model = new PlayerObservation(java.util.UUID.randomUUID(), 0);
        observer.observe(model.snapshot(), 0);
        StalledSocket socket = new StalledSocket(); socket.subprotocol = "evelyn.minecraft.v2";
        WebSocket.Listener listener = listener(observer);
        listener.onOpen(socket);
        JsonObject welcome = new JsonObject(); welcome.addProperty("v", 2); welcome.addProperty("type", "welcome");
        welcome.add("session", engine.hello().get("session")); welcome.add("world", engine.hello().get("world"));
        listener.onText(socket, welcome.toString(), true);
        check((boolean) ready.invoke(observer), "valid matching v2 welcome establishes transport readiness");
        JsonObject forbidden = welcome.deepCopy(); forbidden.addProperty("type", "query");
        forbidden.addProperty("seq", 1); forbidden.addProperty("request_id", "forbidden");
        listener.onText(socket, forbidden.toString(), true);
        check(callbacks.get() == 1 && socket.aborts == 1, "forbidden v2 command type closes host and socket once");
        check(!(boolean) ready.invoke(observer), "failed observer cannot retain transport readiness");
        observer.disconnect(); observer.disconnect(); observer.close(); observer.tick();
        check(callbacks.get() == 1, "repeated failure/close must not repeat host shutdown");
        check(engine.hello().get("observation").isJsonNull(), "disconnect must remove retained observation immediately");
        try { observer.connect(); throw new AssertionError("stopped observer reconnected"); }
        catch (IllegalStateException expected) { writerChecks++; }
        check(callbacks.get() == 1, "late callback cannot repeat host close");
        callbacks.set(0);
        try (DirectClient mismatch = (DirectClient) factory.invoke(null, URI.create("ws://127.0.0.1:1234/minecraft"), "a".repeat(32), (Runnable) callbacks::incrementAndGet)) {
            StalledSocket wrongVersion = new StalledSocket();
            listener(mismatch).onOpen(wrongVersion);
            check(callbacks.get() == 1 && wrongVersion.aborts == 1 && wrongVersion.sent.isEmpty(), "v2 must reject negotiated v1 before sending any observation");
        }
        callbacks.set(0);
        try (DirectClient expired = (DirectClient) factory.invoke(null, URI.create("ws://127.0.0.1:1234/minecraft"), "a".repeat(32), (Runnable) callbacks::incrementAndGet)) {
            expired.observe(model.snapshot(), 0);
            StalledSocket waiting = new StalledSocket(); waiting.subprotocol = "evelyn.minecraft.v2";
            listener(expired).onOpen(waiting);
            set(expired, "handshakeStarted", System.nanoTime());
            set(expired, "sendStarted", System.nanoTime() - TimeUnit.SECONDS.toNanos(3));
            call(expired, "checkDeadlines");
            check(callbacks.get() == 1 && waiting.aborts == 1, "stalled v2 writer must close host without an owner tick");
            check(((SessionEngine) get(expired, "engine")).hello().get("observation").isJsonNull(), "timer expiry must immediately invalidate typed snapshot");
        }
        System.out.println("CLIENT_OBSERVER_PASS " + (writerChecks - initialChecks));
    }

    private static WebSocket.Listener listener(DirectClient client) throws Exception {
        Class<?> type = java.util.Arrays.stream(DirectClient.class.getDeclaredClasses()).filter(x -> x.getSimpleName().equals("Listener")).findFirst().orElseThrow();
        Constructor<?> constructor = type.getDeclaredConstructor(DirectClient.class, long.class); constructor.setAccessible(true);
        return (WebSocket.Listener) constructor.newInstance(client, (long) get(client, "generation"));
    }

    private static void orderedObserverWelcomes() throws Exception {
        int initialChecks = writerChecks;
        java.util.concurrent.atomic.AtomicInteger callbacks = new java.util.concurrent.atomic.AtomicInteger();
        try (DirectClient client = DirectClient.observer(URI.create("ws://127.0.0.1:1234/minecraft"), "a".repeat(32), callbacks::incrementAndGet)) {
            ((ScheduledExecutorService) get(client, "scheduler")).shutdownNow();
            PlayerObservation model = new PlayerObservation(java.util.UUID.randomUUID(), 0);
            client.observe(model.snapshot(), 0);
            SessionEngine engine = (SessionEngine) get(client, "engine");
            StalledSocket socket = new StalledSocket(); socket.subprotocol = "evelyn.minecraft.v2"; socket.completeImmediately = true;
            WebSocket.Listener listener = listener(client); listener.onOpen(socket);
            JsonObject first = welcome(engine.hello());
            model.invalidate(1, 1); client.observe(model.snapshot(), 0);
            JsonObject second = welcome(engine.hello());
            check(socket.sent.size() == 2, "fixture must send both ordered generation hellos");
            long lease = (long) get(client, "ownerHeartbeat"), handshake = (long) get(client, "handshakeStarted");
            long requests = socket.requests;
            listener.onText(socket, first.toString(), true);
            check(callbacks.get() == 0 && socket.aborts == 0, "prior issued welcome must not terminate the host");
            check(!client.ready() && (long) get(client, "ownerHeartbeat") == lease && (long) get(client, "handshakeStarted") == handshake,
                    "prior welcome must not ready the new generation or extend any deadline");
            check(socket.requests == requests + 1, "ignoring a stale welcome must still request the next inbound frame");
            listener.onText(socket, second.toString(), true);
            check(client.ready(), "latest generation welcome must establish readiness");
            lease = (long) get(client, "ownerHeartbeat");
            listener.onText(socket, first.toString(), true);
            check(client.ready() && (long) get(client, "ownerHeartbeat") == lease, "duplicate old welcome preserves current readiness without renewing lease");
            Object transportLock = get(client, "lock");
            Thread owner = Thread.currentThread();
            java.util.concurrent.CountDownLatch locked = new java.util.concurrent.CountDownLatch(1);
            java.util.concurrent.FutureTask<Void> interleavedAck = new java.util.concurrent.FutureTask<>(() -> {
                synchronized (transportLock) {
                    locked.countDown(); long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(2);
                    while (owner.getState() != Thread.State.BLOCKED) {
                        if (System.nanoTime() - deadline >= 0) throw new AssertionError("owner did not reach the publication boundary");
                        Thread.onSpinWait();
                    }
                    check(engine.hello().get("world").equals(second.get("world")), "typed publication and hello enqueue must share the transport's serial order");
                    listener.onText(socket, second.toString(), true);
                    check(client.ready() && callbacks.get() == 0, "ACK already holding the transport lock still belongs to the previously published world");
                }
                return null;
            });
            Thread race = new Thread(interleavedAck, "observer-welcome-fixture"); race.setDaemon(true); race.start();
            if (!locked.await(2, TimeUnit.SECONDS)) throw new AssertionError("fixture lock not acquired");
            model.invalidate(2, 2); client.observe(model.snapshot(), 0); interleavedAck.get(2, TimeUnit.SECONDS);
            check(!client.ready(), "new publication must await its own ACK after an earlier ACK completes");
            listener.onText(socket, welcome(engine.hello()).toString(), true);
            check(client.ready(), "current hello remains usable after the interleaved prior welcome");
            JsonObject unknown = second.deepCopy(); unknown.addProperty("world", "never-issued");
            listener.onText(socket, unknown.toString(), true);
            check(callbacks.get() == 1 && socket.aborts == 1, "unissued binding must still fail closed");
            check(((java.util.Map<?, ?>) get(client, "helloBindings")).isEmpty(), "disconnect must clear hello history");
        }
        System.out.println("CLIENT_WELCOME_PASS " + (writerChecks - initialChecks));
    }

    private static void observerHelloBounds() throws Exception {
        int initialChecks = writerChecks;
        for (boolean acknowledge : new boolean[]{false, true}) {
            java.util.concurrent.atomic.AtomicInteger callbacks = new java.util.concurrent.atomic.AtomicInteger();
            try (DirectClient client = DirectClient.observer(URI.create("ws://127.0.0.1:1234/minecraft"), "a".repeat(32), callbacks::incrementAndGet)) {
                ((ScheduledExecutorService) get(client, "scheduler")).shutdownNow();
                PlayerObservation model = new PlayerObservation(java.util.UUID.randomUUID(), 0); client.observe(model.snapshot(), 0);
                SessionEngine engine = (SessionEngine) get(client, "engine");
                StalledSocket socket = new StalledSocket(); socket.subprotocol = "evelyn.minecraft.v2"; socket.completeImmediately = true;
                WebSocket.Listener listener = listener(client); listener.onOpen(socket);
                JsonObject earliest = welcome(engine.hello());
                if (acknowledge) { listener.onText(socket, earliest.toString(), true); client.tick(); }
                int count = acknowledge ? 70 : 63;
                for (int i = 1; i <= count; i++) {
                    model.invalidate(i, i); client.observe(model.snapshot(), 0);
                    if (acknowledge) { listener.onText(socket, welcome(engine.hello()).toString(), true); client.tick(); }
                }
                check(callbacks.get() == 0 && ((java.util.Map<?, ?>) get(client, "helloBindings")).size() == 64,
                        "hello history must stay bounded while retaining every outstanding binding");
                if (acknowledge) {
                    check(client.ready(), "acknowledged history eviction cannot interrupt current readiness");
                    listener.onText(socket, earliest.toString(), true);
                    check(callbacks.get() == 1, "evicted history cannot authorize a claimed binding");
                } else {
                    model.invalidate(64, 64); client.observe(model.snapshot(), 0);
                    check(callbacks.get() == 1 && socket.aborts == 1, "65 outstanding distinct hellos must fail closed instead of forgetting pending authority");
                }
                check(((java.util.Map<?, ?>) get(client, "helloBindings")).isEmpty(), "hello bound failure clears all retained binding history");
            }
        }
        System.out.println("CLIENT_HELLO_BOUND_PASS " + (writerChecks - initialChecks));
    }

    private static JsonObject welcome(JsonObject hello) {
        JsonObject value = new JsonObject(); value.addProperty("v", 2); value.addProperty("type", "welcome");
        value.add("session", hello.get("session")); value.add("world", hello.get("world")); return value;
    }

    private static int writerChecks;
    private static void check(boolean condition, String message) {
        writerChecks++;
        if (!condition) throw new AssertionError(message);
    }

    /** Only the actual network send is replaced: admission, writer, queues and cleanup stay real. */
    private static final class StalledSocket implements WebSocket {
        final List<String> sent = new ArrayList<>();
        final List<CompletableFuture<WebSocket>> pending = new ArrayList<>();
        int maximumPending;
        int aborts;
        boolean closed;
        String subprotocol = "evelyn.minecraft.v1";
        boolean completeImmediately;
        long requests;
        public CompletableFuture<WebSocket> sendText(CharSequence data, boolean last) {
            if (closed) throw new AssertionError("writer sent after disconnect");
            if (!last) throw new AssertionError("bounded JSON must be a complete text message");
            sent.add(data.toString());
            if (completeImmediately) return CompletableFuture.completedFuture(this);
            CompletableFuture<WebSocket> future = new CompletableFuture<>(); pending.add(future);
            maximumPending = Math.max(maximumPending, (int) pending.stream().filter(x -> !x.isDone()).count());
            return future;
        }
        public CompletableFuture<WebSocket> sendBinary(ByteBuffer data, boolean last) { throw new AssertionError("binary output unexpected"); }
        public CompletableFuture<WebSocket> sendPing(ByteBuffer message) { return CompletableFuture.completedFuture(this); }
        public CompletableFuture<WebSocket> sendPong(ByteBuffer message) { return CompletableFuture.completedFuture(this); }
        public CompletableFuture<WebSocket> sendClose(int status, String reason) { abort(); return CompletableFuture.completedFuture(this); }
        public void request(long count) { requests += count; }
        public String getSubprotocol() { return subprotocol; }
        public boolean isOutputClosed() { return closed; }
        public boolean isInputClosed() { return closed; }
        public void abort() {
            aborts++; closed = true;
            for (CompletableFuture<WebSocket> future : List.copyOf(pending)) future.completeExceptionally(new java.io.IOException("synthetic_disconnect"));
        }
    }

    private static final class Effects implements DirectClient.Executor {
        int starts, stops;
        public void start(String action) { starts++; }
        public String poll() { return null; }
        public void stop() { stops++; }
    }

    private static final class WriterHarness implements AutoCloseable {
        final Effects effects = new Effects();
        final DirectClient client = new DirectClient(URI.create("ws://127.0.0.1:1234/minecraft"), "a".repeat(32), Set.of("hold"), effects);
        final StalledSocket socket = new StalledSocket();
        final SessionEngine engine;
        WriterHarness() throws Exception {
            // No server is started. Disable the timer to drive each specific deadline deterministically.
            ScheduledExecutorService scheduler = (ScheduledExecutorService) get(client, "scheduler");
            scheduler.shutdownNow();
            if (!scheduler.awaitTermination(2, TimeUnit.SECONDS)) throw new AssertionError("owned scheduler did not stop");
            engine = (SessionEngine) get(client, "engine");
            client.setWorld("synthetic-writer"); client.observe("ready");
            engine.transportOpened(); engine.offer(frame("welcome")); client.tick();
            set(client, "socket", socket); set(client, "welcomed", true); set(client, "ownerHeartbeat", System.nanoTime());
        }
        JsonObject frame(String type) {
            JsonObject hello = engine.hello(); JsonObject frame = new JsonObject();
            frame.addProperty("v", 1); frame.addProperty("type", type); frame.add("session", hello.get("session")); frame.add("world", hello.get("world"));
            return frame;
        }
        void startHold() {
            JsonObject command = frame("command"); command.addProperty("request_id", "hold-request"); command.addProperty("seq", 1);
            command.addProperty("action", "hold"); command.addProperty("observation_seq", engine.hello().getAsJsonObject("observation").get("seq").getAsInt());
            command.addProperty("max_age_ms", 2000); command.addProperty("run_ms", 30000);
            check(engine.offer(Protocol.decode(command.toString())), "synthetic command admission failed"); client.tick();
            check(effects.starts == 1, "test requires an actual running effect before timeout");
        }
        @Override public void close() { client.close(); client.tick(); }
    }

    private static void stalledWriterBounds() throws Exception {
        try (WriterHarness h = new WriterHarness()) {
            h.client.observe("first");
            for (int i = 0; i < 100; i++) h.client.observe("state-" + i);
            check(h.socket.sent.size() == 1 && h.socket.maximumPending == 1, "writer cannot start a second send while one is outstanding");
            Object latest = get(h.client, "observation");
            check(latest != null, "latest observation was lost under backpressure");
            Method text = latest.getClass().getDeclaredMethod("text"); text.setAccessible(true);
            check(JsonParser.parseString((String) text.invoke(latest)).getAsJsonObject().getAsJsonObject("observation").get("state").getAsString().equals("state-99"), "pending observation must replace older state");
            for (int i = 0; i < 64; i++) call(h.client, "enqueue", JsonObject.class, h.frame("heartbeat"));
            check(queue(h.client).size() == 64, "control capacity must be bounded at 64 while send is stalled");
            check(h.socket.sent.size() == 1, "queued controls must not cause overlapping send calls");
            h.socket.pending.get(0).complete(h.socket);
            check(h.socket.sent.size() == 2 && JsonParser.parseString(h.socket.sent.get(1)).getAsJsonObject().get("type").getAsString().equals("heartbeat"), "control must be sent ahead of waiting observation");
            check(h.socket.maximumPending == 1 && queue(h.client).size() == 63, "send completion releases exactly one next message");
            set(h.client, "observationSent", System.nanoTime() - TimeUnit.SECONDS.toNanos(1));
            for (int i = 1; i <= 64; i++) h.socket.pending.get(i).complete(h.socket);
            check(h.socket.sent.size() == 66 && h.socket.sent.subList(1, 65).stream().allMatch(textFrame -> JsonParser.parseString(textFrame).getAsJsonObject().get("type").getAsString().equals("heartbeat")), "all waiting control results precede observation");
            check(JsonParser.parseString(h.socket.sent.get(65)).getAsJsonObject().getAsJsonObject("observation").get("state").getAsString().equals("state-99") && h.socket.maximumPending == 1, "unblocked writer must actually send the newest observation exactly once");
            for (int i = 0; i < 64; i++) call(h.client, "enqueue", JsonObject.class, h.frame("heartbeat"));
            call(h.client, "enqueue", JsonObject.class, h.frame("heartbeat"));
            check(h.socket.aborts == 1 && get(h.client, "socket") == null, "control overflow must disconnect rather than drop a result");
            check(queue(h.client).isEmpty() && get(h.client, "observation") == null && get(h.client, "sending") == null, "disconnect must release all pending buffers");
        }
    }

    private static void deadlineStopsOnlyOnOwnerTick(boolean queueDeadline) throws Exception {
        try (WriterHarness h = new WriterHarness()) {
            h.startHold();
            long now = System.nanoTime();
            set(h.client, "ownerHeartbeat", now);
            set(h.client, "sendStarted", now);
            if (queueDeadline) {
                ArrayDeque<Object> controls = queue(h.client);
                Object first = controls.removeFirst();
                Method text = first.getClass().getDeclaredMethod("text"); text.setAccessible(true);
                Constructor<?> constructor = first.getClass().getDeclaredConstructor(String.class, long.class); constructor.setAccessible(true);
                controls.addFirst(constructor.newInstance(text.invoke(first), now - TimeUnit.SECONDS.toNanos(3)));
            } else set(h.client, "sendStarted", now - TimeUnit.SECONDS.toNanos(3));
            call(h.client, "checkDeadlines");
            check(h.socket.aborts == 1 && get(h.client, "socket") == null, "expired " + (queueDeadline ? "queued control" : "send") + " must close transport");
            check(h.effects.stops == 0 && h.engine.hello().getAsJsonObject("active").get("status").getAsString().equals("running"), "network timeout cannot claim completed game cleanup");
            h.client.tick();
            check(h.effects.stops == 1 && h.engine.hello().get("active").isJsonNull(), "next owner tick must honor timeout stop latch");
            check(h.engine.hello().getAsJsonArray("receipts").get(0).getAsJsonObject().get("status").getAsString().equals("cancelled"), "cancelled outcome requires actual cleanup");
        }
    }

    private static Object get(Object object, String name) throws Exception {
        Field field = object.getClass().getDeclaredField(name); field.setAccessible(true); return field.get(object);
    }
    private static void set(Object object, String name, Object value) throws Exception {
        Field field = object.getClass().getDeclaredField(name); field.setAccessible(true); field.set(object, value);
    }
    @SuppressWarnings("unchecked") private static ArrayDeque<Object> queue(DirectClient client) throws Exception { return (ArrayDeque<Object>) get(client, "controls"); }
    private static void call(Object object, String name) throws Exception {
        Method method = object.getClass().getDeclaredMethod(name); method.setAccessible(true); method.invoke(object);
    }
    private static void call(Object object, String name, Class<?> type, Object value) throws Exception {
        Method method = object.getClass().getDeclaredMethod(name, type); method.setAccessible(true); method.invoke(object, value);
    }
}
