package dev.evelyn.minecraft;

import com.google.gson.JsonObject;
import com.google.gson.JsonElement;
import com.google.gson.JsonParser;
import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.util.Arrays;
import java.util.ArrayDeque;
import java.util.HexFormat;
import java.util.Map;

/** Literal vectors come from the pinned official codec probe, never from the reader. */
public final class ComponentWireCodecTest {
    private static int checks;
    private static final String VECTORS="0:0a00,1:01,2:01,3:00,4:,5:00013e4ccccd,6:080000,7:00000000,9:080000,10:0a6d696e6563726166743a,11:00,12:00,13:00,14:010000000000,15:010000000000,16:00,17:00000000,18:0000,19:00,20:,21:00,22:0a00,23:044019999a00,24:3fcccccd01bf050100,25:01010000,28:003f8000000101,29:0100000000,30:00000000404000000000000040a000003e99999a3f800000,32:04470000000101010000a80b,34:,35:0a6d696e6563726166743a,36:00,37:000000003f8000000142b4000000000000003f8000003f800000000000003f800000000000,38:01000000,39:0a00000000000000003f8000000000,40:0106,41:0106,42:00,44:04,45:00,46:00,47:00000000,48:00,49:0a00,50:00,51:00,52:00,53:00000000,54:00000000,55:00,56:00,59:0a00,61:0a00,65:00,67:01,68:090000000000,69:0001,71:0000,72:0000000000000000,73:0a6d696e6563726166743a,74:00,75:00,76:00000000,77:00,78:00,79:00,80:01010000,81:0a00,88:00,91:00,92:00,93:00,94:00,95:00,96:00,97:00,98:00,99:00,108:00,110:00,111:00,114:00,115:00,116:00,120:,121:00";
    private static final String EXTRA_VECTORS="24:3fcccccd01bf050101000000000000,24:3fcccccd01bf0501010101,24:3fcccccd01bf05010102,24:3fcccccd01bf050101030000000000,24:3fcccccd01bf05010104000a6d696e6563726166743a00,16:01000e6d696e6563726166743a746573740000000000000000000000,16:01000e6d696e6563726166743a746573740000000000000000000001,16:01000e6d696e6563726166743a746573740000000000000000000002080000,14:01000000000101000a00,14:0100000000010101090000000000,14:0100000000010102090000000000,14:01000000000101030a00,14:01000000000101040800027b7d,14:01000000000101050a00,14:01000000000101060a00,14:01000000000101070a00,14:01000000000101080a00,14:01000000000101090a00,14:010000000001010a0a00,14:010000000001010b0a00,14:010000000001010c0a00,14:010000000001010d0a00,14:010000000001010e090000000000,14:01000000000100030a00,14:0100000001030700,72:0000000201610200620002610001620000000000";
    private static final String DEEP_VECTORS="25:07020000,77:02000107020000,6:080002c080,24:3fcccccd01bf050101000100002800010101010a000101003f800000,0:0a080003737472000441c0804201000162ff0200017301000a000163000600016440020000000000000c00026c61000000020000000000000001ffffffffffffffff050001663fc000000b000269610000000200000001ffffffff03000169000000010400016c00000000000000010900026c69030000000200000001ffffffff0700026261000000030102ff00";
    public static void main(String[] args)throws Exception {
        holderSetsAndNodes();officialVectors();typedValues();reportedNodes();unionsAndBounds();System.out.println("COMPONENT_WIRE_PASS "+checks);
    }
    private static void officialVectors()throws Exception {
        verifyVectors(VECTORS,86);verifyVectors(EXTRA_VECTORS,26);verifyVectors(DEEP_VECTORS,5);
    }
    private static void verifyVectors(String vectors,int expectedCount)throws Exception {
        int count=0;
        for(String vector:vectors.split(",")){
            String[] parts=vector.split(":",-1);int id=Integer.parseInt(parts[0]);byte[] literal=HexFormat.of().parseHex(parts[1]);
            byte[] framed=new byte[literal.length+5];framed[0]=0x7e;System.arraycopy(literal,0,framed,1,literal.length);
            System.arraycopy(new byte[]{(byte)0xde,(byte)0xad,(byte)0xbe,(byte)0xef},0,framed,literal.length+1,4);byte[] saved=framed.clone();
            var decoded=ComponentWireCodec.decode(id,framed,1);
            check(decoded.start()==1&&decoded.end()==literal.length+1&&decoded.consumed()==literal.length,"exact official value span id="+id);
            check(decoded.value().get("syntax_known").getAsBoolean()&&!decoded.value().get("effective_known").getAsBoolean(),"wire parsing cannot imply effective semantics");
            check(Arrays.equals(saved,framed),"decoder cannot modify source or suffix");
            check(decoded.nodeCount()>=decodedStructureNodes(decoded.value())&&decoded.nodeCount()<=65536,"reported budget conservatively covers every returned structure node id="+id);
            for(int size=0;size<literal.length;size++)reject(id,Arrays.copyOf(literal,size));count++;
        }
        check(count==expectedCount,"all official literal fixtures checked");
    }
    private static void typedValues()throws Exception {
        JsonObject food=ComponentWireCodec.decode(23,HexFormat.of().parseHex("044019999a00"),0).value();
        check(food.get("kind").getAsString().equals("record"),"food is a typed record");
        var fields=food.getAsJsonArray("fields");
        check(fields.get(0).getAsJsonObject().get("name").getAsString().equals("nutrition"),"common field identity retained");
        JsonObject nutrition=fields.get(0).getAsJsonObject().getAsJsonObject("value"),saturation=fields.get(1).getAsJsonObject().getAsJsonObject("value");
        check(nutrition.get("type").getAsString().equals("int32")&&nutrition.get("value").getAsInt()==4,"integer semantic width retained");
        check(saturation.get("type").getAsString().equals("float32")&&saturation.get("bits").getAsString().equals("4019999a"),"float exact width and bits retained");
        var template=ComponentWireCodec.decode(25,new byte[]{7,2,0,0},0);check(template.end()==4,"nested template item-first shape");
        var nested=template.value().getAsJsonArray("fields").get(0).getAsJsonObject().getAsJsonObject("value").getAsJsonArray("fields");
        check(nested.get(0).getAsJsonObject().getAsJsonObject("value").get("id").getAsInt()==7&&nested.get(1).getAsJsonObject().getAsJsonObject("value").get("value").getAsInt()==2,"nested item ID and count cannot be swapped");
        JsonObject tag=ComponentWireCodec.decode(0,HexFormat.of().parseHex("0a030001780000000100"),0).value();
        check(tag.get("tag").getAsInt()==10,"nameless root compound retained");
        JsonObject child=tag.getAsJsonArray("value").get(0).getAsJsonObject().getAsJsonObject("value");
        check(child.get("tag").getAsInt()==3&&child.get("type").getAsString().equals("int32")&&child.get("value").getAsInt()==1,"NBT numeric width retained");
        var dynamic=ComponentWireCodec.decode(8,new byte[]{0},0,Map.of("minecraft:damage_type",1));
        check(dynamic.end()==1&&dynamic.value().get("id").getAsInt()==0,"owned session registry membership can be supplied");
        reject(8,new byte[]{0});
        reject(8,new byte[]{1},Map.of("minecraft:damage_type",1));
        reject(25,new byte[]{1,1,2,0,3,1,3,2});
        reject(25,new byte[]{1,1,1,1,3,1,3});
        byte[] properties=HexFormat.of().parseHex("0000000201610200620002610001620000000000");
        check(ComponentWireCodec.decode(72,properties,0).end()==properties.length,"distinct profile properties containing NUL cannot be conflated");
        byte[] anyPresence=HexFormat.of().parseHex("01000000000100030a00");
        check(ComponentWireCodec.decode(14,anyPresence,0).end()==anyPresence.length,"any-value predicate uses NBT unit rather than the component value stream");
    }
    private static void reportedNodes()throws Exception {
        check(ComponentWireCodec.decode(4,new byte[0],0).nodeCount()==1,"zero-wire units still consume a packet structure node");
        check(ComponentWireCodec.decode(23,HexFormat.of().parseHex("044019999a00"),0).nodeCount()==8,"food record, field array, three field objects and three scalar nodes are reported");
        check(ComponentWireCodec.decode(0,HexFormat.of().parseHex("0a030001780000000100"),0).nodeCount()==4,"NBT compound, field array, field object and scalar child are reported");
        byte[] array=HexFormat.of().parseHex("07000000030102ff");
        var first=ComponentWireCodec.decode(0,array,0);var second=ComponentWireCodec.decode(0,array,0);
        check(first.nodeCount()==5,"NBT array includes all three raw element nodes");
        check(first.nodeCount()+second.nodeCount()==10,"independent decoded values expose counts for packet-global accumulation");
    }
    private static void holderSetsAndNodes()throws Exception {
        check(decodedStructureNodes(JsonParser.parseString("{\"kind\":\"root\",\"values\":[{\"kind\":\"child\"},1,2]}"))==5,"independent output counter includes nested arrays and raw entries");
        byte[] excessive=toolHolders(52,1286);check(excessive.length==127303,"reviewer's original holder-set wire counterexample preserved");reject(28,excessive);
        for(byte[] normal:new byte[][]{toolHolders(1,2),toolHolders(50,1286)}){
            var decoded=ComponentWireCodec.decode(28,normal,0);
            check(decoded.end()==normal.length,"small and near-limit direct HolderSets preserve exact consumption");
            check(decoded.nodeCount()>=decodedStructureNodes(decoded.value())&&decoded.nodeCount()<=65536,"direct registry helpers cannot escape the global structure budget");
        }
        byte[] inline=HexFormat.of().parseHex("3fcccccd01000a6d696e6563726166743a000100");
        var decoded=ComponentWireCodec.decode(24,inline,0);
        check(decoded.end()==inline.length&&decoded.nodeCount()>=decodedStructureNodes(decoded.value()),"inline registry record fields are fully counted");
    }
    private static byte[] toolHolders(int rules,int holders)throws IOException {
        ByteArrayOutputStream bytes=new ByteArrayOutputStream();varint(bytes,rules);
        for(int rule=0;rule<rules;rule++){varint(bytes,holders+1);for(int id=0;id<holders;id++)varint(bytes,id);bytes.write(0);bytes.write(0);}
        bytes.writeBytes(new byte[]{0x3f,(byte)0x80,0,0,1,1});return bytes.toByteArray();
    }
    private static void varint(ByteArrayOutputStream bytes,int value){while((value&~127)!=0){bytes.write((value&127)|128);value>>>=7;}bytes.write(value);}
    /** Independent output-tree walk counts objects, arrays and raw scalar collection entries. */
    private static int decodedStructureNodes(JsonElement root){
        ArrayDeque<JsonElement> pending=new ArrayDeque<>();pending.push(root);int count=0;
        while(!pending.isEmpty()){
            JsonElement current=pending.pop();count++;
            if(current.isJsonObject()){
                for(var entry:current.getAsJsonObject().entrySet())if(entry.getValue().isJsonObject()||entry.getValue().isJsonArray())pending.push(entry.getValue());
            }else if(current.isJsonArray())for(JsonElement value:current.getAsJsonArray())pending.push(value);
        }
        return count;
    }
    private static void unionsAndBounds()throws Exception {
        reject(-1,new byte[]{0});reject(122,new byte[]{0});reject(12,new byte[]{4});reject(21,new byte[]{2});
        reject(1,new byte[]{(byte)0x80,(byte)0x80,(byte)0x80,(byte)0x80,(byte)0x80,0});
        reject(1,new byte[]{(byte)0xff,(byte)0xff,(byte)0xff,(byte)0xff,0x10});
        reject(7,HexFormat.of().parseHex("7f800000"));reject(6,HexFormat.of().parseHex("080001c0"));
        reject(11,new byte[]{(byte)0x81,2});reject(0,new byte[]{13});
        reject(10,new byte[]{1,(byte)0xff});
        reject(0,HexFormat.of().parseHex("0a0100017801010001780000"));
        byte[] unknownEffect=HexFormat.of().parseHex("3fcccccd01bf05010105");reject(24,unknownEffect);
        byte[] effects=HexFormat.of().parseHex("3fcccccd01bf05010102");
        check(ComponentWireCodec.decode(24,effects,0).end()==effects.length,"clear-all consume effect branch has zero payload");
        ByteArrayOutputStream deep=new ByteArrayOutputStream();deep.write(10);for(int n=0;n<34;n++)deep.writeBytes(new byte[]{10,0,1,120});for(int n=0;n<35;n++)deep.write(0);reject(0,deep.toByteArray());
        ByteArrayOutputStream arrays=new ByteArrayOutputStream();DataOutputStream data=new DataOutputStream(arrays);data.writeByte(10);
        for(int n=0;n<17;n++){data.writeByte(7);data.writeUTF("a"+n);data.writeInt(4096);data.write(new byte[4096]);}data.writeByte(0);
        reject(0,arrays.toByteArray());
        ByteArrayOutputStream recursive=new ByteArrayOutputStream();recursive.writeBytes(HexFormat.of().parseHex("3fcccccd01bf050101000100"));
        for(int n=0;n<20;n++)recursive.writeBytes(new byte[]{0,1,0,1,1,1});recursive.writeBytes(new byte[]{0,1,0,1,1,0});recursive.writeBytes(HexFormat.of().parseHex("3f800000"));reject(24,recursive.toByteArray());
        reject(0,new byte[1048577]);
        try{ComponentWireCodec.decode(1,new byte[]{1},2);throw new AssertionError("invalid offset accepted");}catch(IOException expected){checks++;}
    }
    private static void reject(int id,byte[] data)throws Exception{reject(id,data,Map.of());}
    private static void reject(int id,byte[] data,Map<String,Integer> registrySizes)throws Exception{try{ComponentWireCodec.decode(id,data,0,registrySizes);}catch(IOException expected){checks++;return;}throw new AssertionError("malformed component accepted id="+id);}
    private static void check(boolean condition,String reason){if(!condition)throw new AssertionError(reason);checks++;}
}
