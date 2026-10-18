package dev.evelyn.minecraft;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Arrays;
import java.util.HexFormat;

public final class MenuPropertiesTest {
    static int checks;
    static final int[] TOTAL={45,54,63,72,81,90,45,46,39,37,39,41,46,38,39,39,41,1,40,39,63,40,39,39,38};
    // Fixture independently audited against all 25 official26.3 menu constructors/parents and addDataSlot(s).
    static final int[] COUNT={0,0,0,0,0,0,0,10,1,3,4,4,0,10,4,0,0,1,1,0,0,1,4,0,1};
    // Official26.3/protocol777 ordered [id,count] facts from tests/minecraft_direct/fixtures/menu_properties_26_3.json.
    static final String OFFICIAL_PROPERTY_SHA256="c8fd6c7837a40d38e1ea988f6ae67ec6d5e337f78efa5018ec1b6f4a5c8a2586";
    static void check(boolean value,String name){checks++;if(!value)throw new AssertionError(name);}
    static void receive(InventoryState state,byte[] packet)throws IOException{state.accept(packet[0]&255,Arrays.copyOfRange(packet,1,packet.length));}
    static void open(InventoryState state,int type)throws IOException{receive(state,ControlFixture.packet(0x3c,d->{ControlFixture.vi(d,5);ControlFixture.vi(d,type);d.writeByte(8);d.writeUTF("");}));}
    static void full(InventoryState state,int container,int total)throws IOException{receive(state,ControlFixture.packet(0x12,d->{ControlFixture.vi(d,container);ControlFixture.vi(d,3);ControlFixture.vi(d,total);for(int n=0;n<=total;n++)ControlFixture.vi(d,0);}));}
    static byte[] property(int container,int index,int value)throws IOException{return ControlFixture.packet(0x13,d->{ControlFixture.vi(d,container);d.writeShort(index);d.writeShort(value);});}
    static void bad(InventoryState state,byte[] packet)throws IOException{
        try{receive(state,packet);throw new AssertionError("malformed property accepted");}catch(IOException expected){checks++;}
        check(!state.view().known()&&state.snapshot(0).get("properties").isJsonNull(),"malformed clears all old property authority");
    }
    static void officialSmithingAndCartography()throws Exception{
        // Official SmithingMenu hasRecipeError/addDataSlot; CartographyTableMenu has no data slots.
        // These receiver expectations are separate from the table below and the product arrays.
        try(ControlFixture f=new ControlFixture(true,true)){
            f.handle(ControlFixture.packet(0x3c,d->{ControlFixture.vi(d,1);ControlFixture.vi(d,21);d.writeByte(8);d.writeUTF("");}));
            f.handle(ControlFixture.packet(0x12,d->{ControlFixture.vi(d,1);ControlFixture.vi(d,5);ControlFixture.vi(d,40);for(int i=0;i<=40;i++)ControlFixture.vi(d,0);}));
            check(f.control.observation().getAsJsonObject("inventory").getAsJsonArray("properties").size()==1,"official smithing error property exists");
            f.handle(property(1,0,1));
            check(f.connection.snapshot().stage()==VanillaConnection.Stage.PLAY&&f.control.observation().getAsJsonObject("inventory").getAsJsonArray("properties").get(0).getAsInt()==1,"valid smithing error update keeps owner open");
            f.handle(ControlFixture.packet(0x3c,d->{ControlFixture.vi(d,2);ControlFixture.vi(d,23);d.writeByte(8);d.writeUTF("");}));
            f.handle(ControlFixture.packet(0x12,d->{ControlFixture.vi(d,2);ControlFixture.vi(d,6);ControlFixture.vi(d,39);for(int i=0;i<=39;i++)ControlFixture.vi(d,0);}));
            check(f.control.observation().getAsJsonObject("inventory").getAsJsonArray("properties").isEmpty(),"official cartography has no property slots");
            try{f.handle(property(2,0,1));throw new AssertionError("invented cartography property accepted");}catch(IOException expected){checks++;}
            check(f.connection.snapshot().stage()==VanillaConnection.Stage.CLOSED,"invalid cartography property fails closed");
        }
    }
    static void officialPropertyOracle(com.google.gson.JsonArray observations)throws Exception{
        check(observations.size()==25,"official property oracle coverage");
        var pairs=new com.google.gson.JsonArray();
        for(int id=0;id<25;id++){
            var observed=observations.get(id).getAsJsonObject();
            check(observed.get("menu_type_id").getAsInt()==id,"official property oracle ID coverage");
            var pair=new com.google.gson.JsonArray();pair.add(id);pair.add(observed.getAsJsonArray("properties").size());pairs.add(pair);
        }
        var digest=MessageDigest.getInstance("SHA-256").digest(pairs.toString().getBytes(StandardCharsets.UTF_8));
        check(HexFormat.of().formatHex(digest).equals(OFFICIAL_PROPERTY_SHA256),"official property oracle count mismatch");
    }
    public static void main(String[] args)throws Exception{
        officialSmithingAndCartography();
        var observations=new com.google.gson.JsonArray();
        for(int type=0;type<25;type++){
            InventoryState state=new InventoryState();check(state.snapshot(0).has("properties"),"explicit property observation field");
            check(state.snapshot(0).get("properties").isJsonNull(),"unknown baseline does not invent properties");open(state,type);
            if(COUNT[type]>0)receive(state,property(5,0,-123));
            full(state,5,TOTAL[type]);var values=state.snapshot(0).getAsJsonArray("properties");
            observations.add(state.snapshot(0));
            check(values.size()==COUNT[type],"official property count "+type);
            for(int index=0;index<COUNT[type];index++)check(index==0?values.get(index).getAsInt()==-123:values.get(index).isJsonNull(),"unreceived property unknown "+type+":"+index);
            int slotState=state.view().stateId();
            for(int index=0;index<COUNT[type];index++){
                long prior=state.view().revision();receive(state,property(5,index,-32768+index*2));
                check(state.view().revision()>prior&&state.view().stateId()==slotState,"property own revision without fabricated slot state");
                check(state.snapshot(0).getAsJsonArray("properties").get(index).getAsInt()==-32768+index*2,"signed-short property "+index);
            }
            if(COUNT[type]>0){receive(state,property(5,0,32767));check(state.snapshot(0).getAsJsonArray("properties").get(0).getAsInt()==32767,"latest observed overwrite");}
            long revision=state.view().revision();receive(state,property(6,0,9));check(state.view().revision()==revision,"foreign container ignored");
            open(state,type);full(state,5,TOTAL[type]);
            for(var value:state.snapshot(0).getAsJsonArray("properties"))check(value.isJsonNull(),"same ID reopen resets properties");
            receive(state,ControlFixture.packet(0x11,d->ControlFixture.vi(d,5)));full(state,0,46);
            check(state.snapshot(0).getAsJsonArray("properties").isEmpty(),"player menu has no properties");
        }
        for(int malformed=0;malformed<4;malformed++){
            InventoryState state=new InventoryState();open(state,14);full(state,5,39);receive(state,property(5,0,13));
            byte[] packet=switch(malformed){case 0->property(5,-1,0);case 1->property(5,4,0);case 2->Arrays.copyOf(property(5,0,2),5);default->Arrays.copyOf(property(5,0,2),7);};
            bad(state,packet);
        }
        InventoryState unknown=new InventoryState();open(unknown,25);receive(unknown,property(5,0,4));
        check(!unknown.view().known()&&unknown.snapshot(0).get("properties").isJsonNull(),"unregistered menu cannot gain property authority");
        officialPropertyOracle(observations);
        System.out.println("MENU_PROPERTY_OBSERVATIONS "+observations);
        System.out.println("MenuPropertiesTest PASS "+checks);
    }
}
