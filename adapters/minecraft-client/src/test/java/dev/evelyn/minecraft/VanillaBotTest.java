package dev.evelyn.minecraft;

public final class VanillaBotTest {
    public static void main(String[] args) throws Exception {
        String id = "12345678-1234-4234-8234-123456789abc";
        var defaults = VanillaBot.arguments(new String[]{id});
        check(defaults.host().equals("127.0.0.1") && defaults.port() == 25565);
        var observing = VanillaBot.arguments(new String[]{id, "--observe", "ws://127.0.0.1:1234/minecraft"});
        check(observing.observerEndpoint().toString().equals("ws://127.0.0.1:1234/minecraft"));
        check(defaults.observerEndpoint() == null);
        check(!defaults.controlMode() && !observing.controlMode());
        var controlling=VanillaBot.arguments(new String[]{id,"--control","ws://127.0.0.1:1234/minecraft"});
        check(controlling.controlMode() && !controlling.advancedControlMode() && controlling.observerEndpoint()!=null);
        var advanced=VanillaBot.arguments(new String[]{id,"--advanced-control","ws://127.0.0.1:1234/minecraft"});
        check(advanced.controlMode() && advanced.advancedControlMode() && advanced.port()==25565);
        var advancedExplicit=VanillaBot.arguments(new String[]{id,"localhost","25566","--advanced-control","ws://127.0.0.1:1234/minecraft"});
        check(advancedExplicit.advancedControlMode() && advancedExplicit.host().equals("localhost") && advancedExplicit.port()==25566);
        check(!defaults.advancedControlMode() && !observing.advancedControlMode());
        reject(new String[]{id,"--advanced-control"});
        reject(new String[]{id,"--advanced-control","ws://example.com:1234/minecraft"});
        reject(new String[]{id,"--advanced-control","ws://127.0.0.1:1234/minecraft?token=no"});
        reject(new String[]{id,"--advanced-control","ws://127.0.0.1:1234/other"});
        reject(new String[]{id,"--control","ws://127.0.0.1:1234/minecraft","--advanced-control","ws://127.0.0.1:1234/minecraft"});
        reject(new String[]{id,"--control","ws://example.com:1234/minecraft"});
        reject(new String[]{id,"--observe","ws://127.0.0.1:1234/minecraft","--control","ws://127.0.0.1:1234/minecraft"});
        reject(new String[]{id, "--observe", "ws://example.com:1234/minecraft"});
        reject(new String[]{id, "--observe", "ws://127.0.0.1:1234/minecraft?token=no"});
        reject(new String[]{id, "--observe", "https://127.0.0.1:1234/minecraft"});
        String token = "synthetic-observer-token-" + "x".repeat(32);
        check(VanillaBot.observerToken(new java.io.ByteArrayInputStream((token + "\n").getBytes())).equals(token));
        for (String invalid : new String[]{"short\n", "x".repeat(257) + "\n", "x".repeat(40), "x".repeat(40) + " \n"}) {
            try { VanillaBot.observerToken(new java.io.ByteArrayInputStream(invalid.getBytes())); throw new AssertionError("invalid token accepted"); }
            catch (IllegalArgumentException expected) { }
        }
        var explicit = VanillaBot.arguments(new String[]{id, "localhost", "25566"});
        check(explicit.clientId().equals(id) && explicit.port() == 25566);
        reject(new String[]{}); reject(new String[]{"not-a-client-id"});
        reject(new String[]{id, "", "25565"}); reject(new String[]{id, "localhost", "0"});
        reject(new String[]{id, "localhost", "65536"}); reject(new String[]{id, "localhost", "abc"});
        reject(new String[]{id, "localhost", "25565", "extra"});
        System.setProperty("jdk.httpserver.maxConnections", "-1");
        VanillaBot.configureCallbackLimits();
        check(System.getProperty("jdk.httpserver.maxConnections").equals("16"));
        check(System.getProperty("sun.net.httpserver.maxReqHeaders").equals("32"));
        check(System.getProperty("sun.net.httpserver.maxReqHeaderSize").equals("16384"));
        check(System.getProperty("sun.net.httpserver.maxReqTime").equals("5000"));
        check(System.getProperty("sun.net.httpserver.maxRspTime").equals("5000"));
        check(System.getProperty("sun.net.httpserver.maxIdleConnections").equals("0"));
        System.out.println("VanillaBotTest: 37 checks passed");
    }
    private static void reject(String[] args) {
        try { VanillaBot.arguments(args); throw new AssertionError("invalid args accepted"); }
        catch (IllegalArgumentException expected) { }
    }
    private static void check(boolean value) { if (!value) throw new AssertionError(); }
}
