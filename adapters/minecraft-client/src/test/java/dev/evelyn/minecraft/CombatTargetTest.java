package dev.evelyn.minecraft;

import java.io.IOException;

public final class CombatTargetTest {
    private static int checks;
    public static void main(String[] args)throws Exception {
        EntityTracker t=new EntityTracker(BlockCatalog.loadPinned());spawn(t,7,154);long epoch=t.target(7).epoch();
        check(epoch>0&&t.target(7).geometryKnown(),"adult zombie pinned baseline");
        meta(t,7,new byte[]{9,3,65,(byte)160,0,0,(byte)255});check(t.target(7).health()==20,"server health is observed");
        meta(t,7,new byte[]{16,8,1,(byte)255});check(!t.target(7).geometryKnown(),"baby invalidates baseline");
        meta(t,7,new byte[]{16,8,0,(byte)255});check(!t.target(7).geometryKnown(),"partial default cannot restore invalidated shape");
        EntityTrackerTest.accept(t,0x4e,EntityTrackerTest.remove(7));spawn(t,7,154);
        check(t.target(7).epoch()>epoch&&t.target(7).geometryKnown(),"ID reuse creates fresh epoch");
        t.beginBundle();event(t,7,3);check(!t.target(7).dead(),"bundle hides death until completion");t.endBundle();
        check(t.target(7).dead()&&t.target(7).health()==0,"death event is server observation");
        EntityTrackerTest.accept(t,0x4e,EntityTrackerTest.remove(7));check(t.target(7).dead(),"bounded death evidence survives immediate removal");
        spawn(t,7,154);check(!t.target(7).dead(),"replacement cannot inherit death evidence");
        meta(t,7,new byte[]{9,3,0,0,0,0,99,0,1,(byte)255});
        check(t.target(7).health()==null&&!t.target(7).geometryKnown(),"unknown suffix cannot commit earlier health");
        spawn(t,8,32);meta(t,8,new byte[]{18,8,1,(byte)255});check(!t.target(8).geometryKnown(),"ignited creeper never exempted");
        spawn(t,9,159);check(!t.target(9).geometryKnown(),"player is not an attack target");
        t.clear();check(t.target(7)==null,"disconnect clears identities and deaths");
        System.out.println("COMBAT_TARGET_PASS "+checks);
    }
    private static void spawn(EntityTracker t,int id,int type)throws IOException{EntityTrackerTest.accept(t,1,EntityTrackerTest.spawn(id,type,2,1,0));}
    private static void meta(EntityTracker t,int id,byte[] data)throws IOException{t.metadata(new VanillaConnection.Cursor(ControlFixture.body(d->{ControlFixture.vi(d,id);d.write(data);})));}
    private static void event(EntityTracker t,int id,int event)throws IOException{t.entityEvent(new VanillaConnection.Cursor(ControlFixture.body(d->{d.writeInt(id);d.writeByte(event);})));}
    private static void check(boolean good,String why){if(!good)throw new AssertionError(why);checks++;}
}
