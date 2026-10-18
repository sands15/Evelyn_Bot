package dev.evelyn.minecraft;

import com.google.gson.JsonObject;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.BooleanSupplier;

public final class ControlSessionTest {
    private static int checks;
    private static void check(boolean value) { checks++; if (!value) throw new AssertionError("check " + checks); }
    static final class Effect implements DirectClient.ControlExecutor {
        int starts, stops, fences, offs; String result; BooleanSupplier permit;
        public void start(JsonObject command, BooleanSupplier permit) { this.permit = permit; starts++; check(command.get("target_x").getAsInt() == -2); }
        public String poll() { return result; }
        public void stop() { stops++; }
        public void fence(int seq) { fences++; }
        public void off() { offs++; }
        public String resultCode() { return "arrived"; }
    }
    static final class H {
        final AtomicLong clock = new AtomicLong(1_000_000_000L);
        final List<JsonObject> output = new ArrayList<>(); final Effect effect = new Effect();
        final SessionEngine engine = new SessionEngine(effect, clock::get, output::add);
        H() { observe(1, 1); engine.transportOpened(); engine.offer(base("welcome")); engine.tick(); }
        void observe(int seq, int gen) {
            JsonObject value = new JsonObject(), self = new JsonObject(); value.addProperty("seq", seq);
            self.addProperty("connection_id", "connection"); self.addProperty("generation", gen); value.add("self", self);
            engine.observeControl(value);
        }
        JsonObject base(String type) {
            JsonObject h = engine.hello(), value = new JsonObject(); value.addProperty("v", 3); value.addProperty("type", type);
            value.add("session", h.get("session")); value.add("world", h.get("world")); return value;
        }
        JsonObject command(int seq) {
            JsonObject c = base("command"); c.addProperty("request_id", "r" + seq); c.addProperty("seq", seq);
            c.addProperty("action", "navigate"); c.addProperty("observation_seq", 1); c.addProperty("max_age_ms", 1000); c.addProperty("run_ms", 2000);
            c.addProperty("target_x", -2); c.addProperty("target_y", 64); c.addProperty("target_z", 0); return c;
        }
    }
    public static void main(String[] args) {
        Effect implicit=new Effect();
        try {new DirectClient(java.net.URI.create("ws://127.0.0.1:1234/minecraft"),"x".repeat(32),java.util.Set.of("navigate"),implicit);throw new AssertionError("implicit control");}
        catch(IllegalArgumentException expected) {check(expected.getMessage().equals("explicit_control_required")&&implicit.offs==0);}
        H h = new H(); h.engine.offer(Protocol.decode(h.command(1).toString(), 3)); h.engine.tick();
        check(h.effect.starts == 1 && h.effect.permit.getAsBoolean());
        JsonObject cancel = h.base("cancel"); cancel.addProperty("request_id", "cancel"); cancel.addProperty("seq", 1);
        h.engine.offer(cancel); check(!h.effect.permit.getAsBoolean() && h.effect.fences == 1);
        h.engine.tick(); check(h.effect.stops == 1);
        h.engine.offer(h.command(1)); h.engine.tick(); check(h.effect.starts == 1);
        H success = new H(); success.engine.offer(success.command(1)); success.engine.tick(); success.effect.result = "succeeded"; success.engine.tick();
        JsonObject receipt = success.engine.hello().getAsJsonArray("receipts").get(0).getAsJsonObject();
        check(receipt.get("evidence").getAsString().equals("client_simulated") && receipt.get("code").getAsString().equals("arrived"));
        H world = new H(); world.engine.offer(world.command(1)); world.engine.tick(); world.observe(2, 2);
        check(!world.effect.permit.getAsBoolean() && world.effect.stops == 1 && world.engine.hello().getAsJsonArray("receipts").isEmpty());
        H lost = new H(); lost.engine.offer(lost.command(1)); lost.engine.tick(); lost.engine.transportLost();
        check(!lost.effect.permit.getAsBoolean()); lost.engine.transportOpened(); lost.engine.offer(lost.base("welcome")); lost.engine.tick();
        check(!lost.effect.permit.getAsBoolean());
        H off = new H(); off.engine.offer(off.command(1)); off.engine.tick(); JsonObject f = off.base("off"); f.addProperty("request_id", "off"); off.engine.offer(f);
        check(off.effect.offs == 1 && !off.effect.permit.getAsBoolean()); check(!off.engine.offer(off.command(2)));
        H late = new H(); late.engine.offer(late.command(1)); late.engine.tick(); late.clock.addAndGet(2_000_000_000L);
        check(!late.effect.permit.getAsBoolean()); late.engine.tick(); check(late.effect.stops == 1);
        System.out.println("ControlSessionTest " + checks + " checks PASS");
    }
}
