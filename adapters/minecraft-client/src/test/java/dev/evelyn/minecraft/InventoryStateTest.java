package dev.evelyn.minecraft;

import com.google.gson.JsonObject;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.util.Arrays;

public final class InventoryStateTest {
    private static int checks;
    public static void main(String[] args)throws Exception {
        baselineAndPages(); deltasAndNamespaces(); unsupportedAndMalformed(); menus(); selectionAndClear();
        System.out.println("INVENTORY_STATE_PASS "+checks);
    }
    private static void baselineAndPages()throws Exception {
        InventoryState i=new InventoryState(); unknown(i,"initial baseline");
        i.accept(0x12,full(0,7,46,36,new byte[]{2,1,0,0},new byte[]{1,2,0,0}));
        JsonObject first=i.snapshot(0), hotbar=i.snapshot(32);
        check(first.get("known").getAsBoolean()&&first.get("container_id").getAsInt()==0,"full establishes menu zero");
        check(first.get("state_id").getAsInt()==7&&first.get("menu_type_id").isJsonNull(),"observed state with default menu type");
        check(first.get("slot_count").getAsInt()==46&&first.getAsJsonArray("slots").size()==16,"bounded first page");
        check(hotbar.getAsJsonArray("slots").size()==14,"last page bounded by actual slots");
        JsonObject slot=hotbar.getAsJsonArray("slots").get(4).getAsJsonObject();
        check(slot.get("index").getAsInt()==36&&slot.get("item_id").getAsInt()==1&&slot.get("count").getAsInt()==2,"literal full stack maps menu36");
        check(first.getAsJsonObject("carried").get("item_id").getAsInt()==2,"full carries cursor stack");
        slot.addProperty("count",99); first.getAsJsonObject("carried").addProperty("count",99);
        check(i.snapshot(32).getAsJsonArray("slots").get(4).getAsJsonObject().get("count").getAsInt()==2&&i.snapshot(0).getAsJsonObject("carried").get("count").getAsInt()==1,"snapshots have no mutable aliases");
        rejectPage(i,-16);rejectPage(i,1);rejectPage(i,48);
    }
    private static void deltasAndNamespaces()throws Exception {
        InventoryState i=new InventoryState();
        i.accept(0x14,new byte[]{0,1,0,36,1,1,0,0}); unknown(i,"slot delta cannot create baseline");
        i.accept(0x6e,new byte[]{0,0});unknown(i,"direct delta cannot create baseline");
        i.accept(0x62,new byte[]{1,1,0,0});unknown(i,"cursor cannot create baseline");
        i.accept(0x12,full(0,2,46,-1,new byte[]{0},new byte[]{0}));long rev=revision(i);
        i.accept(0x14,new byte[]{9,3,0,36,0});check(revision(i)==rev,"foreign container cannot change active baseline");
        i.accept(0x14,new byte[]{0,3,0,44,1,7,0,0});check(item(i,44)==7&&i.snapshot(0).get("state_id").getAsInt()==3,"slot delta updates exact menu index and state");
        i.accept(0x6e,new byte[]{8,0});check(empty(i,44),"direct hotbar8 maps menu44");
        i.accept(0x6e,new byte[]{36,1,8,0,0});check(item(i,8)==8,"direct feet36 maps armor8");
        i.accept(0x6e,new byte[]{39,1,9,0,0});check(item(i,5)==9,"direct head39 maps armor5");
        i.accept(0x6e,new byte[]{40,1,10,0,0});check(item(i,45)==10,"direct offhand40 maps menu45");
        i.accept(0x62,new byte[]{1,11,0,0});check(i.snapshot(0).getAsJsonObject("carried").get("item_id").getAsInt()==11,"cursor packet0x62 observed");
        i.accept(0x62,new byte[]{0});check(i.snapshot(0).getAsJsonObject("carried").get("state").getAsString().equals("empty"),"cursor clear observed");
    }
    private static void unsupportedAndMalformed()throws Exception {
        InventoryState i=new InventoryState();
        for(byte[] patch:new byte[][]{{1,1,1,0,13,1,0,1},{1,1,1,0,45,1,0,1}}){
            i.accept(0x12,full(0,1,46,-1,new byte[]{0},new byte[]{0}));
            i.accept(0x12,full(0,2,46,45,patch,new byte[]{0}));unknown(i,"unsupported patch invalidates all preceding slots atomically");
            i.accept(0x6e,new byte[]{8,0});unknown(i,"delta cannot restore unsupported full baseline");
        }
        byte[] good=full(0,1,46,-1,new byte[]{0},new byte[]{0});
        for(byte[] bad:new byte[][]{Arrays.copyOf(good,good.length-1),concat(good,new byte[]{0}),{0,1,45},{0,1,(byte)0x81,32}}){
            i.accept(0x12,good);reject(i,0x12,bad);unknown(i,"malformed full clears earlier baseline");
        }
        for(byte[] bad:new byte[][]{{0,2,0,46,0},{0,2,(byte)255,(byte)255,0},{0,2,0,0,100},{0,2,0,0,(byte)255,(byte)255,(byte)255,(byte)255,15}}){
            i.accept(0x12,good);reject(i,0x14,bad);unknown(i,"malformed slot invalidates baseline");
        }
        i.accept(0x12,good);reject(i,0x6e,new byte[]{43,0});unknown(i,"direct slot outside official inventory namespace rejected");
        i.accept(0x12,good);reject(i,0x62,new byte[]{0,0});unknown(i,"trailing cursor data rejected");
        i.accept(0x12,good);reject(i,0x62,new byte[1048577]);unknown(i,"payload allocation boundary rejected");
        rejectPage(i,16);
    }
    private static void menus()throws Exception {
        InventoryState i=new InventoryState();i.accept(0x12,full(0,1,46,-1,new byte[]{0},new byte[]{0}));
        i.accept(0x3c,new byte[]{5,2,8,0,0});unknown(i,"open revokes old menu baseline");
        i.accept(0x12,full(5,9,63,27,new byte[]{1,12,0,0},new byte[]{0}));
        check(i.snapshot(0).get("known").getAsBoolean()&&i.snapshot(0).get("menu_type_id").getAsInt()==2,"exact generic9x3 layout supported");
        i.accept(0x6e,new byte[]{9,1,13,0,0});check(item(i,27)==13,"direct main inventory index maps after container slots");
        i.accept(0x6e,new byte[]{8,1,14,0,0});check(item(i,62)==14,"direct hotbar maps final container row");
        i.accept(0x62,new byte[]{1,21,0,0});
        i.accept(0x14,new byte[]{0,10,0,36,1,15,0,0});
        check(!slot(i,54).get("item_id").isJsonNull()&&item(i,54)==15&&i.snapshot(0).get("state_id").getAsInt()==9,"default menu delta updates shared player slot without replacing active state");
        i.accept(0x12,full(0,11,46,44,new byte[]{1,16,0,0},new byte[]{1,17,0,0}));
        check(item(i,62)==16&&i.snapshot(0).get("state_id").getAsInt()==9&&i.snapshot(0).getAsJsonObject("carried").get("item_id").getAsInt()==21,"default full updates shared slots while preserving active state and cursor");
        long rev=revision(i);i.accept(0x11,new byte[]{4});check(revision(i)==rev,"foreign close cannot close active menu");
        i.accept(0x11,new byte[]{5});unknown(i,"close requires a fresh default baseline");
        i.accept(0x12,full(0,10,46,-1,new byte[]{0},new byte[]{0}));check(i.snapshot(0).get("known").getAsBoolean(),"default full restores after close");
        i.accept(0x3c,new byte[]{6,25,8,0,0});i.accept(0x12,full(6,1,38,-1,new byte[]{0},new byte[]{0}));unknown(i,"unregistered menu remains unsupported");
        reject(i,0x3c,new byte[]{7,2,8,0});unknown(i,"truncated title clears state");
        i.accept(0x3c,new byte[]{8,6,8,0,0});i.accept(0x12,full(8,1,45,-1,new byte[]{0},new byte[]{0}));check(i.snapshot(0).get("known").getAsBoolean(),"exact dispenser45 supported");
    }
    private static void selectionAndClear()throws Exception {
        InventoryState i=new InventoryState();i.accept(0x6b,new byte[]{8});
        check(i.snapshot(0).get("selected").getAsInt()==8&&!i.snapshot(0).get("known").getAsBoolean(),"selection alone is observed without baseline");
        long before=revision(i);i.clear();unknown(i,"generation clear drops baseline");
        check(i.snapshot(0).get("selected").isJsonNull()&&revision(i)>before,"generation clear drops selection without reusing revision");
        reject(i,0x6b,new byte[]{9});reject(i,0x6b,new byte[]{0,0});
        var field=InventoryState.class.getDeclaredField("revision");field.setAccessible(true);field.setLong(i,Protocol.MAX_SEQUENCE);
        reject(i,0x6b,new byte[]{0});unknown(i,"revision overflow remains fail closed");
        check(revision(i)==Protocol.MAX_SEQUENCE&&i.snapshot(0).get("selected").isJsonNull(),"overflow cannot wrap or publish accepted selection");
    }
    private static byte[] full(int container,int state,int count,int itemIndex,byte[] stack,byte[] carried)throws IOException {
        ByteArrayOutputStream out=new ByteArrayOutputStream();vi(out,container);vi(out,state);vi(out,count);
        for(int slot=0;slot<count;slot++)out.write(slot==itemIndex?stack:new byte[]{0});out.write(carried);return out.toByteArray();
    }
    private static void vi(ByteArrayOutputStream out,int value){do{int part=value&127;value>>>=7;out.write(part|(value==0?0:128));}while(value!=0);}
    private static byte[] concat(byte[] a,byte[] b)throws IOException{ByteArrayOutputStream out=new ByteArrayOutputStream();out.write(a);out.write(b);return out.toByteArray();}
    private static JsonObject slot(InventoryState i,int index){return i.snapshot(index/16*16).getAsJsonArray("slots").get(index%16).getAsJsonObject();}
    private static int item(InventoryState i,int index){return slot(i,index).get("item_id").getAsInt();}
    private static boolean empty(InventoryState i,int index){JsonObject s=slot(i,index);return s.get("state").getAsString().equals("empty")&&s.get("item_id").isJsonNull()&&s.get("count").getAsInt()==0&&s.get("patch_known").getAsBoolean();}
    private static long revision(InventoryState i){return i.snapshot(0).get("revision").getAsLong();}
    private static void unknown(InventoryState i,String reason){JsonObject s=i.snapshot(0);check(!s.get("known").getAsBoolean()&&s.get("slot_count").getAsInt()==0&&s.getAsJsonArray("slots").isEmpty()&&s.get("carried").isJsonNull()&&s.get("container_id").isJsonNull()&&s.get("state_id").isJsonNull()&&s.get("menu_type_id").isJsonNull()&&s.get("page_start").getAsInt()==0&&s.get("source").getAsString().equals("server_observed"),reason);}
    private static void reject(InventoryState i,int id,byte[] body)throws Exception{try{i.accept(id,body);}catch(IOException expected){checks++;return;}throw new AssertionError("invalid packet accepted");}
    private static void rejectPage(InventoryState i,int page){try{i.snapshot(page);}catch(IllegalArgumentException expected){checks++;return;}throw new AssertionError("invalid page accepted");}
    private static void check(boolean condition,String reason){if(!condition)throw new AssertionError(reason);checks++;}
}
