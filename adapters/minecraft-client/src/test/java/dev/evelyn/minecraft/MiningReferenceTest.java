package dev.evelyn.minecraft;

import java.io.IOException;

public final class MiningReferenceTest {
    static int checks;
    static void check(boolean value,String label){checks++;if(!value)throw new AssertionError(label);}
    public static void main(String[] args)throws Exception{
        var catalog=BlockCatalog.loadPinned();var reference=MiningReference.loadPinned(catalog);
        check(reference.blockKeys().size()==1286,"all structural block IDs");
        check(reference.blockKeys().get(1).equals("minecraft:stone"),"official stone block registry ID");
        int unbreakable=0,toolRequired=0;
        for(int id=0;id<1286;id++){
            var row=reference.block(id);check(row.id()==id&&row.name().equals(reference.blockKeys().get(id)),"contiguous ID/name identity");
            if(row.hardness()<0)unbreakable++;if(row.requiresCorrectToolForDrops())toolRequired++;
            check(row.origin().equals("VANILLA_REFERENCE"),"reference is never observed server state");
        }
        check(unbreakable==15&&toolRequired==470,"independent official raw metadata totals");
        for(int id=0;id<35723;id++)check(reference.state(id).name().equals(catalog.lookup(id).name()),"all global states map by pinned name");
        var stone=reference.state(1);check(stone.hardness()==1.5f&&stone.requiresCorrectToolForDrops(),"hand breaking stone does not imply correct drops");
        for(int id=139;id<=141;id++){
            var log=reference.state(id);check(log.name().equals("minecraft:oak_log")&&log.hardness()==2&&!log.requiresCorrectToolForDrops(),"log hardness/tool policy independent from movement geometry");
            check(!catalog.lookup(id).geometrySupported(),"reference never widens original walking authority");
        }
        for(int id:new int[]{-1,35723})try{reference.state(id);throw new AssertionError("bad state ID accepted");}catch(IllegalArgumentException expected){checks++;}
        for(int id:new int[]{-1,1286})try{reference.block(id);throw new AssertionError("bad block ID accepted");}catch(IllegalArgumentException expected){checks++;}
        try{reference.blockKeys().set(1,"test:changed");throw new AssertionError("mutable mapping");}catch(UnsupportedOperationException expected){checks++;}
        System.out.println("MINING_REFERENCE_PASS "+checks);
    }
}
