package dev.evelyn.minecraft;
import java.io.IOException;
import java.util.*;

/** Current received tags. Cursor starts after the packet ID; caller supplies supported registry ID extents. */
final class TagState {
    static final int MAX_NODES=65536,MAX_BYTES=1024*1024;
    enum Presence {UNOBSERVED,MISSING,EMPTY,NONEMPTY}
    enum Membership {UNKNOWN_CONTEXT,UNKNOWN_TAGS,MISSING_TAG,INVALID_ID,MEMBER,NOT_MEMBER}
    private record Tag(Set<Integer> members,boolean duplicates,int rawCount) {}
    private record RegistryTags(Map<String,Tag> tags,int nodes,long bytes) {}
    static final class Snapshot {
        private final long generation,revision,retainedBytes;
        private final int retainedNodes;
        private final boolean exhausted;
        private final Map<String,RegistryTags> registries;
        private final Map<String,Integer> extents;
        private Snapshot(long generation,long revision,boolean exhausted,Map<String,RegistryTags> registries,Map<String,Integer> extents,int nodes,long bytes){
            this.generation=generation;this.revision=revision;this.exhausted=exhausted;this.registries=Map.copyOf(registries);this.extents=Map.copyOf(extents);retainedNodes=nodes;retainedBytes=bytes;
        }
        long generation(){return generation;}long revision(){return revision;}boolean exhausted(){return exhausted;}
        int retainedNodes(){return retainedNodes;}long retainedBytes(){return retainedBytes;}
        Presence presence(String registry,String name){RegistryTags table=registries.get(registry);if(table==null)return Presence.UNOBSERVED;Tag tag=table.tags.get(name);if(tag==null)return Presence.MISSING;return tag.members.isEmpty()?Presence.EMPTY:Presence.NONEMPTY;}
        Membership membership(String registry,String name,int id){
            if(id<0)return Membership.INVALID_ID;Integer extent=extents.get(registry);if(extent==null)return Membership.UNKNOWN_CONTEXT;if(id>=extent)return Membership.INVALID_ID;
            RegistryTags table=registries.get(registry);if(table==null)return Membership.UNKNOWN_TAGS;Tag tag=table.tags.get(name);if(tag==null)return Membership.MISSING_TAG;
            return tag.members.contains(id)?Membership.MEMBER:Membership.NOT_MEMBER;
        }
        Optional<Set<Integer>> members(String registry,String name){RegistryTags table=registries.get(registry);Tag tag=table==null?null:table.tags.get(name);return extents.containsKey(registry)&&tag!=null?Optional.of(tag.members):Optional.empty();}
        boolean duplicateMembers(String registry,String name){RegistryTags table=registries.get(registry);Tag tag=table==null?null:table.tags.get(name);return tag!=null&&tag.duplicates;}
    }
    private long generation,revision;
    private boolean exhausted;
    private Snapshot current=new Snapshot(0,0,false,Map.of(),Map.of(),4,0);
    synchronized Snapshot snapshot(){return current;}
    synchronized void clear(){invalidate();}
    private void invalidate(){
        if(generation>=Protocol.MAX_SEQUENCE||revision>=Protocol.MAX_SEQUENCE)exhausted=true;
        if(generation<Protocol.MAX_SEQUENCE)generation++;if(revision<Protocol.MAX_SEQUENCE)revision++;
        current=new Snapshot(generation,revision,exhausted,Map.of(),Map.of(),4,0);
    }
    /** Returns only registries supplied in this packet, so existing packet-local flow guards need no second parse. */
    synchronized Set<String> accept(long expectedGeneration,VanillaConnection.Cursor input,Map<String,Integer> supplied)throws IOException {
        if(expectedGeneration!=generation)throw error("tag_generation"); // A stale packet must not erase a successor's state.
        try {
            if(exhausted||revision>=Protocol.MAX_SEQUENCE||generation>=Protocol.MAX_SEQUENCE)throw error("tag_lifetime");
            if(input.size()>MAX_BYTES)throw error("tag_packet_bytes");
            Budget parsed=new Budget(4,0),retained=new Budget(4,0);
            for(RegistryTags old:current.registries.values())retained.reserve(old.nodes,old.bytes);
            Map<String,Integer> extents=context(supplied,parsed,retained);
            int registryCount=input.count(256);parsed.reserve(registryCount*6+2,0); // Registry records plus seen/returned update sets.
            Map<String,RegistryTags> next=new HashMap<>(current.registries);Set<String> seen=new HashSet<>();
            for(int r=0;r<registryCount;r++){
                String registry=name(input,parsed,null);if(!seen.add(registry))throw error("duplicate_tag_registry");
                RegistryTags old=next.remove(registry);if(old!=null)retained.release(old.nodes,old.bytes);
                if(next.size()>=256)throw error("tag_registries");retained.reserve(4,registry.length());int nodes=4;long bytes=registry.length();
                int tagCount=input.count(65536);parsed.reserve(tagCount*4,0);retained.reserve(tagCount*4,0);nodes+=tagCount*4;
                Map<String,Tag> tags=new HashMap<>();Integer extent=extents.get(registry);
                for(int t=0;t<tagCount;t++){
                    String tag=name(input,parsed,retained);bytes+=tag.length();if(tags.containsKey(tag))throw error("duplicate_tag");
                    int count=input.count(1000000);parsed.reserve(count,4L*count);retained.reserve(count,4L*count);nodes+=count;bytes+=4L*count;
                    if(count>input.bytes.available())throw error("truncated_packet"); // At least one byte per VarInt, before member allocations.
                    Set<Integer> members=new HashSet<>();boolean duplicates=false;
                    for(int i=0;i<count;i++){int id=input.count(Integer.MAX_VALUE);if(extent!=null&&id>=extent)throw error("tag_member_id");if(!members.add(id))duplicates=true;}
                    tags.put(tag,new Tag(Set.copyOf(members),duplicates,count));
                }
                next.put(registry,new RegistryTags(Map.copyOf(tags),nodes,bytes));
            }
            input.end();
            // An extent first supplied later must validate every retained member before it can become usable.
            for(var registry:next.entrySet()){Integer extent=extents.get(registry.getKey());if(extent!=null)for(Tag tag:registry.getValue().tags.values())for(int id:tag.members)if(id>=extent)throw error("tag_member_id");}
            Set<String> updated=Set.copyOf(seen);revision++;current=new Snapshot(generation,revision,false,next,extents,retained.nodes,retained.bytes);return updated;
        }catch(IOException malformed){invalidate();throw malformed;}
        catch(RuntimeException malformed){invalidate();throw error("tag_context");}
    }
    private static Map<String,Integer> context(Map<String,Integer> supplied,Budget parsed,Budget retained)throws IOException {
        if(supplied==null||supplied.size()>256)throw error("tag_context");Map<String,Integer> copied=new HashMap<>();
        for(var entry:supplied.entrySet()){String key=entry.getKey();Integer extent=entry.getValue();
            if(key==null||key.length()>32767||!key.matches("[a-z0-9_.-]+:[a-z0-9/._-]+")||extent==null||extent<0||extent>1048576)throw error("tag_context");
            parsed.reserve(3,key.length()+4L);retained.reserve(3,key.length()+4L);copied.put(key,extent);
        }
        return Map.copyOf(copied);
    }
    private static String name(VanillaConnection.Cursor input,Budget parsed,Budget retained)throws IOException {
        // Reuse Cursor's bounded VarInt/string/UTF-8/identifier codec. Peek its length to reserve before allocation.
        input.bytes.mark(5);int encoded;try{encoded=input.count(32767*3);}finally{input.bytes.reset();}
        parsed.reserve(0,encoded);if(retained!=null)retained.reserve(0,encoded);return input.identifier();
    }
    private static final class Budget {
        int nodes;long bytes;
        Budget(int nodes,long bytes){this.nodes=nodes;this.bytes=bytes;}
        void reserve(int added,long addedBytes)throws IOException {
            if(added<0||nodes>MAX_NODES-added)throw error("tag_nodes");if(addedBytes<0||bytes>MAX_BYTES-addedBytes)throw error("tag_bytes");nodes+=added;bytes+=addedBytes;
        }
        void release(int removed,long removedBytes){nodes-=removed;bytes-=removedBytes;}
    }
    private static IOException error(String code){return new IOException(code);}
}
