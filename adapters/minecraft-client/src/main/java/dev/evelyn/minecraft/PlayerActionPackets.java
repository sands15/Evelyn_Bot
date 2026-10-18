package dev.evelyn.minecraft;

import java.nio.ByteBuffer;
import java.util.Arrays;

/** Exact 26.3 packet payloads only; the connection owner authorizes sends and verifies effects. */
final class PlayerActionPackets {
    private PlayerActionPackets() { }

    record Packet(int id, byte[] payload) {
        Packet { payload = payload.clone(); }
        @Override public byte[] payload() { return payload.clone(); }
    }

    enum DigAction {
        START_DESTROY_BLOCK(0), CHANGE_DESTROY_DIRECTION(1), ABORT_DESTROY_BLOCK(2), STOP_DESTROY_BLOCK(3),
        DROP_ALL_ITEMS(4), DROP_ITEM(5), RELEASE_USE_ITEM(6), SWAP_ITEM_WITH_OFFHAND(7), STAB(8);
        private final int wireId;
        DigAction(int wireId) { this.wireId = wireId; }
    }

    static Packet selectSlot(int slot) {
        range(slot, 0, 8);
        return encoded(0x36, ByteBuffer.allocate(2).putShort((short) slot));
    }

    static Packet useItem(int hand, int sequence, float yaw, float pitch) {
        range(hand, 0, 1); sequence(sequence);
        if (!Float.isFinite(yaw) || Math.abs(yaw) > 360 || !Float.isFinite(pitch) || Math.abs(pitch) > 90) throw invalid();
        ByteBuffer out = ByteBuffer.allocate(14).put((byte) hand);
        varInt(out, sequence);
        return encoded(0x43, out.putFloat(yaw).putFloat(pitch));
    }

    static Packet useBlock(int x, int y, int z, int face, int hand, float hitX, float hitY, float hitZ,
                           boolean inside, boolean worldBorderHit, int sequence) {
        long pos = position(x, y, z);
        range(face, 0, 5); range(hand, 0, 1); sequence(sequence);
        for (float hit : new float[]{hitX, hitY, hitZ}) if (!Float.isFinite(hit) || hit < 0 || hit > 1) throw invalid();
        ByteBuffer out = ByteBuffer.allocate(29).put((byte) hand).putLong(pos).put((byte) face)
            .putFloat(hitX).putFloat(hitY).putFloat(hitZ).put((byte) (inside ? 1 : 0)).put((byte) (worldBorderHit ? 1 : 0));
        varInt(out, sequence);
        return encoded(0x42, out);
    }

    static Packet dig(DigAction action, int x, int y, int z, int face, int sequence) {
        if (action == null) throw invalid();
        long pos = position(x, y, z);
        range(face, 0, 5); sequence(sequence);
        ByteBuffer out = ByteBuffer.allocate(15).put((byte) action.wireId).putLong(pos).put((byte) face);
        varInt(out, sequence);
        return encoded(0x29, out);
    }

    static Packet releaseUse(int sequence) { return dig(DigAction.RELEASE_USE_ITEM, 0, 0, 0, 0, sequence); }
    static Packet swapHands(int sequence) { return dig(DigAction.SWAP_ITEM_WITH_OFFHAND, 0, 0, 0, 0, sequence); }
    static Packet drop(boolean all, int sequence) { return dig(all ? DigAction.DROP_ALL_ITEMS : DigAction.DROP_ITEM, 0, 0, 0, 0, sequence); }

    static Packet closeContainer(int containerId) {
        if (containerId < 0) throw invalid();
        ByteBuffer out = ByteBuffer.allocate(5);
        varInt(out, containerId);
        return encoded(0x13, out);
    }

    static Packet playerInput(boolean forward, boolean backward, boolean left, boolean right, boolean jump, boolean shift, boolean sprint) {
        int flags = (forward ? 1 : 0) | (backward ? 2 : 0) | (left ? 4 : 0) | (right ? 8 : 0)
            | (jump ? 16 : 0) | (shift ? 32 : 0) | (sprint ? 64 : 0);
        return encoded(0x2b, ByteBuffer.allocate(1).put((byte) flags));
    }

    private static long position(int x, int y, int z) {
        range(x, -30_000_000, 30_000_000); range(y, -2048, 2047); range(z, -30_000_000, 30_000_000);
        return ((long) x & 0x3ffffffL) << 38 | ((long) z & 0x3ffffffL) << 12 | ((long) y & 0xfffL);
    }

    private static void sequence(int sequence) { if (sequence < 0) throw invalid(); }
    private static void range(int value, int min, int max) { if (value < min || value > max) throw invalid(); }
    private static IllegalArgumentException invalid() { return new IllegalArgumentException("player_action_parameters_invalid"); }
    private static void varInt(ByteBuffer out, int value) {
        while ((value & ~0x7f) != 0) { out.put((byte) ((value & 0x7f) | 0x80)); value >>>= 7; }
        out.put((byte) value);
    }
    private static Packet encoded(int id, ByteBuffer out) { return new Packet(id, Arrays.copyOf(out.array(), out.position())); }
}
