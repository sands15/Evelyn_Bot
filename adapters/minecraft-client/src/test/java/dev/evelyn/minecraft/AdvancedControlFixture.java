package dev.evelyn.minecraft;

/** v4 uses the shared owned-memory game transport and bounded local WebSocket fixture. */
public final class AdvancedControlFixture {
    public static void main(String[] args){
        try{ControlFixture.runLoopback(true);System.out.println("CONTROL_PASS");}
        catch(Exception|AssertionError failure){System.out.println("CONTROL_FAILED");System.exit(2);}
    }
    static boolean handle(ControlFixture f,String input)throws Exception{
        switch(input){
            case "slab" -> f.handle(ControlFixture.block(9,1,8,15252));
            case "stairs" -> f.handle(ControlFixture.block(9,1,8,5474));
            case "water" -> {
                var ids=BlockCatalog.loadPinned().defaultFlowBlockIds();
                f.handle(ControlFixture.packet(0x89,d->{ControlFixture.vi(d,2);ControlFixture.string(d,"minecraft:block");ControlFixture.vi(d,1);ControlFixture.string(d,"minecraft:blocks_fluid_flow");ControlFixture.vi(d,ids.size());for(int id:ids)ControlFixture.vi(d,id);
                    ControlFixture.string(d,"minecraft:fluid");ControlFixture.vi(d,1);ControlFixture.string(d,"minecraft:water");ControlFixture.vi(d,2);ControlFixture.vi(d,1);ControlFixture.vi(d,2);}));
                f.handle(new byte[]{0});
                for(int x=5;x<=13;x++)for(int z=5;z<=11;z++)for(int y=1;y<=3;y++)f.handle(ControlFixture.block(x,y,z,89));
                f.handle(new byte[]{0});
            }
            case "empty_hands" -> {f.handle(ControlFixture.packet(0x12,d->d.write(BareHandInventoryTest.full(-1,false))));f.handle(new byte[]{0x6b,0});}
            case "weapon" -> f.handle(ControlFixture.packet(0x12,d->d.write(BareHandInventoryTest.full(36,false))));
            case "target" -> f.handle(ControlFixture.packet(1,d->d.write(EntityTrackerTest.spawn(7,154,10.5,1,8.5))));
            case "target_dead" -> f.handle(ControlFixture.packet(0x22,d->{d.writeInt(7);d.writeByte(3);}));
            case "target_remove" -> f.handle(ControlFixture.packet(0x4e,d->d.write(EntityTrackerTest.remove(7))));
            default -> {return false;}
        }
        return true;
    }
}
