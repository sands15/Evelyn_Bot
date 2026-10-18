package dev.evelyn.minecraft;

import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.util.Arrays;
import java.util.HexFormat;

/** Official 26.3 fixed vectors; no sockets, authentication or Minecraft execution. */
public final class ChunkCodecTest {
    private static int checks;
    public static void main(String[] args) throws Exception {
        fixedUpdateVectors();
        singleValueChunk();
        localPalettesAndPadding();
        biomeRegistryWidths();
        unknownGlobalStillChecksOuter();
        pinnedBlockRegistry();
        malformedBounds();
        lightingAndBlockEntities();
        System.out.println("CHUNK_CODEC_PASS " + checks + " checks (official fixed vectors, memory only)");
    }

    private static void fixedUpdateVectors() throws Exception {
        var block = ChunkCodec.decodeBlock(packet("080000007fffffe04001"));
        equal(1, block.x()); equal(64, block.y()); equal(-2, block.z()); equal(1, block.stateId());
        var section = ChunkCodec.decodeSection(packet("56fffffc00002ffffc01c526"));
        equal(-1, section.x()); equal(-4, section.y()); equal(2, section.z());
        equal(1, section.positions().length); equal(0x345, section.positions()[0]); equal(1, section.states()[0]);
        var unload = ChunkCodec.decodeUnload(packet("2600000003fffffffe"));
        equal(-2, unload.x()); equal(3, unload.z());
        var duplicate = ChunkCodec.decodeSection(packet("56000000000000000002c526c546"));
        equal(0x345, duplicate.positions()[0]); equal(0x345, duplicate.positions()[1]);
        equal(1, duplicate.states()[0]); equal(2, duplicate.states()[1]);
        for (String value : new String[]{"080000007fffffe040", "080000007fffffe040ffffffff0f", "080000007fffffe0400100"})
            rejects(() -> ChunkCodec.decodeBlock(packet(value)));
        for (String value : new String[]{"56fffffc00002ffffc01c5", "560000000000000000ffffffff0f",
                "560000000000000000018080808080808080808002", "560000000000000000018080808080808002"})
            rejects(() -> ChunkCodec.decodeSection(packet(value)));
        rejects(() -> ChunkCodec.decodeUnload(packet("2600000003ffffff")));
        rejects(() -> ChunkCodec.decodeUnload(packet("2600000003fffffffe00")));
    }

    private static void singleValueChunk() throws Exception {
        byte[] fixed = hex("2e00000000000000000008000000000000000000000000000000");
        var result = chunkPacket(fixed, 1, 1);
        equal(0, result.x()); equal(0, result.z()); equal(1, result.sections().length);
        equal(4096, result.sections()[0].length); equal(0, result.sections()[0][0]); equal(0, result.sections()[0][4095]);
        for (int n = 1; n < fixed.length; n++) {
            byte[] truncated = Arrays.copyOf(fixed, n);
            rejects(() -> chunkPacket(truncated, 1, 1));
        }
        rejects(() -> chunkPacket(concat(fixed, new byte[]{0}), 1, 1));
        byte[] lower = hex("0000000000070000");
        byte[] upper = hex("0000000000090000");
        var two = chunk(concat(lower, upper), 2, 1);
        equal(7, two.sections()[0][0]); equal(9, two.sections()[1][4095]);
    }

    private static void localPalettesAndPadding() throws Exception {
        for (int header = 1; header <= 4; header++) {
            byte[] local = localSection(header, 4, new int[]{0, Integer.MAX_VALUE}, 0x10L, false);
            var section = chunk(local, 1, 1).sections()[0];
            equal(0, section[0]); equal(Integer.MAX_VALUE, section[1]); equal(0, section[16]);
        }
        int[] palette = new int[12]; for (int i = 0; i < palette.length; i++) palette[i] = i;
        // Manual official 5-bit packing: entries 0..11 in low bits, four unused high bits.
        var five = chunk(localSection(5, 5, palette, 0xf5a928398a418820L, false), 1, 1).sections()[0];
        for (int i = 0; i < 12; i++) equal(i, five[i]);
        equal(0, five[12]); equal(0, five[4095]);
        for (int bits = 6; bits <= 8; bits++) {
            var section = chunk(localSection(bits, bits, new int[]{0, 19}, 1L << bits, false), 1, 1).sections()[0];
            equal(0, section[0]); equal(19, section[1]);
        }
        rejects(() -> chunk(localSection(4, 4, new int[]{0, 1}, 2L, false), 1, 1));
        rejects(() -> chunk(localSection(4, 4, new int[]{0, 1}, 0L, true), 1, 1));
        rejects(() -> chunk(hex("0000000004000000"), 1, 1));
        rejects(() -> chunk(hex("000000000411"), 1, 1));
        rejects(() -> chunk(hex("0000000000ffffffff0f0000"), 1, 1));
    }

    private static void biomeRegistryWidths() throws Exception {
        for (int bits = 1; bits <= 3; bits++) {
            Buf b = new Buf(); b.raw(hex("000000000000")); b.u8(bits); b.vi(2); b.vi(0); b.vi(1);
            int words = (64 + 64 / bits - 1) / (64 / bits);
            b.i64(1); for (int i = 1; i < words; i++) b.i64(0);
            check(chunk(b.bytes(), 1, 2).sections() != null, "local biome width");
        }
        // Registry size 17 requires five wire bits even when global header says four or 32.
        for (int header : new int[]{4, 5, 32}) {
            Buf b = new Buf(); b.raw(hex("000000000000")); b.u8(header); b.i64(16);
            for (int i = 1; i < 6; i++) b.i64(0);
            check(chunk(b.bytes(), 1, 17).sections() != null, "global biome registry-derived width");
        }
        check(chunk(hex("00000000000004"), 1, 1).sections() != null, "one-entry global biome has zero storage bits");
        Buf invalid = new Buf(); invalid.raw(hex("00000000000004")); invalid.i64(17);
        for (int i = 1; i < 6; i++) invalid.i64(0);
        rejects(() -> chunk(invalid.bytes(), 1, 17));
        rejects(() -> chunk(hex("0000000000000001"), 1, 1));
        rejects(() -> chunk(hex("00000000000021"), 1, 17));
    }

    private static void unknownGlobalStillChecksOuter() throws Exception {
        for (int header : new int[]{9, 15, 32}) {
            byte[] unknown = new byte[]{0, 0, 0, 0, (byte) header};
            check(chunk(unknown, 1, 1).sections() == null, "unsupported global is unknown, not air");
            rejects(() -> decode(chunkBody(unknown, new byte[]{0, 0}), 1, 1));
            rejects(() -> decode(concat(chunkBody(unknown, new byte[7]), new byte[]{1}), 1, 1));
            rejects(() -> decode(chunkBody(unknown, hex("010000000001000000000000")), 1, 1));
        }
        byte[] prefix = concat(hex("0000000000070000"), hex("0000000009ff"));
        check(chunk(prefix, 2, 1).sections() == null, "known prefix never leaks from unknown chunk");
        for (int header : new int[]{33, 127, 128, 255})
            rejects(() -> chunk(new byte[]{0, 0, 0, 0, (byte)header}, 1, 1));
    }

    private static void malformedBounds() throws Exception {
        for (int n : new int[]{0, -1, 255}) rejects(() -> chunk(hex("0000000000000000"), n, 1));
        for (int n : new int[]{0, -1, 65537}) rejects(() -> chunk(hex("0000000000000000"), 1, n));
        rejects(() -> chunk(hex("1001000000000000"), 1, 1));
        rejects(() -> chunk(hex("0000ffff00000000"), 1, 1));
        rejects(() -> chunk(concat(hex("0000000000000000"), new byte[]{0}), 1, 1));
        Buf count = new Buf(); count.i32(0); count.i32(0); count.vi(7);
        rejects(() -> decode(count.bytes(), 1, 1));
        Buf type = new Buf(); type.i32(0); type.i32(0); type.vi(1); type.vi(6); type.vi(0);
        rejects(() -> decode(type.bytes(), 1, 1));
        Buf longs = new Buf(); longs.i32(0); longs.i32(0); longs.vi(1); longs.vi(1); longs.vi(65);
        rejects(() -> decode(longs.bytes(), 1, 1));
        Buf duplicate = new Buf(); duplicate.i32(0); duplicate.i32(0); duplicate.vi(2); duplicate.raw(hex("01000100"));
        rejects(() -> decode(duplicate.bytes(), 1, 1));
        Buf array = new Buf(); array.i32(0); array.i32(0); array.vi(0); array.vi(2097153);
        rejects(() -> decode(array.bytes(), 1, 1));
    }

    private static void pinnedBlockRegistry() throws Exception {
        // Four official 16-bit IDs per long, independent of the global wire header.
        for (int header : new int[]{9, 16, 32}) {
            Buf b = new Buf(); b.i32(0); b.u8(header); b.i64(0x00028b8a00010000L);
            for (int i = 1; i < 1024; i++) b.i64(0);
            b.raw(new byte[]{0, 0});
            int[] states = pinnedChunk(b.bytes(), 35723).sections()[0];
            equal(0, states[0]); equal(1, states[1]); equal(35722, states[2]);
            equal(2, states[3]); equal(0, states[4]); equal(0, states[4095]);
        }
        Buf invalid = new Buf(); invalid.i32(0); invalid.u8(16); invalid.i64(35723);
        for (int i = 1; i < 1024; i++) invalid.i64(0);
        invalid.raw(new byte[]{0, 0});
        rejects(() -> pinnedChunk(invalid.bytes(), 35723));
        rejects(() -> pinnedChunk(hex("0000000010"), 35723));
        for (int id : new int[]{35723, Integer.MAX_VALUE}) {
            rejects(() -> pinnedChunk(localSection(4, 4, new int[]{0, id}, 0, false), 35723));
            Buf single = new Buf(); single.i32(0); single.u8(0); single.vi(id); single.raw(new byte[]{0, 0});
            rejects(() -> pinnedChunk(single.bytes(), 35723));
        }
        for (int count : new int[]{-1, 65537})
            rejects(() -> pinnedChunk(hex("0000000000000000"), count));
        equal(19, pinnedChunk(localSection(4, 4, new int[]{0, 19}, 0x10L, false), 35723).sections()[0][1]);
    }
    private static ChunkCodec.Chunk pinnedChunk(byte[] section, int blocks) throws IOException {
        return ChunkCodec.decodeChunk(new VanillaConnection.Cursor(chunkBody(section, new byte[7])), 1, 1, blocks);
    }

    private static void lightingAndBlockEntities() throws Exception {
        byte[] section = hex("0000000000000000");
        // One block entity: packedXZ=0x12, y=3, type=0, unnamed empty compound 0a00.
        check(decode(chunkBody(section, hex("01120003000a00000000000000")), 1, 1).sections() != null, "optional compound consumed");
        check(decode(chunkBody(section, hex("011200030000000000000000")), 1, 1).sections() != null, "absent compound consumed");
        rejects(() -> decode(chunkBody(section, hex("01120003000100000000000000")), 1, 1));
        Buf light = new Buf(); light.vi(0); light.array(new byte[]{1}); light.array(new byte[0]);
        light.array(new byte[0]); light.array(new byte[0]); light.vi(1); light.array(new byte[2048]); light.vi(0);
        check(decode(chunkBody(section, light.bytes()), 1, 1).sections() != null, "byte-array light mask and exact DataLayer");
        rejects(() -> decode(chunkBody(section, hex("0001080000000000")), 1, 1));
        rejects(() -> decode(chunkBody(section, hex("0001010000000000")), 1, 1));
        rejects(() -> decode(chunkBody(section, hex("000101000101000000")), 1, 1));
        Buf shortLight = new Buf(); shortLight.raw(hex("00010100000001")); shortLight.array(new byte[2047]); shortLight.vi(0);
        rejects(() -> decode(chunkBody(section, shortLight.bytes()), 1, 1));
        Buf tooManyEntities = new Buf(); tooManyEntities.vi(4097);
        rejects(() -> decode(chunkBody(section, tooManyEntities.bytes()), 1, 1));
    }

    private static byte[] localSection(int header, int bits, int[] palette, long first, boolean oldLength) throws IOException {
        Buf b = new Buf(); b.i32(0); b.u8(header); b.vi(palette.length);
        for (int id : palette) b.vi(id);
        int count = (4096 + 64 / bits - 1) / (64 / bits);
        if (oldLength) b.vi(count);
        b.i64(first); for (int i = 1; i < count; i++) b.i64(0);
        b.raw(new byte[]{0, 0}); return b.bytes();
    }
    private static ChunkCodec.Chunk chunk(byte[] section, int count, int biomes) throws IOException {
        return decode(chunkBody(section, new byte[7]), count, biomes);
    }
    private static byte[] chunkBody(byte[] section, byte[] outer) throws IOException {
        Buf b = new Buf(); b.i32(0); b.i32(0); b.vi(0); b.array(section); b.raw(outer); return b.bytes();
    }
    private static ChunkCodec.Chunk decode(byte[] body, int count, int biomes) throws IOException {
        return ChunkCodec.decodeChunk(new VanillaConnection.Cursor(body), count, biomes);
    }
    private static ChunkCodec.Chunk chunkPacket(byte[] packet, int count, int biomes) throws IOException {
        return decode(Arrays.copyOfRange(packet, 1, packet.length), count, biomes);
    }
    private static VanillaConnection.Cursor packet(String packet) {
        byte[] bytes = hex(packet); return new VanillaConnection.Cursor(Arrays.copyOfRange(bytes, 1, bytes.length));
    }
    private static byte[] hex(String value) { return HexFormat.of().parseHex(value); }
    private static byte[] concat(byte[] a, byte[] b) {
        byte[] result = Arrays.copyOf(a, a.length + b.length); System.arraycopy(b, 0, result, a.length, b.length); return result;
    }
    private static void equal(int expected, int actual) { check(expected == actual, "integer mismatch: " + expected + " / " + actual); }
    private static void check(boolean condition, String label) { checks++; if (!condition) throw new AssertionError(label); }
    @FunctionalInterface private interface Checked { void run() throws IOException; }
    private static void rejects(Checked action) throws IOException {
        checks++;
        try { action.run(); } catch (IOException expected) { return; }
        throw new AssertionError("malformed data accepted");
    }
    private static final class Buf {
        final ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        final DataOutputStream data = new DataOutputStream(bytes);
        void u8(int value) throws IOException { data.writeByte(value); }
        void i32(int value) throws IOException { data.writeInt(value); }
        void i64(long value) throws IOException { data.writeLong(value); }
        void vi(int value) throws IOException {
            do { int part = value & 127; value >>>= 7; u8(part | (value == 0 ? 0 : 128)); } while (value != 0);
        }
        void raw(byte[] value) throws IOException { data.write(value); }
        void array(byte[] value) throws IOException { vi(value.length); raw(value); }
        byte[] bytes() { return bytes.toByteArray(); }
    }
}
