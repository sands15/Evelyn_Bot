package dev.evelyn.minecraft;

import java.io.IOException;
import java.util.Arrays;
import java.util.BitSet;

/** Bounded 26.3 read-only codecs, derived from the SHA1-pinned official client artifact. */
final class ChunkCodec {
    // These two retention-free skip limits are adapter policy, not registry membership claims.
    private static final int MAX_BLOCK_ENTITIES = 4096, MAX_HEIGHTMAP_LONGS = 64;
    record Chunk(int x, int z, int[][] sections) {}
    record BlockUpdate(int x, int y, int z, int stateId) {}
    /** Section coordinates; duplicate packed positions retain wire order for last-wins application. */
    record SectionUpdate(int x, int y, int z, int[] positions, int[] states) {}
    record ChunkPos(int x, int z) {}

    private ChunkCodec() {}

    /** Cursor starts after the packet ID. Null sections means the entire chunk is unknown. */
    static Chunk decodeChunk(VanillaConnection.Cursor c, int sectionCount, int biomeCount) throws IOException {
        return decodeChunk(c, sectionCount, biomeCount, 0);
    }
    static Chunk decodeChunk(VanillaConnection.Cursor c, int sectionCount, int biomeCount, int blockCount) throws IOException {
        if (sectionCount < 1 || sectionCount > 254 || biomeCount < 1 || biomeCount > 65536)
            throw invalid("chunk_registry_bounds");
        if (blockCount < 0 || blockCount > 65536) throw invalid("chunk_registry_bounds");
        int x = c.data.readInt(), z = c.data.readInt();
        int heightmaps = c.count(6), seen = 0;
        for (int i = 0; i < heightmaps; i++) {
            int type = c.count(5);
            if ((seen & (1 << type)) != 0) throw invalid("duplicate_heightmap");
            seen |= 1 << type;
            c.skip((long)c.count(MAX_HEIGHTMAP_LONGS) * 8);
        }
        VanillaConnection.Cursor section = new VanillaConnection.Cursor(c.array(2097152));
        int[][] sections = new int[sectionCount][];
        for (int i = 0; i < sectionCount; i++) {
            int blocks = section.data.readShort(), fluids = section.data.readShort();
            if (blocks < 0 || blocks > 4096 || fluids < 0 || fluids > 4096)
                throw invalid("section_count_bounds");
            sections[i] = palette(section, 4096, blockCount, true);
            if (sections[i] == null) { sections = null; break; }
            palette(section, 64, biomeCount, false);
        }
        // A global block palette cannot be sized without the audited client registry count.
        // Its isolated byte array is abandoned; outer packet structure must still validate.
        if (sections != null) section.end();
        int entities = c.count(MAX_BLOCK_ENTITIES);
        for (int i = 0; i < entities; i++) {
            c.data.readUnsignedByte(); c.data.readShort(); c.count(Integer.MAX_VALUE);
            int tag = c.data.readUnsignedByte();
            if (tag == 10) c.nbt(tag, 0);
            else if (tag != 0) throw invalid("invalid_block_entity_nbt");
        }
        int lightSections = sectionCount + 2;
        BitSet sky = lightMask(c, lightSections), block = lightMask(c, lightSections);
        BitSet emptySky = lightMask(c, lightSections), emptyBlock = lightMask(c, lightSections);
        if (sky.intersects(emptySky) || block.intersects(emptyBlock)) throw invalid("overlapping_light_masks");
        lightLayers(c, sky.cardinality()); lightLayers(c, block.cardinality());
        c.end();
        return new Chunk(x, z, sections);
    }

    static BlockUpdate decodeBlock(VanillaConnection.Cursor c) throws IOException {
        long pos = c.data.readLong();
        int state = c.count(Integer.MAX_VALUE); c.end();
        return new BlockUpdate((int)(pos >> 38), (int)(pos << 52 >> 52), (int)(pos << 26 >> 38), state);
    }

    static SectionUpdate decodeSection(VanillaConnection.Cursor c) throws IOException {
        long section = c.data.readLong();
        int count = c.count(4096);
        int[] positions = new int[count], states = new int[count];
        for (int i = 0; i < count; i++) {
            long value = varLong(c);
            if (value < 0 || (value >>> 12) > Integer.MAX_VALUE) throw invalid("block_state_bounds");
            positions[i] = (int)(value & 4095); states[i] = (int)(value >>> 12);
        }
        c.end();
        return new SectionUpdate((int)(section >> 42), (int)(section << 44 >> 44),
                (int)(section << 22 >> 42), positions, states);
    }

    static ChunkPos decodeUnload(VanillaConnection.Cursor c) throws IOException {
        long pos = c.data.readLong(); c.end(); return new ChunkPos((int)pos, (int)(pos >>> 32));
    }

    private static int[] palette(VanillaConnection.Cursor c, int entries, int registrySize, boolean blocks) throws IOException {
        int header = c.data.readUnsignedByte();
        if (header > 32) throw invalid("palette_header_bounds");
        if (header == 0) {
            int id = c.count(blocks && registrySize == 0 ? Integer.MAX_VALUE : registrySize - 1);
            int[] values = new int[entries]; Arrays.fill(values, id); return values;
        }
        if (blocks && header > 8 && registrySize == 0) return null;
        boolean local = header <= (blocks ? 8 : 3);
        int bits = local ? (blocks ? Math.max(4, header) : header)
                : 32 - Integer.numberOfLeadingZeros(registrySize - 1);
        int[] ids = null;
        if (local) {
            int count = c.count(1 << bits);
            if (count == 0) throw invalid("empty_palette");
            ids = new int[count];
            for (int i = 0; i < count; i++) ids[i] = c.count(blocks && registrySize == 0 ? Integer.MAX_VALUE : registrySize - 1);
        }
        int[] values = new int[entries];
        if (bits == 0) return values;
        int perLong = 64 / bits;
        long mask = (1L << bits) - 1;
        int offset = 0;
        while (offset < entries) {
            long word = c.data.readLong();
            for (int slot = 0; slot < perLong && offset < entries; slot++, offset++) {
                int value = (int)((word >>> (slot * bits)) & mask);
                if (local) {
                    if (value >= ids.length) throw invalid("palette_index_bounds");
                    value = ids[value];
                } else if (value >= registrySize) throw invalid("biome_id_bounds");
                values[offset] = value;
            }
        }
        return values;
    }

    private static BitSet lightMask(VanillaConnection.Cursor c, int bits) throws IOException {
        BitSet mask = BitSet.valueOf(c.array((bits + 7) / 8));
        if (mask.length() > bits) throw invalid("light_mask_bounds");
        return mask;
    }

    private static void lightLayers(VanillaConnection.Cursor c, int expected) throws IOException {
        if (c.count(expected) != expected) throw invalid("light_layer_count");
        for (int i = 0; i < expected; i++) {
            if (c.count(2048) != 2048) throw invalid("light_layer_size");
            c.skip(2048);
        }
    }

    private static long varLong(VanillaConnection.Cursor c) throws IOException {
        long result = 0;
        for (int i = 0; i < 10; i++) {
            int part = c.data.readUnsignedByte();
            if (i == 9 && (part & 0xfe) != 0) throw invalid("varlong_overflow");
            result |= (long)(part & 127) << (i * 7);
            if ((part & 128) == 0) return result;
        }
        throw invalid("varlong_overflow");
    }

    private static VanillaConnection.Failure invalid(String code) { return new VanillaConnection.Failure(code); }
}
