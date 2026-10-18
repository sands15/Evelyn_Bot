package dev.evelyn.minecraft;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.DataOutputStream;
import java.util.HexFormat;
import java.util.Map;

/** Outer stack framing and atomic component identity; typed syntax is not effective semantics. */
public final class InventoryComponentsTest {
    private static int checks;
    private static void check(boolean value,String label){if(!value)throw new AssertionError(label);checks++;}
    private static byte[] full(byte[] stack)throws IOException {
        ByteArrayOutputStream out=new ByteArrayOutputStream();out.write(new byte[]{0,1,46});
        for(int slot=0;slot<46;slot++)out.write(slot==36?stack:new byte[]{0});out.write(0);return out.toByteArray();
    }
    public static void main(String[] args)throws Exception {
        InventoryState state=new InventoryState();String previous=null;
        for(String hex:new String[]{"010101000307","0101000103","0101010004"}){
            state.accept(0x12,full(HexFormat.of().parseHex(hex)));
            check(state.view().known()&&state.view().slots().get(36).itemId()==1,"official primitive/remove/unit patch establishes full inventory");
            String identity=state.view().slots().get(36).wirePatchIdentity();
            check(identity.length()==64&&!identity.equals(previous),"patch identity is exact wire fingerprint, not prediction hash");previous=identity;
        }
        state.accept(0x12,full(HexFormat.of().parseHex("010101000d010001")));
        check(!state.view().known(),"missing dynamic registry context is unknown, not empty or guessed vanilla");
        state.accept(0x12,full(HexFormat.of().parseHex("010101000d010001")),Map.of("minecraft:enchantment",1));
        check(state.view().known(),"received dynamic registry extent validates membership without inventing semantics");
        for(String hex:new String[]{"0101010003","0101020003070308","01010101030703","010100017a"}){
            state.accept(0x12,full(new byte[]{1,1,0,0}));
            try{state.accept(0x12,full(HexFormat.of().parseHex(hex)));throw new AssertionError("malformed patch accepted");}catch(IOException expected){checks++;}
            check(!state.view().known(),"malformed patch atomically revokes baseline");
        }
        byte[] heavy=heavy();ByteArrayOutputStream large=new ByteArrayOutputStream();large.write(new byte[]{0,1,46});
        for(int slot=0;slot<46;slot++)large.write(slot==36||slot==37?heavy:new byte[]{0});large.write(0);
        try{state.accept(0x12,large.toByteArray());throw new AssertionError("packet-wide node budget bypassed");}
        catch(IOException expected){check("inventory_component_budget".equals(expected.getMessage()),"packet sums separate component node costs");}
        check(!state.view().known(),"packet budget failure publishes no partial full inventory");
        state.accept(0x12,full(heavy));check(state.view().known(),"single bounded large component accepted");
        ByteArrayOutputStream delta=new ByteArrayOutputStream();delta.write(new byte[]{0,2,0,37});delta.write(heavy);
        try{state.accept(0x14,delta.toByteArray());throw new AssertionError("retained inventory budget bypassed");}
        catch(IOException expected){check("inventory_retained_component_budget".equals(expected.getMessage()),"separate packets cannot accumulate unbounded retained structures");}
        check(!state.view().known(),"retained budget failure revokes baseline");
        for(String air:new String[]{"01000000","010001000307"}){
            state.accept(0x12,full(HexFormat.of().parseHex(air)));
            var normalized=state.view().slots().get(36);
            check(state.view().known()&&normalized.count()==0&&normalized.itemId()==-1&&normalized.hash().count()==0,"optional AIR stack normalizes to actual empty even with positive count or a patch");
        }
        state.accept(0x62,HexFormat.of().parseHex("01000000"));
        check(state.view().carried().count()==0&&state.view().carried().hash().count()==0,"AIR cursor is reported as EMPTY, never a nonempty hash");
        for(String patch:new String[]{"010101000d010001","010101002d010001"}){
            byte[] payload=full(HexFormat.of().parseHex(patch));
            state.accept(0x12,payload,Map.of("minecraft:enchantment",1),Map.of("minecraft:enchantment",java.util.List.of("test:alpha")));
            var first=state.view().slots().get(36);
            check(state.view().known()&&first.hash()!=null,"received registry key makes enchantment patch hash available");
            state.accept(0x12,payload,Map.of("minecraft:enchantment",1),Map.of("minecraft:enchantment",java.util.List.of("test:beta")));
            var second=state.view().slots().get(36);
            check(first.wirePatchIdentity().equals(second.wirePatchIdentity())&&!first.hash().equals(second.hash()),"same raw ID and wire bytes use the current exact registry name, never an old hash");
            state.accept(0x12,payload,Map.of("minecraft:enchantment",1),Map.of());
            check(state.view().known()&&state.view().slots().get(36).hash()==null,"missing key keeps known syntax but removes canonical hash authority");
        }
        System.out.println("InventoryComponentsTest PASS "+checks);
    }
    private static byte[] heavy()throws IOException {
        ByteArrayOutputStream bytes=new ByteArrayOutputStream();DataOutputStream out=new DataOutputStream(bytes);
        out.write(new byte[]{1,1,1,0,0,10});
        for(int field=0;field<8;field++){out.writeByte(11);out.writeUTF("v"+field);out.writeInt(4096);for(int n=0;n<4096;n++)out.writeInt(0);}
        out.writeByte(0);return bytes.toByteArray();
    }
}
