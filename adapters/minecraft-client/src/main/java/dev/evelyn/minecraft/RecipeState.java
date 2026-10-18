package dev.evelyn.minecraft;

import java.io.IOException;
import java.util.*;

final class RecipeState {
    record DisplayId(int value){DisplayId{if(value<0)throw new IllegalArgumentException("display_id");}}
    record Entry(DisplayId id,RecipeDisplayGrammar.Display display,Integer group,int category,
                 List<RecipeDisplayGrammar.Value> requirements,int flags,long observedRevision,int nodes,int wireBytes) {
        Entry{if(requirements!=null)requirements=List.copyOf(requirements);}
        boolean notification(){return (flags&1)!=0;}boolean highlight(){return (flags&2)!=0;}
    }
    record TypeSettings(boolean open,boolean filtering){}
    record Settings(List<TypeSettings> types){Settings{types=List.copyOf(types);}}
    record StonecutterChoice(RecipeDisplayGrammar.Value input,RecipeDisplayGrammar.Slot display){}
    record Properties(Map<String,List<Integer>> itemSets,List<StonecutterChoice> stonecutter){Properties{var copy=new LinkedHashMap<String,List<Integer>>();itemSets.forEach((key,value)->copy.put(key,List.copyOf(value)));itemSets=Map.copyOf(copy);stonecutter=List.copyOf(stonecutter);}}
    record Ghost(int containerId,RecipeDisplayGrammar.Display display){}
    record Snapshot(long generation,long revision,boolean bookComplete,Map<DisplayId,Entry> bindings,
                    Settings settings,Properties properties,Ghost ghost,boolean exhausted) {
        Snapshot{bindings=Map.copyOf(bindings);}
        boolean craftabilityKnown(){return false;}
    }
    private final long generation;
    private final Map<String,Integer> registries;
    private long revision;
    private boolean complete,exhausted;
    private Map<DisplayId,Entry> book=Map.of();
    private Part<Settings> settings;
    private Part<Properties> properties;
    private Part<Ghost> ghost;
    private record Part<T>(T value,int nodes,int bytes){}
    RecipeState(long generation,Map<String,Integer> registries){if(generation<1)throw new IllegalArgumentException("generation");this.generation=generation;this.registries=Map.copyOf(registries);}
    synchronized Snapshot snapshot(){return new Snapshot(generation,revision,complete,book,settings==null?null:settings.value(),properties==null?null:properties.value(),ghost==null?null:ghost.value(),exhausted);}
    static boolean supports(int id){return id==0x4b||id==0x4c||id==0x4d||id==0x88||id==0x40;}
    private void checkGeneration(long event)throws IOException{if(event!=generation)throw new IOException("stale_recipe_generation");}
    private void erase(){book=Map.of();complete=false;settings=null;properties=null;ghost=null;}
    synchronized void clear(long generation)throws IOException {
        checkGeneration(generation);erase();if(revision==Long.MAX_VALUE){exhausted=true;throw new IOException("recipe_revision_exhausted");}revision++;
    }
    /** A ghost is a hint for one menu/content lifetime; the received book is connection scoped. */
    synchronized void clearGhost(long generation)throws IOException {
        checkGeneration(generation);if(exhausted)throw new IOException("recipe_revision_exhausted");if(ghost==null)return;
        if(revision==Long.MAX_VALUE){erase();exhausted=true;throw new IOException("recipe_revision_exhausted");}
        ghost=null;revision++;
    }
    synchronized boolean accept(long generation,int packetId,byte[] bytes)throws IOException {
        checkGeneration(generation);if(!supports(packetId))return false;
        if(exhausted)throw new IOException("recipe_revision_exhausted");
        if(revision==Long.MAX_VALUE){erase();exhausted=true;throw new IOException("recipe_revision_exhausted");}
        try {
            var in=ComponentWireCodec.open(bytes,0,registries);long next=revision+1;
            switch(packetId) {
                case 0x4b->{
                    int count=in.count(4096);in.reserve(1+count);var added=new LinkedHashMap<DisplayId,Entry>();
                    for(int i=0;i<count;i++){var entry=entry(in,next,true);added.put(entry.id(),entry);}
                    boolean replace=in.bool();RecipeDisplayGrammar.end(in);
                    var base=replace?Map.<DisplayId,Entry>of():book;int size=base.size();long nodes=baseNodes(base),wire=baseBytes(base);
                    for(var value:added.values()){var old=base.get(value.id());if(old!=null){nodes-=old.nodes();wire-=old.wireBytes();}else{size++;nodes++;}nodes+=value.nodes();wire+=value.wireBytes();}
                    checkRetained(size,nodes,wire,settings,properties,replace?null:ghost);
                    in.reserve(2+size*2);var updated=new LinkedHashMap<>(base);updated.putAll(added);book=Map.copyOf(updated);complete=replace||complete;if(replace)ghost=null;
                }
                case 0x4c->{
                    int count=in.count(4096);in.reserve(1+count);var removed=new HashSet<DisplayId>();
                    for(int i=0;i<count;i++){in.reserve(1);removed.add(new DisplayId(RecipeDisplayGrammar.nonnegative(in)));}
                    RecipeDisplayGrammar.end(in);in.reserve(2+book.size()*2);var updated=new LinkedHashMap<>(book);for(var id:removed)updated.remove(id);book=Map.copyOf(updated);
                }
                case 0x4d->{
                    int start=in.position(),nodes=in.nodesUsed();in.reserve(16);var types=new ArrayList<TypeSettings>(4);
                    for(int i=0;i<4;i++)types.add(new TypeSettings(in.bool(),in.bool()));RecipeDisplayGrammar.end(in);
                    var value=new Part<>(new Settings(types),in.nodesUsed()-nodes,in.position()-start);checkRetained(book.size(),baseNodes(book),baseBytes(book),value,properties,ghost);settings=value;
                }
                case 0x88->{
                    int start=in.position(),nodes=in.nodesUsed(),count=in.count(4096);in.reserve(1+count);var sets=new LinkedHashMap<String,List<Integer>>();
                    for(int i=0;i<count;i++){
                        String key=in.identifier();if(sets.containsKey(key))throw new IOException("recipe_duplicate_property_key");
                        int size=in.count(4096);in.reserve(2+size*3);var ids=new ArrayList<Integer>(size);for(int j=0;j<size;j++)ids.add(RecipeDisplayGrammar.registry(in,1658));sets.put(key,List.copyOf(ids));
                    }
                    int choices=in.count(4096);in.reserve(2+choices*3);var list=new ArrayList<StonecutterChoice>(choices);
                    for(int i=0;i<choices;i++)list.add(new StonecutterChoice(RecipeDisplayGrammar.holders(in,1),RecipeDisplayGrammar.slot(in,1)));
                    RecipeDisplayGrammar.end(in);in.reserve(4+sets.size()*2);var value=new Part<>(new Properties(sets,list),in.nodesUsed()-nodes,in.position()-start);
                    checkRetained(0,1,0,settings,value,null);properties=value;
                    // Conservative observation fence: this packet has no replacement recipe-book ID binding.
                    book=Map.of();complete=false;ghost=null;
                }
                case 0x40->{
                    int start=in.position(),nodes=in.nodesUsed(),container=RecipeDisplayGrammar.nonnegative(in);var display=RecipeDisplayGrammar.display(in,0);RecipeDisplayGrammar.end(in);in.reserve(2);
                    var value=new Part<>(new Ghost(container,display),in.nodesUsed()-nodes,in.position()-start);checkRetained(book.size(),baseNodes(book),baseBytes(book),settings,properties,value);ghost=value;
                }
                default->throw new IOException("recipe_packet");
            }
            revision=next;return true;
        }catch(IOException|RuntimeException invalid){erase();revision++;throw invalid instanceof IOException io?io:new IOException("recipe_malformed",invalid);}
    }
    static Entry decodeEntry(byte[] bytes,Map<String,Integer> context)throws IOException {var in=ComponentWireCodec.open(bytes,0,context);var entry=entry(in,0,false);RecipeDisplayGrammar.end(in);return entry;}
    private static Entry entry(ComponentWireCodec.DecodeSession in,long revision,boolean includeFlags)throws IOException {
        int start=in.position(),before=in.nodesUsed();in.reserve(2);var id=new DisplayId(RecipeDisplayGrammar.nonnegative(in));var display=RecipeDisplayGrammar.display(in,1);
        int wireGroup=in.vi();if(wireGroup!=0)in.reserve(1);Integer group=wireGroup==0?null:wireGroup-1;int category=RecipeDisplayGrammar.registry(in,13);List<RecipeDisplayGrammar.Value> requirements=null;
        if(in.bool()){
            int size=in.count(4096);in.reserve(2+size*2);var list=new ArrayList<RecipeDisplayGrammar.Value>(size);
            for(int i=0;i<size;i++)list.add(RecipeDisplayGrammar.holders(in,2));requirements=List.copyOf(list);
        }
        int flags=includeFlags?in.u8():0;return new Entry(id,display,group,category,requirements,flags,revision,in.nodesUsed()-before,in.position()-start);
    }
    private static long baseNodes(Map<DisplayId,Entry> map){long count=1L+map.size();for(var e:map.values())count+=e.nodes();return count;}
    private static long baseBytes(Map<DisplayId,Entry> map){long count=0;for(var e:map.values())count+=e.wireBytes();return count;}
    private static void checkRetained(int entries,long nodes,long bytes,Part<?> first,Part<?> second,Part<?> third)throws IOException {
        nodes+=(first==null?0:first.nodes())+(second==null?0:second.nodes())+(third==null?0:third.nodes());
        bytes+=(first==null?0:first.bytes())+(second==null?0:second.bytes())+(third==null?0:third.bytes());
        if(entries>4096||nodes>65536||bytes>1048576)throw new IOException("recipe_retained_budget");
    }
}
