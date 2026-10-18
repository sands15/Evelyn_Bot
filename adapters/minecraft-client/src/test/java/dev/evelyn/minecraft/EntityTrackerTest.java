package dev.evelyn.minecraft;

import java.io.IOException;
import java.util.Arrays;

/** Fixed vectors from the pinned official 26.3 packet codecs, without game execution. */
public final class EntityTrackerTest {
    private static int checks;
    private static final EntityTracker.Point SELF=new EntityTracker.Point(0,1,0);
    public static void main(String[] args)throws Exception{
        if(args.length==1&&args[0].equals("segment")){segmentClearance();System.out.println("ENTITY_TRACKER_TARGET_PASS "+checks);return;}
        BlockCatalog catalog=BlockCatalog.loadPinned();
        check(catalog.lookupEntityType(32).name().equals("minecraft:creeper")&&catalog.lookupEntityType(32).monster(),"official creeper category");
        check(catalog.lookupEntityType(154).name().equals("minecraft:zombie")&&catalog.lookupEntityType(154).monster(),"official zombie category");
        EntityTracker t=new EntityTracker(catalog);
        accept(t,1,spawn(1,32,12.12345,1,0));
        check(t.count()==1&&!t.near(SELF,8,true),"spawn beyond radius");
        accept(t,0x36,relative(1,0,new short[][]{{0,0,0}}));
        check(t.near(new EntityTracker.Point(12.12345,1,0),1e-9,true),"zero delta preserves unquantized base exactly");
        accept(t,0x36,relative(1,0,new short[][]{{1,0,0}}));
        double quantized=(Math.round(12.12345*4096)+1)/4096.0;
        check(t.near(new EntityTracker.Point(quantized,1,0),1e-9,true),"nonzero delta rounds base at 4096 precision");
        accept(t,0x23,sync(1,20.125,1,0));
        accept(t,0x36,relative(1,0,new short[][]{{4096,0,0}}));
        check(t.near(new EntityTracker.Point(21.125,1,0),1e-9,true),"sync replaces relative decoding base");
        long before=t.event(),danger=t.dangerEpoch();byte[] bad=spawn(2,154,1,1,0);bad=Arrays.copyOf(bad,bad.length+1);
        byte[] malformed=bad;reject(()->accept(t,1,malformed));
        check(t.count()==1&&t.event()==before&&t.dangerEpoch()==danger,"trailing data rolls back entity and danger");
        t.beginBundle();accept(t,0x23,sync(1,1,1,0));accept(t,0x4e,remove(1));
        check(t.count()==1&&t.event()==before,"bundle publishes no intermediate movement or removal");t.endBundle();
        check(t.count()==0&&!t.near(SELF,8,true)&&t.dangerEpoch()>danger,"approach then remove retains danger epoch");
        accept(t,1,spawn(3,154,12,1,0));before=t.dangerEpoch();
        accept(t,0x36,relative(3,2,new short[][]{{-24576,0,0},{24576,0,0}}));
        check(!t.near(SELF,8,true)&&t.dangerEpoch()>before,"stepped traversal catches intermediate nearby monster");
        long stable=t.event();reject(()->accept(t,1,spawn(4,161,0,1,0)));
        check(t.count()==1&&t.event()==stable,"unknown registry type never invents safe entity");
        accept(t,0x36,relative(999,0,new short[][]{{4096,0,0}}));check(t.count()==1,"untracked movement cannot create entity");
        reject(()->accept(t,0x36,new byte[]{(byte)0xe7,7,0,0}));
        EntityTracker full=new EntityTracker(catalog);for(int i=0;i<1024;i++)accept(full,1,spawn(i,32,100,1,0));
        reject(()->accept(full,1,spawn(1024,32,100,1,0)));check(full.count()==1024,"bounded entity set preserves last publication on overflow");
        VanillaConnection.Cursor zero=new VanillaConnection.Cursor(new byte[]{0});check(EntityTracker.lpVector(zero).equals(new EntityTracker.Point(0,0,0)),"LP zero vector");zero.end();
        reject(()->EntityTracker.lpVector(new VanillaConnection.Cursor(new byte[]{1})));
        t.clear();check(t.count()==0,"clear discards tracking");
        segmentClearance();
        System.out.println("ENTITY_TRACKER_PASS "+checks+" checks (fixed official-layout synthetic vectors)");
    }
    private static void segmentClearance()throws Exception{
        EntityTracker t=new EntityTracker(BlockCatalog.loadPinned());
        EntityTracker.Point start=new EntityTracker.Point(.5,0,.5),end=new EntityTracker.Point(.598,0,.5);
        accept(t,1,spawn(1,11,.549,0,2.4998));
        check(!t.near(start,2,false)&&!t.near(end,2,false),"round buffer counterexample has two clear endpoints");
        check(!clearSegment(t,start,end,2),"interior segment entering entity buffer is blocked even for non-monster");
        check(clearSegment(t,start,new EntityTracker.Point(.598,0,.4),2),"segment moving away from same entity remains clear");
        check(!clearSegment(t,new EntityTracker.Point(.549,0,.5),new EntityTracker.Point(.549,0,.5),2),"zero-length segment checks its occupied buffer");
    }
    private static boolean clearSegment(EntityTracker tracker,EntityTracker.Point start,EntityTracker.Point end,double radius){return !tracker.nearSegment(start,end,radius);}
    static byte[] spawn(int id,int type,double x,double y,double z)throws IOException{
        return ControlFixture.body(d->{ControlFixture.vi(d,id);d.writeLong(0);d.writeLong(id);ControlFixture.vi(d,type);
            d.writeDouble(x);d.writeDouble(y);d.writeDouble(z);d.writeByte(0);d.write(new byte[3]);ControlFixture.vi(d,0);});
    }
    static byte[] sync(int id,double x,double y,double z)throws IOException{
        return ControlFixture.body(d->{ControlFixture.vi(d,id);ControlFixture.vi(d,0);d.writeDouble(x);d.writeDouble(y);d.writeDouble(z);d.writeFloat(0);d.writeFloat(0);d.writeBoolean(true);});
    }
    static byte[] relative(int id,int steps,short[][] deltas)throws IOException{
        return ControlFixture.body(d->{ControlFixture.vi(d,id);ControlFixture.vi(d,steps<<1);for(short[] xyz:deltas){if(steps!=0)ControlFixture.vi(d,1);for(short n:xyz)d.writeShort(n);}});
    }
    static byte[] remove(int id)throws IOException{return ControlFixture.body(d->{ControlFixture.vi(d,1);ControlFixture.vi(d,id);});}
    static void accept(EntityTracker t,int id,byte[] body)throws IOException{t.accept(id,new VanillaConnection.Cursor(body),SELF);}
    private static void check(boolean good,String why){if(!good)throw new AssertionError(why);checks++;}
    private static void reject(Checked call)throws Exception{try{call.run();}catch(IOException|IllegalArgumentException e){checks++;return;}throw new AssertionError("invalid entity packet accepted");}
    @FunctionalInterface interface Checked{void run()throws Exception;}
}
