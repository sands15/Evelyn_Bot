package dev.evelyn.minecraft;
import java.io.IOException;
import java.util.*;

/** Bounded wire observations, never default values or effective movement/mining readiness. */
final class PlayerAttributes {
    static final int MAX_BYTES=1048576,MAX_NODES=65536,ATTRIBUTE_COUNT=40,MAX_MODIFIERS=128;
    record Modifier(String id,double amount,int operation) {}
    record DecodedPacket(int entityId,Map<Integer,Entry> entries){DecodedPacket{entries=Map.copyOf(entries);}}
    @FunctionalInterface interface PacketValidator {void validate(DecodedPacket packet)throws IOException;}
    record Entry(double base,List<Modifier> modifiers) {
        Entry{modifiers=List.copyOf(modifiers);}
        String origin(){return "SERVER_OBSERVED";}
    }
    record Snapshot(long generation,long revision,Integer entityId,boolean received,Map<Integer,Entry> entries,int retainedNodes,long retainedBytes,boolean exhausted){
        Snapshot{entries=Map.copyOf(entries);}
        Optional<Entry> entry(int attributeId){return Optional.ofNullable(entries.get(attributeId));}
    }
    private long generation,revision;
    private boolean exhausted;
    private Integer selfId;
    private Snapshot current=new Snapshot(0,0,null,false,Map.of(),4,0,false);
    synchronized Snapshot snapshot(){return current;}
    /** Owner binds an observed login/respawn identity; binding itself creates no attribute evidence. */
    synchronized void bind(int id)throws IOException {
        invalidate();
        if(id<0)throw error("attribute_identity");
        if(exhausted||generation>=Protocol.MAX_SEQUENCE||revision>=Protocol.MAX_SEQUENCE){exhausted=true;publishEmpty();throw error("attribute_lifetime");}
        selfId=id;current=new Snapshot(generation,revision,id,false,Map.of(),4,0,false);
    }
    synchronized void clear(){invalidate();}
    private void invalidate(){
        if(generation>=Protocol.MAX_SEQUENCE||revision>=Protocol.MAX_SEQUENCE)exhausted=true;
        if(generation<Protocol.MAX_SEQUENCE)generation++;if(revision<Protocol.MAX_SEQUENCE)revision++;
        selfId=null;publishEmpty();
    }
    private void publishEmpty(){current=new Snapshot(generation,revision,null,false,Map.of(),4,0,exhausted);}
    synchronized boolean accept(long expectedGeneration,VanillaConnection.Cursor input)throws IOException {
        return accept(expectedGeneration,input,packet->{});
    }
    /** Cursor points after CB0x86 ID. Validator sees exactly this packet before any self publication. */
    synchronized boolean accept(long expectedGeneration,VanillaConnection.Cursor input,PacketValidator validator)throws IOException {
        if(expectedGeneration!=generation)throw error("attribute_generation"); // Stale callbacks must not erase a successor.
        long startedGeneration=generation,startedRevision=revision;Integer startedSelf=selfId;
        try {
            if(exhausted||generation>=Protocol.MAX_SEQUENCE||revision>=Protocol.MAX_SEQUENCE)throw error("attribute_lifetime");
            if(selfId==null)throw error("attribute_identity");
            if(input.size()>MAX_BYTES)throw error("attribute_packet_bytes");
            int entity=input.count(Integer.MAX_VALUE),count=input.count(ATTRIBUTE_COUNT);boolean target=entity==selfId;
            Budget parsed=new Budget(4,0),retained=new Budget(4,0);parsed.reserve(count*5,count*12L);
            // Packet record/entity, temporary/immutable maps and entry references share this parse budget.
            parsed.reserve(4+count*2,0);
            Map<Integer,Entry> next=target?new HashMap<>(current.entries):new HashMap<>();
            Map<Integer,Entry> packetEntries=new HashMap<>();
            if(target)for(Entry old:current.entries.values())retained.reserve(nodes(old),bytes(old));
            Set<Integer> seen=new HashSet<>();
            for(int i=0;i<count;i++){
                int id=input.count(ATTRIBUTE_COUNT-1);if(!seen.add(id))throw error("duplicate_attribute");
                Entry old=next.remove(id);if(old!=null)retained.release(nodes(old),bytes(old));retained.reserve(5,12);
                double base=input.finiteDouble();int modifiers=input.count(MAX_MODIFIERS);
                // Reserve every logical modifier/key/list entry before boxing, list/map allocation or name decoding.
                parsed.reserve(modifiers*4,modifiers*9L);retained.reserve(modifiers*4,modifiers*9L);
                if(modifiers>input.bytes.available()/10)throw error("truncated_packet");
                List<Modifier> values=new ArrayList<>();Set<String> names=new HashSet<>();
                for(int m=0;m<modifiers;m++){
                    String name=name(input,parsed,retained);if(!names.add(name))throw error("duplicate_modifier");
                    double amount=input.finiteDouble();int operation=input.count(2);
                    values.add(new Modifier(name,amount,operation));
                }
                Entry observed=new Entry(base,values);
                next.put(id,observed);packetEntries.put(id,observed); // Whole received modifier-list replacement.
            }
            input.end();validator.validate(new DecodedPacket(entity,packetEntries));
            if(!sameState(startedGeneration,startedRevision,startedSelf))throw error("attribute_state_changed");
            if(!target)return false;
            revision++;current=new Snapshot(generation,revision,selfId,true,next,retained.nodes,retained.bytes,false);return true;
        }catch(IOException malformed){if(sameState(startedGeneration,startedRevision,startedSelf))invalidate();throw malformed;}
        catch(RuntimeException malformed){if(sameState(startedGeneration,startedRevision,startedSelf))invalidate();throw error("attribute_packet");}
    }
    private boolean sameState(long startedGeneration,long startedRevision,Integer startedSelf){return generation==startedGeneration&&revision==startedRevision&&Objects.equals(selfId,startedSelf);}
    private static int nodes(Entry value){return 5+value.modifiers.size()*4;}
    private static long bytes(Entry value){long size=12;for(Modifier modifier:value.modifiers)size+=9L+modifier.id.length();return size;}
    private static String name(VanillaConnection.Cursor input,Budget parsed,Budget retained)throws IOException {
        input.bytes.mark(5);int encoded;try{encoded=input.count(32767*3);}finally{input.bytes.reset();}
        parsed.reserve(0,encoded);retained.reserve(0,encoded);return input.identifier();
    }
    private static final class Budget {
        int nodes;long bytes;
        Budget(int nodes,long bytes){this.nodes=nodes;this.bytes=bytes;}
        void reserve(int added,long amount)throws IOException {
            if(added<0||nodes>MAX_NODES-added)throw error("attribute_nodes");if(amount<0||bytes>MAX_BYTES-amount)throw error("attribute_bytes");nodes+=added;bytes+=amount;
        }
        void release(int removed,long amount){nodes-=removed;bytes-=amount;}
    }
    private static IOException error(String code){return new IOException(code);}
}
