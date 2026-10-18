package dev.evelyn.minecraft;

import com.google.gson.JsonObject;

public final class AdvancedControlTest {
    private static int checks;
    public static void main(String[] args)throws Exception {
        if(args.length==1&&args[0].equals("combat_visibility")){combatVisibility();System.out.println("COMBAT_VISIBILITY_PASS "+checks);return;}
        combatVisibility();
        try(ControlFixture f=new ControlFixture(true)){
            check(f.control.observation().has("locomotion")&&f.control.observation().has("targets"),"explicit v4 projection");
            JsonObject jump=ControlFixture.look(1,0,0);jump.addProperty("action","jump");jump.remove("yaw");jump.remove("pitch");
            f.control.start(jump,()->true);long end=System.nanoTime()+3_000_000_000L;String result=null;double highest=1;
            while(result==null&&System.nanoTime()-end<0){Thread.sleep(50);result=f.control.poll();highest=Math.max(highest,f.control.pose().y());}
            check("succeeded".equals(result)&&highest>2,"jump rises and lands in owned packet fixture");
            f.control.idle(()->false);check(f.connection.controlReady(),"dry terminal needs no maintenance or close");
        }
        try(ControlFixture f=new ControlFixture(true)){
            f.handle(ControlFixture.packet(0x12,d->d.write(BareHandInventoryTest.full(-1,false))));f.handle(new byte[]{0x6b,0});
            f.handle(ControlFixture.packet(1,d->d.write(EntityTrackerTest.spawn(7,154,10.5,1,8.5))));
            long epoch=f.control.observation().getAsJsonArray("targets").get(0).getAsJsonObject().get("spawn_epoch").getAsLong();
            JsonObject command=attack(1,7,epoch);f.control.start(command,()->true);f.control.poll();
            check(f.control.observation().getAsJsonObject("combat").get("attacks_sent").getAsInt()==1,"explicit target permits one attack");
            f.handle(ControlFixture.packet(0x22,d->{d.writeInt(7);d.writeByte(3);}));
            check("succeeded".equals(f.control.poll())&&f.control.resultCode().equals("target_defeated_observed"),"observed death terminal is not send count");
            f.control.off();int bytes=f.output.size();f.control.idle(()->true);check(f.output.size()==bytes,"OFF cannot be revived by idle permit");
        }
        try(ControlFixture f=new ControlFixture(true)){
            f.handle(ControlFixture.packet(1,d->d.write(EntityTrackerTest.spawn(7,154,10.5,1,8.5))));
            f.control.start(attack(1,7,1),()->true);check("failed".equals(f.control.poll())&&f.output.size()==0,"unknown inventory cannot send attack");
        }
        for(int variant=0;variant<4;variant++){
            try(ControlFixture f=new ControlFixture(true)){
                f.handle(ControlFixture.packet(0x12,d->d.write(BareHandInventoryTest.full(-1,false))));f.handle(new byte[]{0x6b,0});
                f.handle(ControlFixture.packet(1,d->d.write(EntityTrackerTest.spawn(7,154,11,1,8.5))));
                int type=159;if(variant!=3){for(int i=0;i<f.control.catalog.entityTypeCount();i++)if(f.control.catalog.lookupEntityType(i).name().equals("minecraft:cow"))type=i;}
                final int otherType=type;double x=variant==2?10:8.5,z=variant==0||variant==3?7.5:variant==1?9.5:10;
                f.handle(ControlFixture.packet(1,d->d.write(EntityTrackerTest.spawn(8,otherType,x,1,z))));
                if(variant==2)f.handle(ControlFixture.packet(0x86,d->{ControlFixture.vi(d,8);ControlFixture.vi(d,1);ControlFixture.vi(d,30);d.writeDouble(4);ControlFixture.vi(d,0);}));
                f.control.start(attack(1,7,1),()->true);check("failed".equals(f.control.poll())&&f.output.size()==0,"only bound target may bypass proximity, unknown shape never clears ray "+variant);
            }
        }
        System.out.println("ADVANCED_CONTROL_PASS "+checks);
    }
    private static void combatVisibility()throws Exception {
        for(String name:new String[]{"minecraft:cow","minecraft:player"}) {
            try(ControlFixture f=armed()) {
                spawnOther(f,name,55.5,1,8.5);
                f.control.start(attack(1,7,1),()->true);
                check(f.control.poll()==null&&sent(f)==1,"unrelated unsupported shape 47 m away cannot veto attack: "+name);
            }
        }
        for(double offset:new double[]{2.1,7.999,8.0,8.001}) {
            try(ControlFixture f=armed()) {
                spawnOther(f,"minecraft:cow",8.5,1,8.5+offset);
                f.control.start(attack(1,7,1),()->true);String result=f.control.poll();
                boolean allowed=offset>8;
                check(allowed?result==null&&sent(f)==1:"failed".equals(result)&&f.output.size()==0,
                    "unsupported shape retains local 8 m protection, offset="+offset);
            }
        }
        try(ControlFixture f=armed()) {
            spawnOther(f,"minecraft:ender_dragon",17,1,8.5);
            f.control.start(attack(1,7,1),()->true);
            check("failed".equals(f.control.poll())&&f.output.size()==0,"nominal body crossing the ray still blocks beyond 8 m origin");
        }
        try(ControlFixture f=armed()) {
            spawnOther(f,"minecraft:cow",55.5,1,8.5);
            f.control.start(attack(1,7,1),()->true);check(f.control.poll()==null&&sent(f)==1,"far unsupported baseline sends once");
            f.handle(ControlFixture.packet(0x4e,d->{ControlFixture.vi(d,1);ControlFixture.vi(d,8);}));
            spawnOther(f,"minecraft:cow",8.5,1,15.5);int bytes=f.output.size();
            check("failed".equals(f.control.poll())&&sent(f)==1&&f.output.size()==bytes,"unsupported approach into 8 m revokes ongoing combat without another write");
        }
    }
    private static ControlFixture armed()throws Exception {
        ControlFixture f=new ControlFixture(true);
        f.handle(ControlFixture.packet(0x12,d->d.write(BareHandInventoryTest.full(-1,false))));f.handle(new byte[]{0x6b,0});
        f.handle(ControlFixture.packet(1,d->d.write(EntityTrackerTest.spawn(7,154,10.5,1,8.5))));return f;
    }
    private static int sent(ControlFixture f){return f.control.observation().getAsJsonObject("combat").get("attacks_sent").getAsInt();}
    private static void spawnOther(ControlFixture f,String name,double x,double y,double z)throws Exception {
        int id=-1;for(int i=0;i<f.control.catalog.entityTypeCount();i++)if(name.equals(f.control.catalog.lookupEntityType(i).name()))id=i;
        if(id<0)throw new AssertionError("missing pinned entity "+name);final int type=id;
        f.handle(ControlFixture.packet(1,d->d.write(EntityTrackerTest.spawn(8,type,x,y,z))));
    }
    static JsonObject attack(int seq,int id,long epoch){JsonObject c=new JsonObject();c.addProperty("seq",seq);c.addProperty("action","attack");c.addProperty("run_ms",5000);c.addProperty("target_id",id);c.addProperty("target_epoch",epoch);return c;}
    private static void check(boolean good,String why){if(!good)throw new AssertionError(why);checks++;}
}
