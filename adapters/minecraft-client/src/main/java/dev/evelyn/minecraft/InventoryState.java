package dev.evelyn.minecraft;

import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Arrays;
import java.util.HexFormat;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;

/** Atomic server observations; no local clicks, predictions, or item mutations. */
final class InventoryState {
    static final int PAGE_SIZE=16;
    // Constructor layouts preserve Crafter's trailing preview and Lectern's absent player slots.
    private static final int[] MENU_SLOTS={45,54,63,72,81,90,45,46,39,37,39,41,46,38,39,39,41,1,40,39,63,40,39,39,38};
    private static final int[] MENU_PLAYER_START={9,18,27,36,45,54,9,9,3,1,3,5,10,2,3,3,5,-1,4,3,27,4,3,3,2};
    private static final int[] MENU_PROPERTIES={0,0,0,0,0,0,0,10,1,3,4,4,0,10,4,0,0,1,1,0,0,1,4,0,1};
    private long revision,epoch,selectionRevision=-1;
    private int containerId, stateId=-1, selected=-1, expectedSlots=46;
    private Integer menuTypeId;
    private Stack[] slots;
    private Stack carried;
    private Integer[] properties=new Integer[0];

    record StackView(int itemId,int count,String wirePatchIdentity,MenuPackets.Carried hash) {}
    record Selection(int index,long revision) {}
    synchronized Selection selection(){return new Selection(selected,selectionRevision);}
    record View(long revision,long epoch,boolean known,int containerId,int stateId,Integer menuTypeId,List<StackView> slots,StackView carried) {
        View {slots=List.copyOf(slots);}
    }
    synchronized View view(){
        List<StackView> copy=new ArrayList<>();if(slots!=null)for(Stack value:slots)copy.add(value.view());
        return new View(revision,epoch,slots!=null,containerId,stateId,menuTypeId,copy,carried==null?null:carried.view());
    }

    synchronized void clear(){reset();if(revision<Protocol.MAX_SEQUENCE)revision++;if(epoch<Protocol.MAX_SEQUENCE)epoch++;}
    private void invalidateBaseline()throws IOException {
        slots=null;carried=null;if(properties!=null)Arrays.fill(properties,null);if(epoch>=Protocol.MAX_SEQUENCE)throw new VanillaConnection.Failure("inventory_lifetime_exhausted");epoch++;
    }
    private void reset(){slots=null;carried=null;containerId=0;stateId=-1;selected=-1;selectionRevision=-1;menuTypeId=null;expectedSlots=46;properties=new Integer[0];}
    private void advance()throws IOException {
        if(revision>=Protocol.MAX_SEQUENCE){reset();throw new VanillaConnection.Failure("inventory_revision_exhausted");}
        revision++;
    }
    synchronized void accept(int id,byte[] payload)throws IOException {
        accept(id,payload,Map.of());
    }
    synchronized void accept(int id,byte[] payload,Map<String,Integer> registries)throws IOException {
        accept(id,payload,registries,Map.of());
    }
    synchronized void accept(int id,byte[] payload,Map<String,Integer> registries,Map<String,List<String>> registryKeys)throws IOException {
        try {
            if(epoch>=Protocol.MAX_SEQUENCE)throw new VanillaConnection.Failure("inventory_lifetime_exhausted");
            if(payload==null||payload.length>1048576)throw new VanillaConnection.Failure("inventory_payload_budget");
            VanillaConnection.Cursor c=new VanillaConnection.Cursor(payload);
            switch(id){
                case 0x6b -> {int next=c.count(8);c.end();advance();selected=next;selectionRevision=revision;}
                case 0x3c -> {
                    int nextContainer=c.count(Integer.MAX_VALUE),nextType=c.count(Integer.MAX_VALUE);
                    if(nextContainer==0)throw new VanillaConnection.Failure("invalid_open_container");
                    c.nbt(c.data.readUnsignedByte(),0);c.end();advance();
                    invalidateBaseline();containerId=nextContainer;menuTypeId=nextType;stateId=-1;
                    expectedSlots=nextType<MENU_SLOTS.length?MENU_SLOTS[nextType]:0;
                    properties=nextType<MENU_PROPERTIES.length?new Integer[MENU_PROPERTIES[nextType]]:null;
                }
                case 0x2a -> {
                    int nextContainer=c.count(Integer.MAX_VALUE);c.count(Integer.MAX_VALUE);int entity=c.data.readInt();c.end();
                    if(nextContainer==0||entity<0)throw new VanillaConnection.Failure("invalid_mount_container");
                    // shortcut: mount slots stay unknown until an entity-bound Horse/Nautilus layout is implemented.
                    advance();invalidateBaseline();containerId=nextContainer;menuTypeId=null;stateId=-1;expectedSlots=0;properties=null;
                }
                case 0x11 -> {
                    int closed=c.count(Integer.MAX_VALUE);c.end();if(closed!=containerId)return;
                    advance();invalidateBaseline();containerId=0;menuTypeId=null;expectedSlots=46;stateId=-1;properties=new Integer[0];
                }
                case 0x13 -> {
                    int target=c.count(Integer.MAX_VALUE),index=c.data.readShort(),value=c.data.readShort();c.end();
                    if(target!=containerId||properties==null)return;
                    if(index<0||index>=properties.length)throw new VanillaConnection.Failure("invalid_menu_property");
                    advance();properties=properties.clone();properties[index]=value;
                }
                case 0x12 -> {
                    int target=c.count(Integer.MAX_VALUE),nextState=c.count(32767),size=c.count(4096);
                    boolean shared=target==0&&containerId!=0&&playerStart()>=0;
                    if(target!=containerId&&!shared){c.discard(1048576);return;}
                    if(expectedSlots==0){c.discard(1048576);invalidateBaseline();advance();return;}
                    if(size!=(shared?46:expectedSlots))throw new VanillaConnection.Failure("unsupported_inventory_layout");
                    Stack[] next=new Stack[size];for(int slot=0;slot<size;slot++)next[slot]=stack(c,payload,registries,registryKeys);
                    Stack nextCarried=stack(c,payload,registries,registryKeys);c.end();advance();
                    if(shared){if(slots!=null){slots=slots.clone();for(int slot=9;slot<45;slot++)slots[playerMenuSlot(slot)]=next[slot];}}
                    else {slots=next;carried=nextCarried;stateId=nextState;}
                }
                case 0x14 -> {
                    int target=c.count(Integer.MAX_VALUE),nextState=c.count(32767),slot=c.data.readShort();
                    boolean shared=target==0&&containerId!=0&&playerStart()>=0;
                    if(target!=containerId&&!shared){c.discard(1048576);return;}
                    if(slot<0||slot>=(shared?46:expectedSlots)&&expectedSlots!=0)throw new VanillaConnection.Failure("invalid_inventory_slot");
                    Stack next=stack(c,payload,registries,registryKeys);c.end();advance();int mapped=shared?playerMenuSlot(slot):slot;
                    if(slots!=null&&mapped>=0){slots=slots.clone();slots[mapped]=next;if(!shared)stateId=nextState;}
                }
                case 0x6e -> {
                    int direct=c.count(42);Stack next=stack(c,payload,registries,registryKeys);c.end();int slot=directSlot(direct);
                    if(slot<0)return;advance();if(slots!=null){slots=slots.clone();slots[slot]=next;}
                }
                case 0x62 -> {Stack next=stack(c,payload,registries,registryKeys);c.end();advance();if(slots!=null)carried=next;}
                default -> throw new VanillaConnection.Failure("inventory_packet");
            }
            if(slots!=null){long nodes=carried.nodeCost(),bytes=carried.patchBytes();for(Stack stack:slots){nodes+=stack.nodeCost();bytes+=stack.patchBytes();}
                if(nodes>65536||bytes>1048576)throw new VanillaConnection.Failure("inventory_retained_component_budget");}
        }catch(UnsupportedStack unsupported){invalidateBaseline();advance();}
        catch(IOException|RuntimeException malformed){clear();throw malformed;}
    }
    private int directSlot(int direct){
        if(containerId==0)return direct<9?36+direct:direct<36?direct:direct<40?44-direct:direct==40?45:-1;
        int start=playerStart();if(start<0||direct>=36)return -1;
        return direct<9?start+27+direct:start+direct-9;
    }
    private int playerStart(){return menuTypeId!=null&&menuTypeId<MENU_PLAYER_START.length?MENU_PLAYER_START[menuTypeId]:-1;}
    private int playerMenuSlot(int slot){return slot<9||slot>=45?-1:directSlot(slot>=36?slot-36:slot);}
    private static Stack stack(VanillaConnection.Cursor c,byte[] payload,Map<String,Integer> registries,Map<String,List<String>> registryKeys)throws IOException {
        int count=c.count(99);if(count==0)return new Stack(-1,0,"",Map.of(),Set.of(),0,0,MenuPackets.Carried.empty());
        // Pinned 26.3 BuiltInRegistries.ITEM has 1658 contiguous IDs; a wire integer alone is not membership.
        int item=c.count(ItemCatalog.loadPinned().size()-1),start=payload.length-c.bytes.available();
        int added=c.count(122),removed=c.count(122),nodes=0;Map<Integer,JsonObject> values=new HashMap<>();Set<Integer> deleted=new HashSet<>(),seen=new HashSet<>();
        for(int index=0;index<added;index++){
            int type=c.count(121);if(!seen.add(type))throw new VanillaConnection.Failure("duplicate_inventory_component");
            ComponentWireCodec.Decoded decoded;
            try{decoded=ComponentWireCodec.decode(type,payload,payload.length-c.bytes.available(),registries);}
            catch(IOException unsupported){if("component_registry_unavailable".equals(unsupported.getMessage()))throw new UnsupportedStack();throw unsupported;}
            c.skip(decoded.consumed());nodes+=decoded.nodeCount();c.nbtNodes+=decoded.nodeCount();
            if(c.nbtNodes>65536)throw new VanillaConnection.Failure("inventory_component_budget");values.put(type,decoded.value());
        }
        for(int index=0;index<removed;index++){int type=c.count(121);if(!seen.add(type))throw new VanillaConnection.Failure("duplicate_inventory_component");deleted.add(type);}
        int end=payload.length-c.bytes.available();String fingerprint;
        try{fingerprint=HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(Arrays.copyOfRange(payload,start,end)));}
        catch(NoSuchAlgorithmException unavailable){throw new IOException("inventory_component_fingerprint",unavailable);}
        // OPTIONAL_STREAM_CODEC accepts an AIR holder, but ItemStack.isEmpty/HashedStack normalize it to EMPTY.
        if(item==0)return new Stack(-1,0,"",Map.of(),Set.of(),0,0,MenuPackets.Carried.empty());
        Map<Integer,Integer> hashes=new HashMap<>();boolean hashKnown=true;
        for(var entry:values.entrySet()){
            var hash=ComponentHash.component(entry.getKey(),entry.getValue(),registryKeys);
            if(hash.isEmpty()){hashKnown=false;break;}hashes.put(entry.getKey(),hash.getAsInt());
        }
        return new Stack(item,count,fingerprint,Map.copyOf(values),Set.copyOf(deleted),nodes,end-start,
                hashKnown?new MenuPackets.Carried(item,count,hashes,deleted):null);
    }
    synchronized JsonObject snapshot(int pageStart){
        if(pageStart<0||pageStart%PAGE_SIZE!=0||slots==null&&pageStart!=0||slots!=null&&pageStart>=slots.length)
            throw new IllegalArgumentException("inventory_page");
        boolean known=slots!=null;JsonObject out=new JsonObject();
        out.addProperty("revision",revision);out.addProperty("known",known);
        out.add("container_id",known?new com.google.gson.JsonPrimitive(containerId):JsonNull.INSTANCE);
        out.add("state_id",known?new com.google.gson.JsonPrimitive(stateId):JsonNull.INSTANCE);
        out.add("menu_type_id",known&&menuTypeId!=null?new com.google.gson.JsonPrimitive(menuTypeId):JsonNull.INSTANCE);
        out.addProperty("slot_count",known?slots.length:0);out.addProperty("page_start",pageStart);
        JsonArray page=new JsonArray();if(known)for(int index=pageStart;index<Math.min(slots.length,pageStart+PAGE_SIZE);index++){
            JsonObject slot=slots[index].json();slot.addProperty("index",index);page.add(slot);
        }
        out.add("slots",page);out.add("carried",known?carried.json():JsonNull.INSTANCE);
        JsonArray values=new JsonArray();if(known&&properties!=null)for(Integer value:properties)values.add(value);
        out.add("properties",known?values:JsonNull.INSTANCE);
        out.add("selected",selected>=0?new com.google.gson.JsonPrimitive(selected):JsonNull.INSTANCE);
        out.addProperty("source","server_observed");return out;
    }
    private record Stack(int itemId,int count,String wireIdentity,Map<Integer,JsonObject> added,Set<Integer> removed,int nodeCost,int patchBytes,MenuPackets.Carried hash){
        StackView view(){return new StackView(itemId,count,wireIdentity,hash);}
        JsonObject json(){JsonObject out=new JsonObject();out.addProperty("state",count==0?"empty":"item");
            out.add("item_id",count==0?JsonNull.INSTANCE:new com.google.gson.JsonPrimitive(itemId));
            out.addProperty("count",count);out.addProperty("patch_known",true);return out;}
    }
    private static final class UnsupportedStack extends IOException {}
}
