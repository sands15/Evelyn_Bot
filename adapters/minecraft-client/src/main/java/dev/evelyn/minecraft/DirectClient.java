package dev.evelyn.minecraft;

import com.google.gson.JsonObject;
import java.net.Proxy;
import java.net.ProxySelector;
import java.net.SocketAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayDeque;
import java.util.List;
import java.util.LinkedHashMap;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.BooleanSupplier;

/** Explicitly started local transport. All executor calls happen only on the constructing thread. */
public final class DirectClient implements AutoCloseable {
    public interface Executor {
        void start(String action) throws Exception;
        /** Null while running, otherwise exactly "succeeded" or "failed". */
        String poll() throws Exception;
        void stop() throws Exception;
    }

    /** Explicit v3 control; fence/off are nonblocking network-thread invalidation hooks. */
    public interface ControlExecutor extends Executor {
        void start(JsonObject command, BooleanSupplier permit) throws Exception;
        default void start(String action) { throw new IllegalArgumentException("typed_command_required"); }
        void fence(int sequence);
        void off();
        String resultCode();
        /** v4 only: bounded physical settling under the completed action's original lease. */
        default void idle(BooleanSupplier lease) throws Exception { }
    }

    private static final long LEASE_NS = TimeUnit.SECONDS.toNanos(3);
    private static final long IO_NS = TimeUnit.SECONDS.toNanos(2);
    private static final long OBSERVATION_NS = TimeUnit.MILLISECONDS.toNanos(200);
    private final URI endpoint;
    private final String token;
    private final SessionEngine engine;
    private final Runnable onDisconnect;
    private final int version;
    private boolean observerDisconnected;
    private final Object lock = new Object();
    private final ArrayDeque<Outbound> controls = new ArrayDeque<>();
    private final LinkedHashMap<Binding, Boolean> helloBindings = new LinkedHashMap<>();
    private JsonObject latestHello;
    private final ExecutorService network;
    private final ScheduledExecutorService scheduler;
    private final HttpClient http;
    private WebSocket socket;
    private Listener listener;
    private CompletableFuture<WebSocket> opening;
    private long generation;
    private boolean connecting;
    private boolean closed;
    private boolean welcomed;
    private long ownerHeartbeat;
    private long handshakeStarted;
    private long observationSent;
    private Outbound observation;
    private Outbound sending;
    private long sendStarted;
    private record Outbound(String text, long queuedAt) { }
    private record Binding(String session, String world) {
        static Binding of(JsonObject value) { return new Binding(value.get("session").getAsString(), value.get("world").getAsString()); }
    }

    public DirectClient(URI endpoint, String token, Set<String> capabilities, Executor executor) {
        this(endpoint, token, capabilities, executor, null);
    }

    /** One-shot v2 observer. The callback must only close the host connection and must not wait for this client's threads. */
    public static DirectClient observer(URI endpoint, String token, Runnable onDisconnect) {
        if (onDisconnect == null) throw new IllegalArgumentException("missing_disconnect_handler");
        AtomicBoolean notified = new AtomicBoolean();
        Runnable once = () -> {
            if (notified.compareAndSet(false, true)) {
                try { onDisconnect.run(); } catch (RuntimeException ignored) { /* Transport cleanup still completes. */ }
            }
        };
        try { return new DirectClient(endpoint, token, Set.of(), null, once); }
        catch (RuntimeException failure) { once.run(); throw failure; }
    }

    public static DirectClient control(URI endpoint, String token, ControlExecutor executor) {
        if (executor == null) throw new IllegalArgumentException("missing_executor");
        try { return new DirectClient(endpoint, token, Set.of("navigate", "look"), executor, executor::off); }
        catch (RuntimeException failure) { executor.off(); throw failure; }
    }

    public static DirectClient advancedControl(URI endpoint, String token, ControlExecutor executor) {
        if (executor == null) throw new IllegalArgumentException("missing_executor");
        try { return new DirectClient(endpoint, token, Set.of("attack", "jump", "navigate", "look"), executor, executor::off, 4); }
        catch (RuntimeException failure) { executor.off(); throw failure; }
    }

    public static DirectClient playerControl(URI endpoint, String token, ControlExecutor executor) {
        if (executor == null) throw new IllegalArgumentException("missing_executor");
        try { return new DirectClient(endpoint, token, Set.of("attack","inventory_page","inventory_transfer","jump","navigate","look","swap_hands"), executor, executor::off, 5); }
        catch (RuntimeException failure) { executor.off(); throw failure; }
    }

    private DirectClient(URI endpoint, String token, Set<String> capabilities, Executor executor, Runnable onDisconnect) {
        this(endpoint,token,capabilities,executor,onDisconnect,executor instanceof ControlExecutor ? 3 : onDisconnect == null ? 1 : 2);
    }

    private DirectClient(URI endpoint, String token, Set<String> capabilities, Executor executor, Runnable onDisconnect, int version) {
        if (onDisconnect == null && executor instanceof ControlExecutor) throw new IllegalArgumentException("explicit_control_required");
        validateEndpoint(endpoint);
        if (token == null || token.length() < 32 || token.length() > 256) throw new IllegalArgumentException("invalid_credential");
        for (int i = 0; i < token.length(); i++) if (token.charAt(i) < 0x21 || token.charAt(i) > 0x7e) throw new IllegalArgumentException("invalid_credential");
        this.endpoint = endpoint; this.token = token; this.onDisconnect = onDisconnect;
        this.version = version;
        this.engine = version >= 3 ? new SessionEngine((ControlExecutor)executor, System::nanoTime, this::enqueue, version)
                : version == 1 ? new SessionEngine(capabilities, executor, System::nanoTime, this::enqueue)
                : new SessionEngine(System::nanoTime, this::enqueue);
        ThreadFactory threads = runnable -> { Thread thread = new Thread(runnable, "evelyn-direct-transport"); thread.setDaemon(true); return thread; };
        network = Executors.newFixedThreadPool(2, threads);
        scheduler = Executors.newSingleThreadScheduledExecutor(threads);
        // Loopback credentials must never be sent through a machine's HTTP proxy.
        ProxySelector direct = new ProxySelector() {
            public List<Proxy> select(URI uri) { return List.of(Proxy.NO_PROXY); }
            public void connectFailed(URI uri, SocketAddress address, java.io.IOException failure) { }
        };
        http = HttpClient.newBuilder().executor(network).connectTimeout(Duration.ofSeconds(3))
            .followRedirects(HttpClient.Redirect.NEVER).proxy(direct).build();
        scheduler.scheduleAtFixedRate(this::checkDeadlines, 50, 50, TimeUnit.MILLISECONDS);
    }

    static void validateEndpoint(URI endpoint) {
        if (endpoint == null || !"ws".equals(endpoint.getScheme()) || endpoint.getPort() < 1 || endpoint.getPort() > 65535
            || !"/minecraft".equals(endpoint.getRawPath()) || endpoint.getRawQuery() != null || endpoint.getRawFragment() != null || endpoint.getRawUserInfo() != null
            || !("127.0.0.1".equals(endpoint.getHost()) || "[::1]".equals(endpoint.getHost()) || "::1".equals(endpoint.getHost()))) {
            throw new IllegalArgumentException("invalid_endpoint");
        }
    }

    /** Physical connect only; welcome and action readiness are processed by subsequent host ticks. */
    public CompletableFuture<Void> connect() {
        engine.requireOwner();
        synchronized (lock) {
            if (closed) throw new IllegalStateException("client_closed");
            if (observerDisconnected) throw new IllegalStateException("observer_stopped");
            if (connecting || socket != null) throw new IllegalStateException("already_connected");
            connecting = true; welcomed = false; handshakeStarted = System.nanoTime();
            long attempt = ++generation;
            listener = new Listener(attempt);
            CompletableFuture<Void> result = new CompletableFuture<>();
            try {
                opening = http.newWebSocketBuilder().connectTimeout(Duration.ofSeconds(3))
                    .header("Authorization", "Bearer " + token).subprotocols("evelyn.minecraft.v" + version)
                    .buildAsync(endpoint, listener);
            } catch (RuntimeException failure) {
                fail(attempt); result.completeExceptionally(new IllegalStateException("connect_failed")); return result;
            }
            opening.whenComplete((connected, error) -> {
                synchronized (lock) {
                    if (attempt != generation || closed) { if (connected != null) connected.abort(); result.completeExceptionally(new IllegalStateException("connect_abandoned")); return; }
                    if (error != null) { fail(attempt); result.completeExceptionally(new IllegalStateException("connect_failed")); }
                    else result.complete(null);
                }
            });
            return result;
        }
    }

    public void tick() { engine.tick(); }
    /** Matching welcome received on the current transport; this is never game/action readiness. */
    public boolean ready() { synchronized (lock) { return socket != null && currentWelcome() && !closed && !observerDisconnected; } }
    private boolean currentWelcome() { return welcomed && (onDisconnect == null || latestHello != null && engine.bindingMatches(latestHello)); }
    public void observe(String state) { engine.observe(state); }
    public void observeControl(JsonObject projection) { synchronized (lock) { engine.observeControl(projection); } }
    public void observe(PlayerObservation.Snapshot snapshot, long originNanos) {
        synchronized (lock) { engine.observe(snapshot, originNanos); }
    }
    public void setWorld(String world) { engine.setWorld(world); }

    /** No reconnect or replay. A final owner tick is needed to finish executor cleanup. */
    public void disconnect() { synchronized (lock) { fail(generation); } }

    @Override public void close() {
        synchronized (lock) {
            if (closed) return;
            closed = true; fail(generation);
        }
        scheduler.shutdownNow(); http.shutdownNow(); network.shutdownNow();
    }

    private void enqueue(JsonObject value) {
        final String encoded;
        try { encoded = Protocol.encode(value); }
        catch (IllegalArgumentException malformed) { synchronized (lock) { fail(generation); } return; }
        synchronized (lock) {
            if (socket == null || closed) return;
            String type = value.get("type").getAsString();
            if (type.equals("observe")) observation = new Outbound(encoded, System.nanoTime());
            else {
                if (controls.size() >= 64) { fail(generation); return; }
                if (type.equals("hello")) {
                    if (onDisconnect != null && !rememberHello(value)) return;
                    welcomed = false; handshakeStarted = System.nanoTime(); observation = null;
                }
                controls.add(new Outbound(encoded, System.nanoTime()));
            }
            pump();
        }
    }

    /** Keep every outstanding binding; only acknowledged history may be evicted. Caller holds lock. */
    private boolean rememberHello(JsonObject value) {
        Binding binding = Binding.of(value);
        if (!helloBindings.containsKey(binding) && helloBindings.size() == 64) {
            Binding acknowledged = helloBindings.entrySet().stream().filter(entry -> entry.getValue()).map(java.util.Map.Entry::getKey).findFirst().orElse(null);
            if (acknowledged == null) { fail(generation); return false; }
            helloBindings.remove(acknowledged);
        }
        helloBindings.put(binding, false);
        latestHello = new JsonObject(); latestHello.add("session", value.get("session")); latestHello.add("world", value.get("world"));
        return true;
    }

    /** Caller holds lock. sendText is asynchronous; one completion releases the next message. */
    private void pump() {
        if (socket == null || sending != null || closed) return;
        Outbound next = controls.poll();
        if (next == null && observation != null && System.nanoTime() - observationSent >= OBSERVATION_NS) {
            next = observation; observation = null; observationSent = System.nanoTime();
        }
        if (next == null) return;
        Outbound item = next;
        sending = item; sendStarted = System.nanoTime(); long attempt = generation;
        try {
            socket.sendText(item.text(), true).whenComplete((sent, error) -> {
                synchronized (lock) {
                    if (attempt != generation || sending != item) return;
                    sending = null;
                    if (error != null) fail(attempt); else pump();
                }
            });
        } catch (RuntimeException failure) { fail(attempt); }
    }

    private void checkDeadlines() {
        synchronized (lock) {
            if (closed || (!connecting && socket == null)) return;
            long now = System.nanoTime();
            boolean expired = !welcomed ? now - handshakeStarted >= LEASE_NS : now - ownerHeartbeat >= LEASE_NS;
            expired |= sending != null && now - sendStarted >= IO_NS;
            expired |= !controls.isEmpty() && now - controls.peek().queuedAt() >= IO_NS;
            expired |= listener != null && listener.assembling && now - listener.fragmentStarted >= IO_NS;
            if (expired) fail(generation); else pump();
        }
    }

    /** Caller holds lock. Generation fences callbacks and outstanding sends from the old socket. */
    private void fail(long attempt) {
        if (attempt != generation) return;
        generation++;
        WebSocket previous = socket; socket = null; connecting = false; welcomed = false;
        controls.clear(); helloBindings.clear(); latestHello = null; observation = null; sending = null; listener = null;
        engine.transportLost();
        if (opening != null && !opening.isDone()) opening.cancel(true);
        opening = null;
        if (previous != null) previous.abort();
        if (onDisconnect != null && !observerDisconnected) { observerDisconnected = true; onDisconnect.run(); }
    }

    private final class Listener implements WebSocket.Listener {
        private final long attempt;
        private final StringBuilder fragments = new StringBuilder();
        private volatile boolean assembling;
        private volatile long fragmentStarted;
        Listener(long attempt) { this.attempt = attempt; }

        @Override public void onOpen(WebSocket opened) {
            synchronized (lock) {
                if (attempt != generation || closed) { opened.abort(); return; }
                if (onDisconnect != null && !("evelyn.minecraft.v" + version).equals(opened.getSubprotocol())) { opened.abort(); fail(attempt); return; }
                socket = opened; connecting = false; engine.transportOpened();
                ownerHeartbeat = System.nanoTime(); enqueue(engine.hello());
            }
            opened.request(1);
        }

        @Override public CompletionStage<?> onText(WebSocket peer, CharSequence part, boolean last) {
            synchronized (lock) { if (attempt != generation || closed) return CompletableFuture.completedFuture(null); }
            if (!assembling) { fragmentStarted = System.nanoTime(); assembling = true; }
            if (System.nanoTime() - fragmentStarted >= IO_NS || fragments.length() + part.length() > Protocol.MAX_BYTES) {
                synchronized (lock) { fail(attempt); } return CompletableFuture.completedFuture(null);
            }
            fragments.append(part);
            if (fragments.toString().getBytes(StandardCharsets.UTF_8).length > Protocol.MAX_BYTES) {
                synchronized (lock) { fail(attempt); } return CompletableFuture.completedFuture(null);
            }
            if (last) {
                String message = fragments.toString(); fragments.setLength(0); assembling = false;
                try {
                    JsonObject value = Protocol.decode(message, version);
                    synchronized (lock) {
                        if (attempt != generation || closed) return CompletableFuture.completedFuture(null);
                        String type = value.get("type").getAsString();
                        if (type.equals("heartbeat")) {
                            if (currentWelcome() && engine.bindingMatches(value)) { ownerHeartbeat = System.nanoTime(); enqueue(value); }
                        } else if (type.equals("welcome")) {
                            boolean current = engine.bindingMatches(value);
                            if (onDisconnect != null) {
                                Binding binding = Binding.of(value);
                                if (!helloBindings.containsKey(binding)) { fail(attempt); return CompletableFuture.completedFuture(null); }
                                helloBindings.put(binding, true);
                                current &= binding.equals(Binding.of(latestHello));
                            } else if (!current) { fail(attempt); return CompletableFuture.completedFuture(null); }
                            if (current) {
                                welcomed = true; ownerHeartbeat = System.nanoTime();
                                if (!engine.offer(value)) fail(attempt);
                            }
                        } else if (!engine.offer(value)) fail(attempt);
                    }
                } catch (IllegalArgumentException failure) { synchronized (lock) { fail(attempt); } }
            }
            synchronized (lock) { if (attempt == generation && !closed) peer.request(1); }
            return CompletableFuture.completedFuture(null);
        }

        @Override public CompletionStage<?> onBinary(WebSocket peer, ByteBuffer data, boolean last) {
            synchronized (lock) { fail(attempt); } return CompletableFuture.completedFuture(null);
        }
        @Override public CompletionStage<?> onPing(WebSocket peer, ByteBuffer message) { peer.request(1); return CompletableFuture.completedFuture(null); }
        @Override public CompletionStage<?> onPong(WebSocket peer, ByteBuffer message) { peer.request(1); return CompletableFuture.completedFuture(null); }
        @Override public CompletionStage<?> onClose(WebSocket peer, int status, String reason) {
            synchronized (lock) { fail(attempt); } return CompletableFuture.completedFuture(null);
        }
        @Override public void onError(WebSocket peer, Throwable failure) { synchronized (lock) { fail(attempt); } }
    }
}
