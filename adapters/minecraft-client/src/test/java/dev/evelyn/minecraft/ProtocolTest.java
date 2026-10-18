package dev.evelyn.minecraft;

import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;

/** Contract cases deliberately exercise the actual wire decoder. */
public final class ProtocolTest {
    private static Method decode;
    private static int checks;
    public static void main(String[] args) throws Exception {
        try {
            decode = Class.forName("dev.evelyn.minecraft.Protocol").getDeclaredMethod("decode", String.class);
            decode.setAccessible(true);
        } catch (ReflectiveOperationException missing) {
            throw new AssertionError("strict inbound decoder is not implemented", missing);
        }
        valid("{\"v\":1,\"type\":\"heartbeat\",\"session\":\"s\",\"world\":\"w\"}");
        invalid("{\"v\":1,\"v\":1,\"type\":\"heartbeat\",\"session\":\"s\",\"world\":\"w\"}");
        invalid("{\"v\":1.0,\"type\":\"heartbeat\",\"session\":\"s\",\"world\":\"w\"}");
        invalid("{\"v\":1e0,\"type\":\"heartbeat\",\"session\":\"s\",\"world\":\"w\"}");
        invalid("{\"v\":true,\"type\":\"heartbeat\",\"session\":\"s\",\"world\":\"w\"}");
        invalid("{\"v\":1,\"type\":\"heartbeat\",\"session\":\"s\",\"world\":\"w\",\"extra\":0}");
        invalid("{\"v\":1,\"type\":\"heartbeat\",\"session\":\"s\",\"world\":\"w\"} {}");
        invalid("{\"v\":1,\"type\":\"heartbeat\",\"session\":{},\"world\":\"w\"}");
        invalid("{\"v\":1,\"type\":\"heartbeat\",\"session\":\"\\u0000\",\"world\":\"w\"}");
        invalid("{\"v\":1,\"type\":\"heartbeat\",\"session\":\"s\",\"world\":\"" + "a".repeat(33000) + "\"}");
        invalid("{v:1,type:'heartbeat',session:'s',world:'w'}");
        valid("{\"v\":1,\"type\":\"command\",\"session\":\"s\",\"world\":\"w\",\"request_id\":\"r\",\"seq\":1,\"action\":\"probe\",\"observation_seq\":1,\"max_age_ms\":1,\"run_ms\":1}");
        invalid("{\"v\":1,\"type\":\"command\",\"session\":\"s\",\"world\":\"w\",\"request_id\":\"r\",\"seq\":0,\"action\":\"probe\",\"observation_seq\":1,\"max_age_ms\":1,\"run_ms\":1}");
        invalid("{\"v\":1,\"type\":\"cancel\",\"session\":\"s\",\"world\":\"w\",\"request_id\":null,\"seq\":1}");
        com.google.gson.JsonObject nullable = new com.google.gson.JsonObject();
        nullable.addProperty("request_id", (String) null);
        if (!Protocol.encode(nullable).equals("{\"request_id\":null}")) throw new AssertionError("required explicit null was omitted on wire");
        checks++;
        observerProtocol();
        System.out.println("PROTOCOL_PASS " + checks);
    }
    private static void observerProtocol() throws Exception {
        Method selected;
        try { selected = Protocol.class.getDeclaredMethod("decode", String.class, int.class); }
        catch (NoSuchMethodException missing) { throw new AssertionError("explicit v2 decoder is missing", missing); }
        String base = "\"session\":\"s\",\"world\":\"w\"";
        for (String type : new String[]{"welcome", "heartbeat"}) {
            selected.invoke(null, "{\"v\":2,\"type\":\"" + type + "\"," + base + "}", 2); checks++;
        }
        for (String text : new String[]{
                "{\"v\":1,\"type\":\"welcome\"," + base + "}",
                "{\"v\":2,\"type\":\"command\"," + base + ",\"request_id\":\"r\",\"seq\":1,\"action\":\"probe\",\"observation_seq\":1,\"max_age_ms\":1,\"run_ms\":1}",
                "{\"v\":2,\"type\":\"query\"," + base + ",\"request_id\":\"r\",\"seq\":1}",
                "{\"v\":2,\"type\":\"cancel\"," + base + ",\"request_id\":\"r\",\"seq\":1}",
                "{\"v\":2,\"type\":\"heartbeat\"," + base + ",\"extra\":1}"}) {
            try { selected.invoke(null, text, 2); }
            catch (InvocationTargetException rejected) {
                if (!(rejected.getCause() instanceof IllegalArgumentException)) throw rejected;
                checks++; continue;
            }
            throw new AssertionError("read-only v2 accepted forbidden input");
        }
        invalid("{\"v\":2,\"type\":\"welcome\"," + base + "}");
    }
    private static void valid(String text) throws Exception {
        try { decode.invoke(null, text); checks++; }
        catch (InvocationTargetException failure) { throw new AssertionError("valid message rejected", failure.getCause()); }
    }
    private static void invalid(String text) throws Exception {
        try { decode.invoke(null, text); }
        catch (InvocationTargetException expected) {
            if (!(expected.getCause() instanceof IllegalArgumentException)) throw expected;
            checks++; return;
        }
        throw new AssertionError("invalid message accepted");
    }
}
