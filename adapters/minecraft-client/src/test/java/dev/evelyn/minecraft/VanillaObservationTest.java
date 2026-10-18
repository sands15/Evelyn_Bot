package dev.evelyn.minecraft;

import java.io.*;
import java.lang.reflect.*;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.*;

/** Admission-to-observation integration using official fixed 26.3 vectors and memory streams only. */
public final class VanillaObservationTest {
    private static final UUID PROFILE = new UUID(0, 1);
    private static final Class<VanillaConnection> TYPE = VanillaConnection.class;
    private static Method observation;
    private static Method origin;
    private static int checks;

    public static void main(String[] args) throws Exception {
        try {
            observation = TYPE.getMethod("observation");
            origin = TYPE.getMethod("observationOriginNanos");
        } catch (NoSuchMethodException missing) {
            throw new AssertionError("VanillaConnection observation API is not implemented", missing);
        }
        bindingAndOfficialHealth();
        bundlePublication();
        invalidPacketsDoNotPublish();
        reconfigurationInvalidates();
        closeAndEofInvalidate();
        failedPositionWriteDoesNotObserve();
        System.out.println("VANILLA_OBSERVATION_PASS " + checks + " checks (synthetic memory streams, no server)");
    }
    private static void bindingAndOfficialHealth() throws Exception {
        try (VanillaConnection first = fixture(); VanillaConnection second = fixture()) {
            var initial = observed(first);
            equal(false, initial.available()); equal(0, initial.eventSequence());
            equal(1, initial.generation()); equal(1, initial.sequence());
            equal((long)origin.invoke(first), initial.observedAtNanos());
            check(initial.dimension() == null && initial.position() == null && initial.health() == null, "initial data unknown");
            check(!PROFILE.equals(initial.connectionId()), "connection identity is not profile UUID");
            check(!initial.connectionId().equals(observed(second).connectionId()), "fresh connections have distinct identities");
            ready(first, "minecraft:overworld");
            var player = observed(first);
            equal(initial.connectionId(), player.connectionId()); equal(true, player.available());
            equal("minecraft:overworld", player.dimension()); equal(1, player.eventSequence());
            check(player.position() == null && player.health() == null, "login binding does not invent pose or health");
            check(first.snapshot().packetsReceived() > player.eventSequence(), "state event number is not packet count");
            // Official CB 0x6A: health FLOAT 20, food VAR_INT 20, saturation FLOAT 5.
            handle(first, hex("6a41a000001440a00000"));
            var health = observed(first);
            equal(20f, health.health().health()); equal(20, health.health().food()); equal(5f, health.health().saturation());
            equal(player.eventSequence() + 1, health.eventSequence()); equal(player.sequence() + 1, health.sequence());
            check(health.observedAtNanos() - player.observedAtNanos() >= 0, "accepted event timestamp is monotonic");
            handle(first, position(7, 1, 64, 2));
            var located = observed(first);
            equal(1d, located.position().x()); equal(64d, located.position().y()); equal(2d, located.position().z());
            equal(90f, located.position().yaw()); equal(0f, located.position().pitch());
            check(first.snapshot().initialPositionConfirmed(), "mandatory correction acknowledgement succeeded");
            equal(health.eventSequence() + 1, located.eventSequence());
            handle(first, hex("2d0000000000000001"));
            equal(located, observed(first));
            equal(false, first.snapshot().worldReady()); check(first.capabilities().isEmpty(), "no gameplay capabilities");
        }
    }
    private static void bundlePublication() throws Exception {
        try (VanillaConnection connection = fixture()) {
            ready(connection, "minecraft:overworld"); handle(connection, position(1, 0, 64, 0));
            handle(connection, hex("6a41a000001440a00000"));
            var before = observed(connection);
            handle(connection, hex("00"));
            handle(connection, hex("6a411000000a40000000")); // health 9, food 10, saturation 2
            equal(before, observed(connection));
            handle(connection, position(2, 4, 65, 6)); equal(before, observed(connection));
            handle(connection, hex("2d0000000000000002")); equal(before, observed(connection));
            handle(connection, hex("00"));
            var after = observed(connection);
            equal(before.sequence() + 1, after.sequence()); equal(before.eventSequence() + 2, after.eventSequence());
            equal(9f, after.health().health()); equal(4d, after.position().x()); equal(65d, after.position().y());
            check(after.observedAtNanos() - before.observedAtNanos() >= 0, "bundle uses final source observation time");
            handle(connection, hex("00")); handle(connection, hex("00")); equal(after, observed(connection));
        }
    }
    private static void invalidPacketsDoNotPublish() throws Exception {
        try (VanillaConnection connection = fixture()) {
            configurationDone(connection);
            var unknown = observed(connection);
            reject(() -> handle(connection, hex("6a41a000001440a00000")));
            equal(unknown, observed(connection));
        }
        byte[][] malformed = {
                hex("6a7fc000001440a00000"), // NaN health
                hex("6a41a00000147f800000"), // infinite saturation
                hex("6abf8000001440a00000"), // negative health
                hex("6a41a00000ffffffff0f40a00000"), // negative food
                hex("6a41a0000014"), // truncated float
                hex("6a41a000001440a0000000") // trailing byte
        };
        for (byte[] body : malformed) {
            try (VanillaConnection connection = fixture()) {
                ready(connection, "minecraft:overworld"); var before = observed(connection);
                reject(() -> handle(connection, body)); equal(before, observed(connection));
            }
        }
    }
    private static void reconfigurationInvalidates() throws Exception {
        try (VanillaConnection connection = fixture()) {
            ready(connection, "minecraft:overworld"); handle(connection, position(1, 1, 64, 2));
            handle(connection, hex("6a41a000001440a00000")); var before = observed(connection);
            handle(connection, hex("78")); var unknown = observed(connection);
            assertUnknown(unknown); equal(before.generation() + 1, unknown.generation());
            equal(before.connectionId(), unknown.connectionId()); equal(before.sequence() + 1, unknown.sequence());
            configurationDone(connection); handle(connection, playLogin("minecraft:the_nether"));
            var rebound = observed(connection);
            equal(true, rebound.available()); equal("minecraft:the_nether", rebound.dimension());
            equal(unknown.generation(), rebound.generation());
            check(rebound.position() == null && rebound.health() == null, "old dimension data does not return");
        }
    }
    private static void closeAndEofInvalidate() throws Exception {
        try (VanillaConnection connection = fixture()) {
            ready(connection, "minecraft:overworld"); handle(connection, position(1, 1, 64, 2));
            var before = observed(connection); handle(connection, hex("00"));
            handle(connection, hex("6a3f8000000140000000")); equal(before, observed(connection));
            connection.close(); var closed = observed(connection);
            assertUnknown(closed); equal(before.generation() + 1, closed.generation());
            equal(before.sequence() + 1, closed.sequence());
            reject(() -> handle(connection, position(2, 99, 99, 99)));
            reject(() -> handle(connection, hex("6a41a000001440a00000")));
            reject(() -> handle(connection, playLogin("minecraft:overworld")));
            equal(closed, observed(connection)); connection.close(); equal(closed, observed(connection));
        }
        try (VanillaConnection connection = fixture()) {
            ready(connection, "minecraft:overworld"); handle(connection, position(1, 1, 64, 2));
            var before = observed(connection); handle(connection, hex("00"));
            handle(connection, hex("6a3f8000000140000000"));
            // Empty memory input is EOF, not a socket. Public maintenance must close/invalidate.
            reject(() -> connection.maintain(Duration.ofSeconds(1)));
            assertUnknown(observed(connection)); equal(before.generation() + 1, observed(connection).generation());
            equal(VanillaConnection.Stage.CLOSED, connection.snapshot().stage());
        }
    }
    private static void failedPositionWriteDoesNotObserve() throws Exception {
        try (VanillaConnection connection = fixture()) {
            ready(connection, "minecraft:overworld"); var before = observed(connection);
            set(connection,"out",new OutputStream(){@Override public void write(int b)throws IOException{throw new IOException("synthetic write failure");}});
            reject(() -> handle(connection, position(1, 1, 64, 2)));
            equal(before, observed(connection)); check(!connection.snapshot().initialPositionConfirmed(), "failed acknowledgement does not confirm position");
        }
    }
    private static VanillaConnection fixture() throws Exception {
        VanillaConnection connection = new VanillaConnection(PROFILE,"Probe",hash -> { throw new AssertionError("authentication must not run"); });
        set(connection,"stage",VanillaConnection.Stage.CONFIGURATION); set(connection,"authenticated",true); set(connection,"encrypted",true);
        set(connection,"out",new ByteArrayOutputStream()); set(connection,"in",new ByteArrayInputStream(new byte[0]));
        return connection;
    }
    private static void ready(VanillaConnection connection,String dimension)throws Exception {
        configurationDone(connection);handle(connection,playLogin(dimension));
    }
    private static void configurationDone(VanillaConnection connection)throws Exception {
        // Unnamed compound with root INT min_y=-64, height=384.
        handle(connection,concat(hex("07"),string("minecraft:dimension_type"),hex("01"),string("minecraft:overworld"),hex("010a0300056d696e5f79ffffffc00300066865696768740000018000")));
        handle(connection,hex("03"));
    }
    private static byte[] playLogin(String dimension) {
        // Entity INT, hardcore BOOL, dimension set; three VarInts, three BOOLs;
        // CommonPlayerSpawnInfo incl. raw holder index, then online/secure-chat BOOLs.
        return concat(hex("320000002a0001"),string(dimension),hex("14020200010000"),string(dimension),hex("00000000000000000100000000003f0101"));
    }
    private static byte[] position(int id,double x,double y,double z) {
        return ByteBuffer.allocate(62).put((byte)0x49).put((byte)id).putDouble(x).putDouble(y).putDouble(z)
                .putDouble(0).putDouble(0).putDouble(0).putFloat(90).putFloat(0).putInt(0).array();
    }
    private static PlayerObservation.Snapshot observed(VanillaConnection c)throws Exception{return (PlayerObservation.Snapshot)observation.invoke(c);}
    private static void handle(VanillaConnection c,byte[] bytes)throws Exception{
        Method method=TYPE.getDeclaredMethod("handlePacket",byte[].class);method.setAccessible(true);
        try{method.invoke(c,(Object)bytes);}catch(InvocationTargetException e){if(e.getCause() instanceof Exception x)throw x;throw (Error)e.getCause();}
    }
    private static void set(Object c,String name,Object value)throws Exception{Field f=TYPE.getDeclaredField(name);f.setAccessible(true);f.set(c,value);}
    private static void assertUnknown(PlayerObservation.Snapshot s){
        equal(false,s.available());check(s.dimension()==null && s.position()==null && s.health()==null,"invalidated snapshot has no prior data");
    }
    private static byte[] string(String s){byte[] b=s.getBytes(StandardCharsets.UTF_8);if(b.length>127)throw new AssertionError("fixture length");return concat(new byte[]{(byte)b.length},b);}
    private static byte[] concat(byte[]... arrays){ByteArrayOutputStream out=new ByteArrayOutputStream();for(byte[] a:arrays)out.writeBytes(a);return out.toByteArray();}
    private static byte[] hex(String value){return HexFormat.of().parseHex(value);}
    private static void equal(Object expected,Object actual){check(Objects.equals(expected,actual),"observation integration mismatch");}
    private static void check(boolean condition,String label){if(!condition)throw new AssertionError(label);checks++;}
    private static void reject(Checked action)throws Exception{try{action.run();}catch(IOException|IllegalArgumentException|IllegalStateException expected){checks++;return;}throw new AssertionError("invalid/late packet accepted");}
    @FunctionalInterface private interface Checked{void run()throws Exception;}
}
