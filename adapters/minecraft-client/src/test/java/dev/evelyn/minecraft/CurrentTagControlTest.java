package dev.evelyn.minecraft;

import java.io.IOException;
import java.util.Map;
import java.util.Set;

public final class CurrentTagControlTest {
    static int checks;
    static void check(boolean value,String name){checks++;if(!value)throw new AssertionError(name);}
    record Tags(String registry,Map<String,int[]> values){}
    static byte[] packet(Tags... registries)throws IOException{
        return ControlFixture.packet(0x89,d->{ControlFixture.vi(d,registries.length);for(Tags registry:registries){
            ControlFixture.string(d,registry.registry());ControlFixture.vi(d,registry.values().size());
            for(var tag:registry.values().entrySet()){
                ControlFixture.string(d,tag.getKey());ControlFixture.vi(d,tag.getValue().length);for(int id:tag.getValue())ControlFixture.vi(d,id);
            }
        }});
    }
    static Tags flow(ControlFixture f){return new Tags("minecraft:block",Map.of("minecraft:blocks_fluid_flow",f.control.catalog.defaultFlowBlockIds().stream().mapToInt(Integer::intValue).sorted().toArray()));}
    static Tags water(){return new Tags("minecraft:fluid",Map.of("minecraft:water",new int[]{1,2}));}
    static boolean confirmed(ControlFixture f)throws Exception{var field=VanillaControl.class.getDeclaredField("flowTagsConfirmed");field.setAccessible(true);return field.getBoolean(f.control);}
    public static void main(String[] args)throws Exception{
        try(ControlFixture f=new ControlFixture(true,true)){
            check(f.connection.tagSnapshot().presence("minecraft:block","minecraft:blocks_fluid_flow")==TagState.Presence.UNOBSERVED,"new connection unknown tags");
            f.handle(packet(flow(f),water()));var first=f.connection.tagSnapshot();check(confirmed(f),"actual matching flow tags confirm");
            check(first.members("minecraft:fluid","minecraft:water").orElseThrow().equals(Set.of(1,2)),"static fluid extent accepted");
            f.handle(packet(new Tags("minecraft:item",Map.of("test:items",new int[]{1657}))));var second=f.connection.tagSnapshot();
            check(second.revision()>first.revision()&&second.membership("minecraft:item","test:items",1657)==TagState.Membership.MEMBER,"actual item registry extent");
            check(confirmed(f)&&second.presence("minecraft:block","minecraft:blocks_fluid_flow")==TagState.Presence.NONEMPTY,"v5 partial unrelated update preserves current flow confirmation");
            check(first.presence("minecraft:item","test:items")==TagState.Presence.UNOBSERVED,"old snapshots immutable");
            f.handle(packet(new Tags("minecraft:block",Map.of("test:blocks",new int[]{1285}))));
            check(!confirmed(f)&&f.connection.tagSnapshot().presence("minecraft:block","minecraft:blocks_fluid_flow")==TagState.Presence.MISSING,"included registry replaces full table");
            check(f.connection.tagSnapshot().members("minecraft:fluid","minecraft:water").orElseThrow().equals(Set.of(1,2)),"omitted fluid stays current");
            f.handle(packet(new Tags("minecraft:block",Map.of("minecraft:blocks_fluid_flow",new int[0]))));
            check(!confirmed(f)&&f.connection.tagSnapshot().presence("minecraft:block","minecraft:blocks_fluid_flow")==TagState.Presence.EMPTY,"known empty is distinct from missing");
            var before=f.connection.tagSnapshot();f.connection.close();var closed=f.connection.tagSnapshot();
            check(closed.generation()>before.generation()&&closed.presence("minecraft:fluid","minecraft:water")==TagState.Presence.UNOBSERVED,"close clears tag authority");
        }
        try(ControlFixture f=new ControlFixture(true,false)){
            f.handle(packet(flow(f),water()));check(confirmed(f)&&f.connection.tagSnapshot()==null,"v4 parser contract preserved");
            f.handle(packet(new Tags("minecraft:item",Map.of("test:items",new int[]{1}))));check(!confirmed(f),"v4 retains previous packet-local availability policy");
        }
        for(int id:new int[]{1286,35722})try(ControlFixture f=new ControlFixture(true,true)){
            f.handle(packet(flow(f),water()));
            try{f.handle(packet(new Tags("minecraft:block",Map.of("test:bad",new int[]{id}))));throw new AssertionError("state ID used as block registry ID");}catch(IOException expected){checks++;}
            check(f.connection.snapshot().stage()==VanillaConnection.Stage.CLOSED,"invalid tag input closes player owner");
            check(f.connection.tagSnapshot().presence("minecraft:block","minecraft:blocks_fluid_flow")==TagState.Presence.UNOBSERVED,"invalid input clears all old tags");
        }
        try(ControlFixture f=new ControlFixture(true,true)){
            f.handle(packet(new Tags("minecraft:item",Map.of("test:items",new int[]{1,1}))));
            check(f.connection.tagSnapshot().duplicateMembers("minecraft:item","test:items"),"generic duplicate fact preserved");
            try{f.handle(packet(new Tags("minecraft:fluid",Map.of("minecraft:water",new int[]{1,1,2}))));throw new AssertionError("flow duplicate relaxed");}catch(IOException expected){checks++;}
            check(f.connection.snapshot().stage()==VanillaConnection.Stage.CLOSED,"legacy selected-tag duplicate guard preserved");
        }
        try(ControlFixture f=new ControlFixture(true,true)){
            f.handle(packet(flow(f),water()));var before=f.connection.tagSnapshot();ControlFixture.set(f.connection,"stage",VanillaConnection.Stage.CONFIGURATION);
            f.handle(ControlFixture.packet(0x07,d->{ControlFixture.string(d,"test:new_registry");ControlFixture.vi(d,1);ControlFixture.string(d,"test:entry");d.write(new byte[]{1,10,0});}));
            check(f.connection.tagSnapshot().generation()>before.generation()&&!confirmed(f),"new configuration registry invalidates old tag binding");
        }
        System.out.println("CurrentTagControlTest PASS "+checks);
    }
}
