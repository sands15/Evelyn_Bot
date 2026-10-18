package dev.evelyn.minecraft;

/** Typed v3 accepts finite coordinates only; v1/v2 must not inherit control authority. */
public final class ControlProtocolTest {
    private static int checks;
    private static void check(boolean value) { checks++; if (!value) throw new AssertionError(); }
    private static void bad(String value, int version) {
        try { Protocol.decode(value, version); throw new AssertionError(value); }
        catch (IllegalArgumentException expected) { checks++; }
    }
    public static void main(String[] args) {
        String base = "{\"v\":3,\"type\":\"command\",\"session\":\"s\",\"world\":\"w\",\"request_id\":\"r\",\"seq\":1,\"action\":\"navigate\",\"observation_seq\":1,\"max_age_ms\":500,\"run_ms\":30000,\"target_x\":-2,\"target_y\":64,\"target_z\":0}";
        check(Protocol.decode(base, 3).get("target_x").getAsInt() == -2);
        bad(base, 1); bad(base, 2);
        for (String n : new String[]{"1.2", "30000001", "-30000001", "1e3", "null", "true", "\"0\""})
            bad(base.replace("\"target_x\":-2", "\"target_x\":" + n), 3);
        bad(base.replace("\"target_z\":0", "\"target_z\":0,\"target_z\":1"), 3);
        bad(base.replace("\"target_z\":0", "\"target_z\":0,\"packet\":1"), 3);
        bad(base.replace("navigate", "raw_packet"), 3);
        String look = base.replace("navigate", "look").replace("\"target_x\":-2,\"target_y\":64,\"target_z\":0", "\"yaw\":-180.5,\"pitch\":90");
        check(Protocol.decode(look, 3).get("yaw").getAsDouble() == -180.5);
        for (String n : new String[]{"NaN", "1e999", "Infinity", "360.1", "null"}) bad(look.replace("-180.5", n), 3);
        bad(look.replace("\"pitch\":90", "\"pitch\":90.1"), 3);
        String off = "{\"v\":3,\"type\":\"off\",\"session\":\"s\",\"world\":\"w\",\"request_id\":\"r\"}";
        check(Protocol.decode(off, 3).get("type").getAsString().equals("off"));
        bad(off, 1); bad(off, 2);
        System.out.println("ControlProtocolTest " + checks + " checks PASS");
    }
}
