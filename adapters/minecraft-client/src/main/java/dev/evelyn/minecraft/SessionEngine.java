package dev.evelyn.minecraft;

import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;
import java.util.function.BooleanSupplier;
import java.util.function.LongSupplier;

final class SessionEngine {
    private final Thread owner = Thread.currentThread();
    private final Set<String> actions;
    private final DirectClient.Executor executor;
    private final LongSupplier clock;
    private final Consumer<JsonObject> output;
    private final boolean observer;
    private final DirectClient.ControlExecutor control;
    private final int version;
    private final String session = UUID.randomUUID().toString();
    private final String incarnation = UUID.randomUUID().toString();
    private final Object inboxLock = new Object();
    private final ArrayDeque<JsonObject> inbox = new ArrayDeque<>();
    private final ArrayDeque<JsonObject> cancels = new ArrayDeque<>();
    private final Map<Integer, Integer> cancelFences = new HashMap<>();
    private final AtomicBoolean live = new AtomicBoolean();
    private final AtomicBoolean stopLatch = new AtomicBoolean();
    private final LinkedHashMap<Integer, Record> records = new LinkedHashMap<>();
    private final LinkedHashMap<Integer, Long> observations = new LinkedHashMap<>();
    private String world = "unbound";
    private int next = 1;
    private int tickNumber;
    private int observationNumber;
    private boolean ready;
    private boolean poisoned;
    private volatile Record active;
    private volatile Record maintenance;
    private JsonObject observation;
    private volatile JsonObject published;
    private boolean observerStopped;
    private PlayerObservation.Snapshot lastSnapshot;
    private long snapshotOrigin;

    private static final class Record {
        final int seq;
        final JsonObject command;
        final long deadline;
        String status;
        String code;
        boolean started;
        volatile boolean cancelled;
        Record(int seq, JsonObject command, long deadline, String status, String code) {
            this.seq = seq; this.command = command; this.deadline = deadline; this.status = status; this.code = code;
        }
        JsonObject receipt() {
            JsonObject result = new JsonObject(); result.addProperty("seq", seq);
            result.addProperty("status", status); result.addProperty("code", code); return result;
        }
        boolean terminal() { return !status.equals("accepted") && !status.equals("running"); }
    }

    SessionEngine(Set<String> actions, DirectClient.Executor executor, LongSupplier clock, Consumer<JsonObject> output) {
        this(actions, executor, clock, output, false);
    }

    SessionEngine(LongSupplier clock, Consumer<JsonObject> output) {
        this(Set.of(), null, clock, output, true);
    }

    SessionEngine(DirectClient.ControlExecutor executor, LongSupplier clock, Consumer<JsonObject> output) {
        this(executor,clock,output,3);
    }

    SessionEngine(DirectClient.ControlExecutor executor, LongSupplier clock, Consumer<JsonObject> output, int version) {
        this(version == 5 ? Set.of("attack","inventory_page","inventory_transfer","jump","navigate","look","swap_hands") : version == 4 ? Set.of("attack","jump","navigate","look") : Set.of("navigate", "look"), executor, clock, output, false,version);
        if(version!=3 && version!=4 && version!=5) throw Protocol.invalid();
    }

    private SessionEngine(Set<String> actions, DirectClient.Executor executor, LongSupplier clock, Consumer<JsonObject> output, boolean observer) {
        this(actions,executor,clock,output,observer,executor instanceof DirectClient.ControlExecutor ? 3 : observer ? 2 : 1);
    }

    private SessionEngine(Set<String> actions, DirectClient.Executor executor, LongSupplier clock, Consumer<JsonObject> output, boolean observer,int version) {
        if (actions == null || actions.size() > 16 || (!observer && executor == null) || clock == null || output == null) throw Protocol.invalid();
        actions.forEach(Protocol::checkSymbol);
        this.actions = Set.copyOf(actions); this.executor = executor; this.clock = clock; this.output = output; this.observer = observer;
        control = executor instanceof DirectClient.ControlExecutor c ? c : null;
        this.version = version;
        if (version >= 2) world = "none";
        publish();
    }

    void requireOwner() {
        if (Thread.currentThread() != owner) throw new IllegalStateException("tick_owner_required");
    }

    void transportOpened() { if (version >= 2 && observerStopped) return; live.set(true); }

    void transportLost() {
        live.set(false); stopLatch.set(true); maintenance=null;
        synchronized (inboxLock) {
            inbox.clear(); cancels.clear(); cancelFences.clear();
            if (version >= 2) { observerStopped = true; observation = null; lastSnapshot = null; publish(); }
        }
    }

    boolean bindingMatches(JsonObject value) {
        JsonObject snapshot = published;
        return snapshot.get("session").equals(value.get("session")) && snapshot.get("world").equals(value.get("world"));
    }

    boolean offer(JsonObject message) {
        synchronized (inboxLock) {
            if (control != null && (observerStopped || message.get("v").getAsInt() != version)) return false;
            if (observer && (observerStopped || message.get("v").getAsInt() != 2
                    || !(message.get("type").getAsString().equals("welcome") || message.get("type").getAsString().equals("heartbeat")))) return false;
            if (inbox.size() + cancels.size() >= 64) { transportLost(); return false; }
            JsonObject copy = message.deepCopy();
            if (control != null && copy.get("type").getAsString().equals("off")) {
                if (bindingMatches(copy)) { observerStopped = true; live.set(false); stopLatch.set(true); maintenance=null; control.off(); }
                return true;
            }
            if (copy.get("type").getAsString().equals("cancel")) {
                cancels.add(copy);
                if (bindingMatches(copy)) {
                    int seq = copy.get("seq").getAsInt(); cancelFences.merge(seq, 1, Integer::sum);
                    if (active != null && active.seq == seq) active.cancelled = true;
                    Record retained=maintenance;
                    if(retained!=null && retained.seq==seq) retained.cancelled=true;
                    if (control != null) control.fence(seq);
                }
            } else inbox.add(copy);
            return true;
        }
    }

    void tick() {
        requireOwner();
        if (tickNumber == Protocol.MAX_SEQUENCE) { poisoned = true; stopLatch.set(true); }
        else tickNumber++;
        if (stopLatch.getAndSet(false)) { ready = false; stopActive("transport_lost"); }
        List<JsonObject> urgent;
        List<JsonObject> regular;
        synchronized (inboxLock) {
            urgent = new ArrayList<>(cancels); cancels.clear();
            regular = new ArrayList<>(inbox); inbox.clear();
        }
        List<JsonObject> deferred = new ArrayList<>();
        for (JsonObject request : urgent) {
            int seq = request.get("seq").getAsInt();
            boolean pending = regular.stream().anyMatch(x -> x.get("type").getAsString().equals("command") && x.get("seq").getAsInt() == seq && bindingMatches(x));
            if (bindingMatches(request) && !records.containsKey(seq) && pending) deferred.add(request);
            else { cancel(request); removeFence(request); }
        }
        for (JsonObject request : regular) {
            switch (request.get("type").getAsString()) {
                case "welcome" -> { if (bindingMatches(request) && live.get() && !stopLatch.get() && !poisoned) ready = true; }
                case "command" -> admit(request);
                case "query" -> query(request);
                default -> { /* Heartbeat is handled by the transport, never by game code. */ }
            }
        }
        for (JsonObject request : deferred) { cancel(request); removeFence(request); }
        if (active != null) {
            if (stopLatch.get() || !live.get()) { ready = false; stopActive("transport_lost"); }
            else if (fenced(active.seq)) stopActive("cancelled");
            else if (clock.getAsLong() >= active.deadline) stopActive("run_expired");
            else if (!active.started) {
                if (!ready || poisoned) stopActive("not_ready");
                else if (!fresh(active.command)) stopActive("observation_stale");
                else {
                    // Mark first: even an exception from start may have produced a partial effect.
                    Record starting = active; starting.started = true;
                    try {
                        if (control == null) executor.start(starting.command.get("action").getAsString());
                        else control.start(starting.command.deepCopy(), () -> active == starting && !starting.cancelled
                            && live.get() && !stopLatch.get() && clock.getAsLong() < starting.deadline && bindingMatches(starting.command));
                    }
                    catch (Exception failure) { if (active == starting) stopAfterExecutorFailure(); }
                    if (active == starting) {
                        String interrupted = interruption(starting);
                        if (interrupted != null) stopActive(interrupted);
                        else { starting.status = "running"; starting.code = "running"; emit(starting, null); }
                    }
                }
            }
            if (active != null && active.started) {
                String interrupted = interruption(active);
                if (interrupted != null) stopActive(interrupted);
                else {
                    Record polled = active;
                    try {
                        String status = executor.poll();
                        // A callback can cross a deadline or invalidate its world/connection before returning.
                        if (active == polled) {
                            interrupted = interruption(polled);
                            if (interrupted != null) stopActive(interrupted);
                            else if (status != null) {
                                if (!status.equals("succeeded") && !status.equals("failed")) stopAfterExecutorFailure();
                                else {
                                    String code = control == null ? status : control.resultCode(); Protocol.checkSymbol(code);
                                    if(version>=4 && status.equals("succeeded")
                                            && Set.of("look","navigate","jump","attack").contains(polled.command.get("action").getAsString())) maintenance=polled;
                                    active = null; polled.status = status; polled.code = code; emit(polled, null);
                                }
                            }
                        }
                    } catch (Exception failure) { if (active == polled) stopAfterExecutorFailure(); }
                }
            }
        }
        if(version>=4 && active==null) idle();
        publish();
    }

    private void idle() {
        Record retained=maintenance;
        BooleanSupplier permit=()->retained!=null && maintenance==retained && !retained.cancelled
                && live.get() && ready && !poisoned && !stopLatch.get()
                && clock.getAsLong()<retained.deadline && bindingMatches(retained.command);
        boolean permitted=permit.getAsBoolean();
        if(!permitted) maintenance=null;
        try {
            control.idle(permit);
            // A callback can cross its deadline, receive cancel, or lose the transport mid-step.
            if(permitted && !permit.getAsBoolean()) {maintenance=null;control.idle(()->false);}
        } catch(Exception failure) {
            maintenance=null;poisoned=true;ready=false;transportLost();
            try {control.off();} catch(RuntimeException ignored) { /* Already revoked; no further effect is authorized. */ }
        }
    }

    private void stopAfterExecutorFailure() {
        stopActive("executor_failed", "failed");
    }

    private String interruption(Record record) {
        if (!world.equals(record.command.get("world").getAsString())) return "world_changed";
        if (stopLatch.get() || !live.get()) return "transport_lost";
        if (fenced(record.seq)) return "cancelled";
        if (clock.getAsLong() >= record.deadline) return "run_expired";
        if (!ready || poisoned) return "not_ready";
        return null;
    }

    private boolean fenced(int seq) { synchronized (inboxLock) { return cancelFences.containsKey(seq); } }

    private void removeFence(JsonObject request) {
        synchronized (inboxLock) {
            if (bindingMatches(request)) cancelFences.computeIfPresent(request.get("seq").getAsInt(), (seq, count) -> count <= 1 ? null : count - 1);
        }
    }

    private void admit(JsonObject message) {
        int seq = message.get("seq").getAsInt();
        String request = message.get("request_id").getAsString();
        JsonObject command = message.deepCopy(); command.remove("request_id");
        if (!bindingMatches(message)) { reject(seq, request, "stale_binding"); return; }
        if (seq < next) {
            Record old = records.get(seq);
            if (old == null) reply(seq, "unknown", "receipt_expired", request);
            else if (!old.command.equals(command)) reject(seq, request, "sequence_conflict");
            else emit(old, request);
            return;
        }
        if (seq > next) { reject(seq, request, "sequence_gap"); return; }
        if (next == Protocol.MAX_SEQUENCE) { reject(seq, request, "sequence_exhausted"); return; }
        next++;
        String rejection = null;
        if (poisoned) rejection = "executor_blocked";
        else if (!ready || !live.get() || stopLatch.get()) rejection = "not_ready";
        else if (!actions.contains(message.get("action").getAsString())) rejection = "unsupported_action";
        else if (message.get("run_ms").getAsInt() > 30000 || message.get("max_age_ms").getAsInt() > 2000) rejection = "invalid_limits";
        else if (!fresh(message)) rejection = "observation_stale";
        else if (active != null) rejection = "busy";
        Record record = new Record(seq, command, clock.getAsLong() + message.get("run_ms").getAsLong() * 1_000_000L,
            rejection == null ? "accepted" : "rejected", rejection == null ? "accepted" : rejection);
        records.put(seq, record);
        while (records.size() > 64) {
            Integer remove = records.keySet().stream().filter(key -> active == null || key != active.seq).findFirst().orElseThrow();
            records.remove(remove);
        }
        if (rejection == null) {maintenance=null;active = record;}
        emit(record, request);
    }

    private boolean fresh(JsonObject command) {
        Long source = observations.get(command.get("observation_seq").getAsInt());
        long now = clock.getAsLong();
        return source != null && now >= source && now - source <= command.get("max_age_ms").getAsLong() * 1_000_000L;
    }

    private void cancel(JsonObject request) {
        int seq = request.get("seq").getAsInt(); String id = request.get("request_id").getAsString();
        if (!bindingMatches(request)) { reject(seq, id, "stale_binding"); return; }
        Record record = records.get(seq);
        if (record == null) { reply(seq, "unknown", seq < next ? "receipt_expired" : "unknown_sequence", id); return; }
        if(version>=4) record.cancelled=true;
        if (!record.terminal() && active == record) stopActive("cancelled");
        emit(record, id);
    }

    private void query(JsonObject request) {
        int seq = request.get("seq").getAsInt(); String id = request.get("request_id").getAsString();
        if (!bindingMatches(request)) { reject(seq, id, "stale_binding"); return; }
        Record record = records.get(seq);
        if (record == null) reply(seq, "unknown", seq < next ? "receipt_expired" : "unknown_sequence", id);
        else emit(record, id);
    }

    private void stopActive(String reason) {
        stopActive(reason, "cancelled");
    }

    private void stopActive(String reason, String status) {
        maintenance=null;
        if (active == null) return;
        Record stopped = active; active = null;
        try {
            if (stopped.started) executor.stop();
            stopped.status = status; stopped.code = reason;
        } catch (Exception failure) { stopped.status = "unknown"; stopped.code = "stop_failed"; poisoned = true; ready = false; }
        emit(stopped, null);
    }

    void observe(String state) {
        requireOwner(); if (version != 1) throw Protocol.invalid(); Protocol.checkSymbol(state);
        if (observationNumber == Protocol.MAX_SEQUENCE) { poisoned = true; stopLatch.set(true); throw new IllegalStateException("observation_exhausted"); }
        int seq = ++observationNumber;
        observations.put(seq, clock.getAsLong());
        while (observations.size() > 32) observations.remove(observations.keySet().iterator().next());
        observation = new JsonObject(); observation.addProperty("seq", seq); observation.addProperty("tick", Math.max(1, tickNumber)); observation.addProperty("state", state);
        publish(); JsonObject frame = frame("observe"); frame.add("observation", observation.deepCopy()); output.accept(frame);
    }

    void observe(PlayerObservation.Snapshot snapshot, long originNanos) {
        requireOwner(); if (!observer) throw Protocol.invalid();
        JsonObject value = Protocol.observation(snapshot, originNanos);
        JsonObject outgoing;
        synchronized (inboxLock) {
            if (observerStopped) throw new IllegalStateException("observer_stopped");
            if (lastSnapshot != null) {
                if (originNanos != snapshotOrigin || !lastSnapshot.connectionId().equals(snapshot.connectionId())) throw Protocol.invalid();
                if (snapshot.equals(lastSnapshot)) return;
                if (snapshot.sequence() <= lastSnapshot.sequence() || snapshot.generation() < lastSnapshot.generation()
                        || snapshot.eventSequence() < lastSnapshot.eventSequence() || snapshot.observedAtNanos() - lastSnapshot.observedAtNanos() < 0) throw Protocol.invalid();
            }
            String newWorld = snapshot.connectionId() + ":" + snapshot.generation();
            boolean changed = !world.equals(newWorld);
            world = newWorld; observation = value; lastSnapshot = snapshot; snapshotOrigin = originNanos;
            if (changed) { ready = false; inbox.clear(); }
            publish();
            outgoing = changed ? hello() : frame("observe");
            if (!changed) outgoing.add("observation", value.deepCopy());
        }
        // Never hold inboxLock while the transport takes its own lock (disconnect takes them in reverse order).
        output.accept(outgoing);
    }

    void setWorld(String newWorld) {
        requireOwner(); if (version != 1) throw Protocol.invalid(); Protocol.checkSymbol(newWorld);
        if (world.equals(newWorld)) return;
        ready = false; stopActive("world_changed"); world = newWorld;
        // v1 receipts do not carry their own world; never relabel an old result under a new binding.
        records.clear();
        observations.clear(); observation = null;
        synchronized (inboxLock) { inbox.clear(); cancels.clear(); cancelFences.clear(); }
        publish(); output.accept(hello());
    }

    JsonObject hello() { return published.deepCopy(); }

    void observeControl(JsonObject projection) {
        requireOwner(); if (control == null || observerStopped) throw Protocol.invalid();
        JsonObject self = projection.getAsJsonObject("self");
        int seq = Protocol.integer(projection, "seq");
        if (seq <= observationNumber) throw Protocol.invalid();
        String newWorld = self.get("connection_id").getAsString() + ":" + self.get("generation").getAsInt();
        boolean changed = !world.equals(newWorld);
        if (changed) {
            ready = false; stopActive("world_changed"); records.clear(); observations.clear();
            synchronized (inboxLock) { inbox.clear(); cancels.clear(); cancelFences.clear(); }
        }
        world = newWorld; observationNumber = seq; observation = projection.deepCopy();
        observations.put(seq, clock.getAsLong());
        while (observations.size() > 32) observations.remove(observations.keySet().iterator().next());
        publish(); JsonObject value = changed ? hello() : frame("observe");
        if (!changed) value.add("observation", observation.deepCopy()); output.accept(value);
    }

    private JsonObject frame(String type) {
        JsonObject frame = new JsonObject(); frame.addProperty("v", version); frame.addProperty("type", type);
        frame.addProperty("session", session); frame.addProperty("world", world); return frame;
    }

    private void publish() {
        synchronized (inboxLock) {
        JsonObject hello = frame("hello"); hello.addProperty("incarnation", incarnation); hello.addProperty("next_seq", next);
        hello.add("observation", observation == null ? JsonNull.INSTANCE : observation.deepCopy());
        JsonArray capabilities = new JsonArray(); new TreeSet<>(actions).forEach(capabilities::add); hello.add("capabilities", capabilities);
        hello.add("active", active == null ? JsonNull.INSTANCE : receipt(active));
        JsonArray receipts = new JsonArray(); records.values().forEach(record -> receipts.add(receipt(record))); hello.add("receipts", receipts);
        published = hello;
        }
    }

    private void emit(Record record, String request) { reply(record.seq, record.status, record.code, request); }
    private JsonObject receipt(Record record) {
        JsonObject value = record.receipt();
        if (control != null) value.addProperty("evidence", evidence(record.status,record.code));
        return value;
    }
    private void reject(int seq, String request, String code) { reply(seq, "rejected", code, request); }
    private void reply(int seq, String status, String code, String request) {
        JsonObject value = frame("receipt"); value.addProperty("seq", seq); value.addProperty("status", status); value.addProperty("code", code);
        if (control != null) value.addProperty("evidence", evidence(status,code));
        value.addProperty("request_id", request); value.addProperty("next_seq", next); output.accept(value);
    }
    private String evidence(String status,String code) {
        return !status.equals("succeeded") ? "none" : (version>=4 && code.equals("target_defeated_observed")
                || version==5 && Set.of("inventory_transfer_observed","hand_swap_observed").contains(code)) ? "server_observed" : "client_simulated";
    }
}
