package dev.evelyn.minecraft;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;

/** Current connection's packet receipts only, with no terrain map or action-completion authority. */
final class BlockInteractionState {
    static final int BLOCK_STATE_COUNT = 35723;
    static final int MAX_SECTION_CHANGES = 4096;
    static final int MAX_PAYLOAD_BYTES = 8 + 5 + MAX_SECTION_CHANGES * 10;

    sealed interface Observation permits Ack, Destruction, BlockChange, SectionChange {}
    record Position(int x, int y, int z) {}
    record Ack(int sequence) implements Observation {}
    record Destruction(int entityId, Position position, int progress) implements Observation {
        boolean clearsCrack() { return progress > 9; }
    }
    record BlockChange(Position position, int stateId) implements Observation {}
    record Cell(int packedPosition, int stateId) {
        int x() { return packedPosition >>> 8 & 15; }
        int y() { return packedPosition & 15; }
        int z() { return packedPosition >>> 4 & 15; }
    }
    record SectionChange(int x, int y, int z, List<Cell> cells) implements Observation {
        SectionChange { cells = List.copyOf(cells); }
        Position position(Cell cell) { return new Position(x * 16 + cell.x(), y * 16 + cell.y(), z * 16 + cell.z()); }
    }

    private int acknowledgedThrough = -1;
    synchronized int acknowledgedThrough() { return acknowledgedThrough; }
    synchronized void clear() { acknowledgedThrough=-1; }

    synchronized Observation receive(int packetId, byte[] payload) throws IOException {
        if (payload == null || payload.length > MAX_PAYLOAD_BYTES) throw new IOException("block_observation_budget");
        var cursor = new VanillaConnection.Cursor(payload.clone());
        Observation result = switch (packetId) {
            case 0x04 -> {
                int sequence = cursor.count(Integer.MAX_VALUE);
                cursor.end();
                yield new Ack(sequence);
            }
            case 0x05 -> {
                int entityId = cursor.vi();
                long position = cursor.data.readLong();
                int progress = cursor.data.readUnsignedByte();
                cursor.end();
                yield new Destruction(entityId, new Position((int)(position >> 38),
                        (int)(position << 52 >> 52), (int)(position << 26 >> 38)), progress);
            }
            case 0x08 -> {
                var block = ChunkCodec.decodeBlock(cursor);
                stateId(block.stateId());
                yield new BlockChange(new Position(block.x(), block.y(), block.z()), block.stateId());
            }
            case 0x56 -> {
                var section = ChunkCodec.decodeSection(cursor);
                var cells = new ArrayList<Cell>(section.positions().length);
                for (int i = 0; i < section.positions().length; i++) {
                    stateId(section.states()[i]);
                    cells.add(new Cell(section.positions()[i], section.states()[i]));
                }
                yield new SectionChange(section.x(), section.y(), section.z(), cells);
            }
            default -> throw new IOException("unsupported_block_observation");
        };
        // The only retained state is a receipt watermark; it has no action-success meaning.
        if (result instanceof Ack ack) acknowledgedThrough = Math.max(acknowledgedThrough, ack.sequence());
        return result;
    }

    private static void stateId(int stateId) throws IOException {
        if (stateId < 0 || stateId >= BLOCK_STATE_COUNT) throw new IOException("block_state_bounds");
    }
}
