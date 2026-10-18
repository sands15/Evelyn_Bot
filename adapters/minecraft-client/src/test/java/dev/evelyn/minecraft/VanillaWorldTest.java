package dev.evelyn.minecraft;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.io.*;
import java.lang.reflect.*;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/** Fixed official-layout packets; memory streams only, never authentication or a game socket. */
public final class VanillaWorldTest {
    private static int checks;
    public static void main(String[] args) throws Exception {
        try (VanillaConnection c = fixture()) {
            ready(c);
            handle(c, position(0, 0, 0));
            int before = c.observation().sequence();
            handle(c, hex("2e00000000000000000008000000000000000000000000000000"));
            JsonObject first = json(c);
            check(first.has("terrain"), "chunk observation is missing");
            check(c.observation().sequence() > before, "chunk-only update advances publication");
            check(first.getAsJsonObject("terrain").get("height").getAsInt() == 16, "nonzero dimension type binds exact metadata");
            check(first.getAsJsonObject("terrain").get("loaded_chunks").getAsInt() == 1, "chunk retained");
            check(state(first, 0, 0, 0).getAsInt() == 0, "raw zero is known");
            check(state(first, -1, 0, 0).isJsonNull(), "missing neighbor is unknown");
            handle(c, hex("00"));
            handle(c, hex("08000000000000000007"));
            handle(c, position(0, 1, 0));
            check(json(c).equals(first), "bundle publishes neither player nor chunk midway");
            handle(c, hex("00"));
            check(c.observation().sequence() == before + 2, "one publication per bundle");
            check(state(json(c), 0, 0, 0).getAsInt() == 7, "block delta committed");
            check(state(first, 0, 0, 0).getAsInt() == 0, "prior projection immutable");
            JsonObject stable = json(c);
            reject(() -> handle(c, hex("2e00000000000000000008000000000000000000000000000000ff")));
            check(json(c).equals(stable), "malformed outer tail cannot commit a valid prefix");
            handle(c, hex("260000000000000000"));
            check(state(json(c), 0, 0, 0).isJsonNull(), "unloaded chunk is unknown");
            handle(c, hex("08000000000000000009"));
            check(state(json(c), 0, 0, 0).isJsonNull(), "late delta cannot resurrect unloaded chunk");
            handle(c, hex("78"));
            check(!json(c).has("terrain") && !c.observation().available(), "reconfiguration clears terrain");
        }
        invalidDimensionMetadata();
        registryFailureDoesNotCommit();
        negativeSectionUpdates();
        globalUnknownReplacesKnown();
        lateChunkCannotReviveClosedConnection();
        System.out.println("VANILLA_WORLD_PASS " + checks + " checks (synthetic memory streams)");
    }

    private static void invalidDimensionMetadata() throws Exception {
        byte[] min = intTag("min_y", 0), height = intTag("height", 16);
        byte[][] invalid = {
            compound(height), compound(min), compound(),
            compound(min, intTag("min_y", 16), height),
            compound(min, height, intTag("height", 32)),
            compound(byteTag("min_y", 0), height),
            compound(min, byteTag("height", 16)),
            compound(height, nested("other", min)),
            compound(min, nested("other", height)),
            compound(nested("other", min, height))
        };
        for (byte[] data : invalid) {
            try (VanillaConnection c = fixture()) {
                reject(() -> handle(c, registryPacket(data)));
                noDimensionMetadata(c);
            }
        }
    }

    private static void registryFailureDoesNotCommit() throws Exception {
        try (VanillaConnection c = fixture()) {
            // A valid first entry must not survive failure in the second entry.
            byte[] packet = registryPacket(dimension(-64, 384), compound(intTag("min_y", 0)));
            reject(() -> handle(c, packet));
            noDimensionMetadata(c);
            ready(c);
            handle(c, position(0, 0, 0));
            check(json(c).getAsJsonObject("terrain").get("height").getAsInt() == 16,
                    "failed registry entry did not reserve or shift the later type ID");
        }
        try (VanillaConnection c = fixture()) {
            byte[] packet = concat(registryPacket(dimension(-64,384), dimension(0,16)), hex("ff"));
            reject(() -> handle(c, packet));
            noDimensionMetadata(c);
            ready(c);
            handle(c, position(0, 0, 0));
            check(json(c).getAsJsonObject("terrain").get("height").getAsInt() == 16,
                    "trailing registry failure permits one subsequent complete registry");
        }
    }

    private static void negativeSectionUpdates() throws Exception {
        try (VanillaConnection c = fixture()) {
            ready(c, -64, 16);
            handle(c, position(-13, -59, 36));
            handle(c, chunkPacket(-1, 2, hex("0000000000000000")));
            check(state(json(c), -13, -59, 36).getAsInt() == 0, "negative chunk and minimum section bound");
            // Official manual vector: section(-1,-4,2), packed local(3,5,4), state 1.
            handle(c, hex("56fffffc00002ffffc01c526"));
            check(state(json(c), -13, -59, 36).getAsInt() == 1, "section coordinates and x/z/y packed order");
            handle(c, hex("56fffffc00002ffffc02c546c566"));
            check(state(json(c), -13, -59, 36).getAsInt() == 3, "duplicate section updates are last-wins");
            JsonObject stable = json(c);
            reject(() -> handle(c, hex("56fffffc00002ffffc01c52600")));
            check(json(c).equals(stable), "section trailing bytes do not commit a valid delta prefix");
            // Chunk(-1,2): big-endian LONG puts z before x on the wire.
            handle(c, hex("2600000002ffffffff"));
            check(state(json(c), -13, -59, 36).isJsonNull(), "negative chunk unload clears the same chunk");
        }
    }

    private static void globalUnknownReplacesKnown() throws Exception {
        try (VanillaConnection c = fixture()) {
            ready(c); handle(c, position(0, 0, 0));
            handle(c, hex("2e00000000000000000008000000000000000000000000000000"));
            check(state(json(c), 0, 0, 0).getAsInt() == 0, "replacement starts with a known chunk");
            byte[] unknown = chunkPacket(0, 0, hex("0000000009"));
            JsonObject stable = json(c);
            reject(() -> handle(c, concat(unknown, hex("ff"))));
            check(json(c).equals(stable), "malformed unknown replacement cannot remove known state");
            int sequence = c.observation().sequence();
            handle(c, unknown);
            JsonObject after = json(c);
            check(state(after, 0, 0, 0).isJsonNull(), "valid global unknown replaces known with unknown");
            check(after.getAsJsonObject("terrain").get("loaded_chunks").getAsInt() == 0, "unknown global evicts retained chunk");
            check(c.observation().sequence() > sequence, "unknown replacement advances observation");
            handle(c, hex("08000000000000000009"));
            check(state(json(c), 0, 0, 0).isJsonNull(), "delta cannot resurrect a global-unknown chunk");
        }
    }

    private static void lateChunkCannotReviveClosedConnection() throws Exception {
        try (VanillaConnection c = fixture()) {
            ready(c); handle(c, position(0, 0, 0));
            byte[] valid = hex("2e00000000000000000008000000000000000000000000000000");
            handle(c, valid);
            int generation = c.observation().generation();
            c.close();
            PlayerObservation.Snapshot closed = c.observation();
            check(closed.generation() > generation && !closed.available(), "close invalidates the prior world generation");
            check(!json(c).has("terrain"), "closed connection has no terrain projection");
            reject(() -> handle(c, valid));
            check(c.observation().equals(closed), "late valid chunk cannot advance or revive closed observation");
            check(c.snapshot().stage() == VanillaConnection.Stage.CLOSED, "late chunk preserves closed lifecycle");
        }
    }

    private static void noDimensionMetadata(VanillaConnection c) throws Exception {
        check(!((Map<?,?>)get(c, "registries")).containsKey("minecraft:dimension_type"), "failed registry count not committed");
        check(((List<?>)get(c, "dimensionTypes")).isEmpty(), "failed dimension metadata not committed");
        check((int)get(c, "registryEntries") == 0, "failed registry entry budget not committed");
        check(!c.observation().available(), "registry rejection does not invent play state");
    }
    private static VanillaConnection fixture() throws Exception {
        VanillaConnection c = new VanillaConnection(new UUID(0, 1), "Synthetic", hash -> { throw new AssertionError("no auth"); });
        set(c,"stage",VanillaConnection.Stage.CONFIGURATION); set(c,"authenticated",true); set(c,"encrypted",true);
        set(c,"out",new ByteArrayOutputStream()); set(c,"in",new ByteArrayInputStream(new byte[0])); return c;
    }
    private static void ready(VanillaConnection c) throws Exception {
        ready(c, 0, 16);
    }
    private static void ready(VanillaConnection c, int minY, int height) throws Exception {
        handle(c, concat(hex("07"), string("minecraft:dimension_type"), hex("02"),
                string("test:first"), dimension(-64,384), string("test:second"), dimension(minY,height)));
        handle(c, concat(hex("07"), string("minecraft:worldgen/biome"), hex("01"), string("test:biome"), hex("010a00")));
        handle(c, hex("03"));
        handle(c, concat(hex("320000002a0001"),string("test:world"),hex("14020200010001"),string("test:world"),hex("00000000000000000100000000003f0101")));
    }
    private static byte[] dimension(int minY, int height) throws IOException {
        ByteArrayOutputStream b=new ByteArrayOutputStream(); DataOutputStream d=new DataOutputStream(b);
        d.writeByte(1); d.writeByte(10); d.writeByte(3); d.writeUTF("min_y"); d.writeInt(minY);
        d.writeByte(3); d.writeUTF("height"); d.writeInt(height); d.writeByte(0); return b.toByteArray();
    }
    private static byte[] intTag(String key, int value) throws IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream(); DataOutputStream d = new DataOutputStream(b);
        d.writeByte(3); d.writeUTF(key); d.writeInt(value); return b.toByteArray();
    }
    private static byte[] byteTag(String key, int value) throws IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream(); DataOutputStream d = new DataOutputStream(b);
        d.writeByte(1); d.writeUTF(key); d.writeByte(value); return b.toByteArray();
    }
    private static byte[] nested(String key, byte[]... tags) throws IOException {
        ByteArrayOutputStream b = new ByteArrayOutputStream(); DataOutputStream d = new DataOutputStream(b);
        d.writeByte(10); d.writeUTF(key); for (byte[] tag : tags) d.write(tag); d.writeByte(0); return b.toByteArray();
    }
    private static byte[] compound(byte[]... tags) {
        ByteArrayOutputStream b = new ByteArrayOutputStream(); b.writeBytes(hex("010a"));
        for (byte[] tag : tags) b.writeBytes(tag); b.write(0); return b.toByteArray();
    }
    private static byte[] registryPacket(byte[]... entries) {
        ByteArrayOutputStream b = new ByteArrayOutputStream();
        b.writeBytes(concat(hex("07"), string("minecraft:dimension_type"), new byte[]{(byte)entries.length}));
        for (int i = 0; i < entries.length; i++) b.writeBytes(concat(string("test:type" + i), entries[i]));
        return b.toByteArray();
    }
    private static byte[] chunkPacket(int x, int z, byte[] sections) {
        return concat(ByteBuffer.allocate(9).put((byte)0x2e).putInt(x).putInt(z).array(),
                new byte[]{0, (byte)sections.length}, sections, new byte[7]);
    }
    private static byte[] position(double x,double y,double z) {
        return ByteBuffer.allocate(62).put((byte)0x49).put((byte)1).putDouble(x).putDouble(y).putDouble(z)
                .putDouble(0).putDouble(0).putDouble(0).putFloat(90).putFloat(0).putInt(0).array();
    }
    private static JsonObject json(VanillaConnection c) { return Protocol.observation(c.observation(),c.observationOriginNanos()); }
    private static com.google.gson.JsonElement state(JsonObject o,int x,int y,int z) {
        JsonArray blocks=o.getAsJsonObject("terrain").getAsJsonArray("blocks");
        for(var item:blocks){ JsonObject b=item.getAsJsonObject(); if(b.get("x").getAsInt()==x && b.get("y").getAsInt()==y && b.get("z").getAsInt()==z)return b.get("state_id"); }
        throw new AssertionError("sample not projected");
    }
    private static void handle(VanillaConnection c,byte[] bytes)throws Exception {
        Method m=VanillaConnection.class.getDeclaredMethod("handlePacket",byte[].class);m.setAccessible(true);
        try{m.invoke(c,(Object)bytes);}catch(InvocationTargetException e){if(e.getCause() instanceof Exception x)throw x;throw (Error)e.getCause();}
    }
    private static void set(Object c,String n,Object v)throws Exception{Field f=VanillaConnection.class.getDeclaredField(n);f.setAccessible(true);f.set(c,v);}
    private static Object get(Object c,String n)throws Exception{Field f=VanillaConnection.class.getDeclaredField(n);f.setAccessible(true);return f.get(c);}
    private static byte[] string(String s){byte[] b=s.getBytes(StandardCharsets.UTF_8);return concat(new byte[]{(byte)b.length},b);}
    private static byte[] concat(byte[]... arrays){ByteArrayOutputStream b=new ByteArrayOutputStream();for(byte[] a:arrays)b.writeBytes(a);return b.toByteArray();}
    private static byte[] hex(String s){return HexFormat.of().parseHex(s);}
    private static void check(boolean ok,String why){if(!ok)throw new AssertionError(why);checks++;}
    private static void reject(Checked action)throws Exception{try{action.run();}catch(IOException|IllegalArgumentException|IllegalStateException e){checks++;return;}throw new AssertionError("malformed packet accepted");}
    @FunctionalInterface private interface Checked {void run()throws Exception;}
}
