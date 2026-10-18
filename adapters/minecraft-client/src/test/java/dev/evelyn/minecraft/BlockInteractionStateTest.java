package dev.evelyn.minecraft;

import java.io.*;
import java.util.*;

public final class BlockInteractionStateTest {
    private static int checks;
    private static final HexFormat HEX = HexFormat.of();
    private static void eq(Object actual, Object expected, String label) {
        checks++; if (!Objects.equals(actual, expected)) throw new AssertionError(label + ": " + actual + " != " + expected);
    }
    private static BlockInteractionState.Observation receive(BlockInteractionState state, int id, String hex) {
        try { return state.receive(id, HEX.parseHex(hex)); }
        catch (IOException e) { throw new AssertionError("required decoder missing or rejected valid literal id=" + id, e); }
    }
    private static void reject(BlockInteractionState state, int id, byte[] payload, String label) {
        int prior = state.acknowledgedThrough();
        try { state.receive(id, payload); throw new AssertionError("accepted " + label); }
        catch (IOException expected) { checks++; }
        eq(state.acknowledgedThrough(), prior, "invalid atomic " + label);
    }
    private static void reject(BlockInteractionState state, int id, String hex, String label) {
        reject(state, id, HEX.parseHex(hex), label);
    }
    private static void pos(BlockInteractionState.Position p, int x, int y, int z, String label) {
        eq(p, new BlockInteractionState.Position(x,y,z), label);
    }
    private static void vi(ByteArrayOutputStream out, long value) {
        do { int b=(int)(value & 127); value >>>=7; out.write(b | (value==0 ? 0:128)); } while(value!=0);
    }
    private static byte[] section(int count, long value) {
        var out=new ByteArrayOutputStream(); out.writeBytes(new byte[8]); vi(out,count);
        for(int i=0;i<count;i++)vi(out,value); return out.toByteArray();
    }
    // Independent reserialization for literal roundtrips, never used to derive expected fields.
    private static String encode(BlockInteractionState.Observation observation) throws IOException {
        var out=new ByteArrayOutputStream(); var data=new DataOutputStream(out);
        if(observation instanceof BlockInteractionState.Ack ack) vi(out,ack.sequence());
        else if(observation instanceof BlockInteractionState.Destruction d) {
            vi(out,Integer.toUnsignedLong(d.entityId())); data.writeLong(packBlock(d.position())); data.writeByte(d.progress());
        } else if(observation instanceof BlockInteractionState.BlockChange b) {
            data.writeLong(packBlock(b.position())); vi(out,b.stateId());
        } else if(observation instanceof BlockInteractionState.SectionChange s) {
            data.writeLong(((long)s.x()&0x3fffff)<<42 | ((long)s.z()&0x3fffff)<<20 | ((long)s.y()&0xfffff));
            vi(out,s.cells().size()); for(var c:s.cells())vi(out,((long)c.stateId()<<12)|c.packedPosition());
        } else throw new AssertionError();
        return HEX.formatHex(out.toByteArray());
    }
    private static long packBlock(BlockInteractionState.Position p) {
        return ((long)p.x()&0x3ffffff)<<38 | ((long)p.z()&0x3ffffff)<<12 | ((long)p.y()&0xfff);
    }
    private static void round(BlockInteractionState state,int id,String literal) throws IOException {
        eq(encode(receive(state,id,literal)),literal,"literal roundtrip " + id);
    }
    public static void main(String[] args) throws Exception {
        var state=new BlockInteractionState(); eq(state.acknowledgedThrough(),-1,"initial no ACK");
        eq(receive(state,0x04,"ac02"),new BlockInteractionState.Ack(300),"ACK literal 300");
        eq(state.acknowledgedThrough(),300,"ACK cumulative max");
        receive(state,0x04,"01"); eq(state.acknowledgedThrough(),300,"older ACK no regression");
        receive(state,0x04,"ac02"); eq(state.acknowledgedThrough(),300,"duplicate ACK");
        receive(state,0x04,"00"); eq(state.acknowledgedThrough(),300,"zero ACK");
        var atomic=new BlockInteractionState();receive(atomic,0x04,"01");
        reject(atomic,0x04,"ac0200","newer ACK with trailing data must not advance watermark");
        eq(atomic.acknowledgedThrough(),1,"ACK commits only after full consumption");
        round(state,0x04,"ffffffff07"); eq(state.acknowledgedThrough(),Integer.MAX_VALUE,"max ACK");

        var crack=(BlockInteractionState.Destruction)receive(state,0x05,"7b000000000000000004");
        eq(crack.entityId(),123,"breaker ID"); pos(crack.position(),0,0,0,"crack origin");
        eq(crack.progress(),4,"stage4"); eq(crack.clearsCrack(),false,"stage does not clear");
        var clear=(BlockInteractionState.Destruction)receive(state,0x05,"7bffffffffffffffffff");
        pos(clear.position(),-1,-1,-1,"negative crack position"); eq(clear.progress(),255,"clear unsigned255"); eq(clear.clearsCrack(),true,"clear signal");
        for(int progress=0;progress<256;progress++) {
            var d=(BlockInteractionState.Destruction)receive(state,0x05,"000000000000000000"+String.format("%02x",progress));
            eq(d.progress(),progress,"unsigned progress");eq(d.clearsCrack(),progress>9,"out of range clears");
        }
        var negativeEntity=(BlockInteractionState.Destruction)receive(state,0x05,"ffffffff0f000000000000000000");
        eq(negativeEntity.entityId(),-1,"exact signed entity wire field");
        var minimum=(BlockInteractionState.BlockChange)receive(state,0x08,"800000200000080001");
        pos(minimum.position(),-33554432,-2048,-33554432,"minimum signed block XYZ");eq(minimum.stateId(),1,"stone");
        var maximum=(BlockInteractionState.BlockChange)receive(state,0x08,"7fffffdffffff7ff8a9702");
        pos(maximum.position(),33554431,2047,33554431,"maximum signed block XYZ");eq(maximum.stateId(),35722,"last catalog state");
        var axis=(BlockInteractionState.BlockChange)receive(state,0x08,"0000007fffffdffe00");
        pos(axis.position(),1,-2,-3,"asymmetric block axes");eq(axis.stateId(),0,"air");

        var sec=(BlockInteractionState.SectionChange)receive(state,0x56,"000000000000000001b222");
        eq(sec.x(),0,"section X");eq(sec.y(),0,"section Y");eq(sec.z(),0,"section Z");eq(sec.cells().size(),1,"one cell");
        var cell=sec.cells().getFirst();eq(cell.packedPosition(),0x132,"packed XZY");eq(cell.x(),1,"local X");eq(cell.y(),2,"local Y");eq(cell.z(),3,"local Z");
        eq(cell.stateId(),1,"section stone");pos(sec.position(cell),1,2,3,"section world coordinate");
        var minSection=(BlockInteractionState.SectionChange)receive(state,0x56,"80000200000800000100");
        eq(minSection.x(),-2097152,"min section X");eq(minSection.y(),-524288,"min section Y");eq(minSection.z(),-2097152,"min section Z");
        pos(minSection.position(minSection.cells().getFirst()),-33554432,-8388608,-33554432,"min section block coordinate uses full signed20 Y");
        var maxSection=(BlockInteractionState.SectionChange)receive(state,0x56,"7ffffdfffff7ffff01ffdfe245");
        eq(maxSection.x(),2097151,"max section X");eq(maxSection.y(),524287,"max section Y");eq(maxSection.z(),2097151,"max section Z");
        pos(maxSection.position(maxSection.cells().getFirst()),33554431,8388607,33554431,"max section block coordinate");eq(maxSection.cells().getFirst().stateId(),35722,"max section state");
        var negativeSection=(BlockInteractionState.SectionChange)receive(state,0x56,"ffffffffffffffff01b222");
        pos(negativeSection.position(negativeSection.cells().getFirst()),-15,-14,-13,"negative section local axes");
        var duplicates=(BlockInteractionState.SectionChange)receive(state,0x56,"000000000000000002b222b242");
        eq(duplicates.cells(),List.of(new BlockInteractionState.Cell(0x132,1),new BlockInteractionState.Cell(0x132,2)),"duplicate positions preserve wire order");
        eq(((BlockInteractionState.SectionChange)receive(state,0x56,"000000000000000000")).cells(),List.of(),"empty section legal");
        var capped=(BlockInteractionState.SectionChange)state.receive(0x56,section(4096,0));eq(capped.cells().size(),4096,"node bound inclusive");
        try { sec.cells().clear();throw new AssertionError("mutable cells"); } catch(UnsupportedOperationException expected) { checks++; }
        var mutable=new ArrayList<BlockInteractionState.Cell>(List.of(cell)); var copied=new BlockInteractionState.SectionChange(0,0,0,mutable);mutable.clear();eq(copied.cells(),List.of(cell),"constructor defensive copy");
        byte[] input=HEX.parseHex("000000000000000001b222");var retained=(BlockInteractionState.SectionChange)state.receive(0x56,input);Arrays.fill(input,(byte)0);eq(retained.cells(),List.of(cell),"input not retained");

        for(String hex:List.of("", "80", "8000", "808080808000", "ffffffff0f", "8080808010", "ac0200"))reject(state,0x04,hex,"invalid ACK "+hex);
        reject(state,0x05,"7b0000000000000000","missing progress");reject(state,0x05,"7b00000000000000000400","trailing destruction");
        reject(state,0x08,"00000000000000008b9702","unknown block state");reject(state,0x08,"0000000000000000ffffffff0f","negative block state");
        reject(state,0x08,"00000000000000000100","trailing block");reject(state,0x08,"0000000000000000","missing block state");
        reject(state,0x56,"00000000000000008120","section count4097");reject(state,0x56,"0000000000000000ffffffff0f","negative section count");
        reject(state,0x56,"000000000000000001ffffe245","unknown section state");reject(state,0x56,"000000000000000001ffffffffffffffffff01","negative VarLong");
        reject(state,0x56,"000000000000000002b222ffffe245","valid first cell then unknown state cannot return a partial observation");
        reject(state,0x56,"00000000000000000180808080808080808002","overflow VarLong");reject(state,0x56,"000000000000000002b222","truncated final cell");
        reject(state,0x56,"00000000000000000000","trailing section");reject(state,0x56,new byte[BlockInteractionState.MAX_PAYLOAD_BYTES+1],"payload bound");
        reject(state,0x03,"00","unknown packet");reject(state,0x04,(byte[])null,"null payload");
        for(int id:List.of(0x04,0x05,0x08,0x56)) {
            String literal=switch(id){case 0x04->"ac02";case 0x05->"7b000000000000000004";case 0x08->"800000200000080001";default->"000000000000000001b222";};
            byte[] bytes=HEX.parseHex(literal);for(int length=0;length<bytes.length;length++)reject(state,id,Arrays.copyOf(bytes,length),"all truncations "+id+"/"+length);
            round(state,id,literal);
        }
        for(String literal:List.of("ffffffffffffffff01b222","80000200000800000100","7ffffdfffff7ffff01ffdfe245","000000000000000002b222b242"))round(state,0x56,literal);
        eq(state.acknowledgedThrough(),Integer.MAX_VALUE,"non ACK observations cannot complete or alter receipt max");
        eq(new BlockInteractionState().acknowledgedThrough(),-1,"new session instance has no prior receipt");
        state.clear();eq(state.acknowledgedThrough(),-1,"closed connection clears prior receipt");
        System.out.println("BLOCK_OBSERVER_CANDIDATE_PASS "+checks);
    }
}
