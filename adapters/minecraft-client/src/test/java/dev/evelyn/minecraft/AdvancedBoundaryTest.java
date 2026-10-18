package dev.evelyn.minecraft;

import com.google.gson.JsonObject;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.net.Socket;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import javax.crypto.Cipher;
import javax.crypto.CipherOutputStream;
import javax.crypto.spec.IvParameterSpec;
import javax.crypto.spec.SecretKeySpec;

/** Independent v4 host boundaries. Only owned in-memory game streams are used. */
public final class AdvancedBoundaryTest {
    private static int checks;
    private static final List<String> failures=new ArrayList<>();
    public static void main(String[] args)throws Exception {
        if(args.length==1&&args[0].equals("water_tags")){waterTags();System.out.println("ADVANCED_BOUNDARY_TAGS_PASS "+checks);return;}
        if(args.length==1&&args[0].equals("post_walk_attack")){postWalkAttack();System.out.println("ADVANCED_BOUNDARY_POST_WALK_PASS "+checks);return;}
        if(args.length==1&&args[0].equals("terminal_environment")){terminalEnvironment();System.out.println("ADVANCED_BOUNDARY_TERMINAL_ENVIRONMENT_PASS "+checks);return;}
        if(args.length==1&&args[0].equals("maintenance_cancel")){maintenanceCancel(false);maintenanceCancel(true);System.out.println("ADVANCED_BOUNDARY_MAINTENANCE_CANCEL_PASS "+checks);return;}
        if(args.length==1&&args[0].equals("maintenance_bundle")){maintenanceBundle();System.out.println("ADVANCED_BOUNDARY_MAINTENANCE_BUNDLE_PASS "+checks);return;}
        if(args.length==1&&args[0].equals("unknown_approach")){afterWrite("unknown_approach");System.out.println("ADVANCED_UNKNOWN_APPROACH_PASS "+checks);return;}
        run("attack_normal",AdvancedBoundaryTest::attackNormal);
        run("post_walk_attack",AdvancedBoundaryTest::postWalkAttack);
        run("attack_before_permission",AdvancedBoundaryTest::beforePermission);
        for(String change:List.of("lease","off","weapon","reuse","metadata","unknown_approach"))
            run("attack_after_"+change,()->afterWrite(change));
        run("attack_partial",()->partialAttack(false));
        run("attack_partial_encrypted",()->partialAttack(true));
        run("bundle_death",AdvancedBoundaryTest::bundleDeath);
        run("target_reuse",AdvancedBoundaryTest::reuseBefore);
        run("other_mob",AdvancedBoundaryTest::otherMob);
        run("weapon",AdvancedBoundaryTest::weaponBefore);
        run("jump_headroom",AdvancedBoundaryTest::jumpHeadroom);
        run("half_slab",AdvancedBoundaryTest::halfSlab);
        run("stairs",AdvancedBoundaryTest::stairs);
        run("water_tags",AdvancedBoundaryTest::waterTags);
        run("water_lease",()->waterMaintenance(false));
        run("water_ttl",()->waterMaintenance(true));
        run("water_cancel",AdvancedBoundaryTest::waterCancel);
        run("water_air",AdvancedBoundaryTest::waterAir);
        run("terminal_environment",AdvancedBoundaryTest::terminalEnvironment);
        run("maintenance_cancel",()->maintenanceCancel(false));
        run("maintenance_cancel_encrypted",()->maintenanceCancel(true));
        run("maintenance_bundle",AdvancedBoundaryTest::maintenanceBundle);
        if(!failures.isEmpty())throw new AssertionError("ADVANCED_BOUNDARY_FAILED "+failures);
        System.out.println("ADVANCED_BOUNDARY_PASS "+checks);
    }
    private static void run(String label,Checked body)throws Exception {
        try{body.run();}catch(Exception|AssertionError problem){failures.add(label+":"+problem.getClass().getSimpleName()+":"+problem.getMessage());}
    }
    private static ControlFixture armed()throws Exception {
        ControlFixture f=new ControlFixture(true);
        f.handle(ControlFixture.packet(0x12,d->d.write(BareHandInventoryTest.full(-1,false))));
        f.handle(new byte[]{0x6b,0});spawn(f,7,154,10.5,1,8.5);return f;
    }
    private static void attackNormal()throws Exception {
        try(ControlFixture f=armed()){
            f.control.start(attack(1,7,epoch(f)),()->true);check(f.control.poll()==null,"attack must await server evidence");
            List<byte[]> packets=frames(f.output);check(packets.size()==6,"first attack has load, neutral, aim, attack, swing, client tick end");
            check(packets.get(0)[0]==0x2c&&packets.get(1)[0]==0x2b&&packets.get(2)[0]==0x1f,"neutral and aim precede attack");
            check(java.util.Arrays.equals(packets.get(3),new byte[]{1,7})&&java.util.Arrays.equals(packets.get(4),new byte[]{0x2e})&&java.util.Arrays.equals(packets.get(5),new byte[]{0x0d}),"official attack entity ID, empty swing, and client tick end payloads");
            check(attacks(f)==1&&f.connection.observation().position().x()==8.5,"sent attack is not hit or server position evidence");
            int size=f.output.size();f.control.poll();check(f.output.size()==size,"cooldown never sends immediate repeated attack");
            f.handle(healthMetadata(7,0));check("succeeded".equals(f.control.poll()),"exact spawn metadata death terminates");
            check(f.control.resultCode().equals("target_defeated_observed"),"death receipt names observation evidence");
            f.control.off();size=f.output.size();f.control.start(attack(2,7,1),()->true);f.control.poll();f.control.idle(()->true);
            check(f.output.size()==size&&closed(f),"OFF permits no new goal or maintenance writes");
        }
    }
    private static void beforePermission()throws Exception {
        try(ControlFixture f=armed()){
            AtomicInteger committed=new AtomicInteger();
            check(!f.connection.sendAttack(7,f.control.pose(),()->false,committed::incrementAndGet),"direct preflight rejects absent permission");
            check(f.output.size()==0&&committed.get()==0,"no byte or local commit before permission");
            f.control.start(attack(1,7,epoch(f)),()->false);
            check("failed".equals(f.control.poll())&&closed(f)&&f.output.size()==0,"host rejected authority closes without sending");
        }
    }
    private static void postWalkAttack()throws Exception {
        try(ControlFixture f=new ControlFixture(true)){
            f.control.start(ControlFixture.navigate(1,9,1,8),()->true);
            check("succeeded".equals(await(f,4000)),"short walk must complete before combat");
            check(Math.abs(f.control.pose().x()-9.5)<=.1500001,"walk uses actual simulated arrival pose");
            check(f.control.velocity().y()<0,"grounded walk retains its vanilla downward velocity");
            f.handle(ControlFixture.packet(0x12,d->d.write(BareHandInventoryTest.full(-1,false))));f.handle(new byte[]{0x6b,0});
            spawn(f,7,154,11.5,1,8.5);f.output.reset();
            f.control.start(attack(2,7,epoch(f)),()->true);
            check(f.control.poll()==null&&attacks(f)==1,"walked grounded player can commit first explicit attack");
            Thread.sleep(510);
            check(f.control.poll()==null&&attacks(f)==2&&!closed(f),"facing-only attack preserves grounded state for a second cooldown attack");
            List<byte[]> sent=frames(f.output);check(sent.stream().filter(p->p[0]==1).count()==2,"two cooldown attacks have two actual packet writes");
            f.handle(healthMetadata(7,0));check("succeeded".equals(f.control.poll()),"observed exact-spawn death completes post-walk combat");
            check(f.control.resultCode().equals("target_defeated_observed"),"post-walk combat success remains server-observed");
        }
    }
    private static void afterWrite(String change)throws Exception {
        try(ControlFixture f=armed()){
            AtomicBoolean lease=new AtomicBoolean(true);long targetEpoch=epoch(f);
            if(change.equals("unknown_approach"))spawn(f,8,159,55.5,1,8.5);
            HookOutput output=new HookOutput(()->{
                switch(change){
                    case "lease"->lease.set(false);
                    case "off"->f.control.off();
                    case "weapon"->f.handle(ControlFixture.packet(0x12,d->d.write(BareHandInventoryTest.full(36,false))));
                    case "reuse"->{f.handle(ControlFixture.packet(0x4e,d->d.write(EntityTrackerTest.remove(7))));spawn(f,7,154,10.5,1,8.5);}
                    case "metadata"->f.handle(ControlFixture.packet(0x65,d->{ControlFixture.vi(d,7);d.write(new byte[]{16,8,1,(byte)255});}));
                    case "unknown_approach"->{f.handle(ControlFixture.packet(0x4e,d->d.write(EntityTrackerTest.remove(8))));spawn(f,8,159,8.5,1,15.5);}
                    default->throw new AssertionError();
                }
            });
            ControlFixture.set(f.connection,"out",output);
            f.control.start(attack(1,7,targetEpoch),lease::get);
            boolean rejected=false;try{f.control.poll();}catch(IOException expected){rejected=true;}
            check(rejected||"failed".equals(f.control.poll()),"post-write boundary must fail");
            check(closed(f)&&attacks(f)==0,"changed authority or target cannot become a committed attack count");
            check(output.size()>0,"case actually crossed complete wire write boundary");
            int size=output.size();f.control.idle(()->true);f.control.start(attack(2,7,targetEpoch),()->true);f.control.poll();
            check(size==output.size(),"unknown external effect never replays after closure");
        }
    }
    private static void bundleDeath()throws Exception {
        try(ControlFixture f=armed()){
            f.control.start(attack(1,7,epoch(f)),()->true);f.control.poll();int count=f.output.size();
            f.handle(new byte[]{0});f.handle(healthMetadata(7,0));
            check(f.control.poll()==null&&f.output.size()==count,"open bundle suspends attacks and terminal claims");
            check(!f.control.observation().getAsJsonArray("targets").get(0).getAsJsonObject().get("dead").getAsBoolean(),"death remains staged until delimiter");
            f.handle(new byte[]{0});check("succeeded".equals(f.control.poll())&&f.output.size()==count,"bundle death completes without another attack");
        }
    }
    private static void partialAttack(boolean encrypted)throws Exception {
        try(ControlFixture f=armed()){
            PartialOutput raw=new PartialOutput();ClosingSocket socket=new ClosingSocket(raw);OutputStream output=raw;
            if(encrypted){Cipher cipher=Cipher.getInstance("AES/CFB8/NoPadding");byte[] key=new byte[16];cipher.init(Cipher.ENCRYPT_MODE,new SecretKeySpec(key,"AES"),new IvParameterSpec(key));output=new CipherOutputStream(raw,cipher);}
            ControlFixture.set(f.connection,"socket",socket);ControlFixture.set(f.connection,"out",output);
            f.control.start(attack(1,7,epoch(f)),()->true);AtomicReference<Throwable> failure=new AtomicReference<>();
            Thread writer=new Thread(()->{try{f.control.poll();}catch(Throwable t){failure.set(t);}},"owned-attack-writer");writer.setDaemon(true);writer.start();
            check(raw.first.await(2,TimeUnit.SECONDS),"attack reached owned partial write");
            Thread cancel=new Thread(()->f.control.fence(1),"owned-attack-cancel");cancel.setDaemon(true);cancel.start();cancel.join(2000);writer.join(2000);
            check(!writer.isAlive()&&!cancel.isAlive(),"attack cancellation cannot deadlock lifetime against send lock");
            check(closed(f)&&socket.closes.get()==1,"partial attack cancellation closes exactly the owned socket");
            check(raw.bytes.get()==1&&attacks(f)==0&&failure.get() instanceof IOException,"partial attack emits no later bytes or committed count");
            f.control.idle(()->true);check(raw.bytes.get()==1,"partial attack never replays after cancellation");
        }
    }
    private static void reuseBefore()throws Exception {
        try(ControlFixture f=armed()){
            long previous=epoch(f);f.handle(ControlFixture.packet(0x4e,d->d.write(EntityTrackerTest.remove(7))));spawn(f,7,154,10.5,1,8.5);
            check(epoch(f)>previous,"same entity ID is a different spawn");f.control.start(attack(1,7,previous),()->true);
            check("failed".equals(f.control.poll())&&f.output.size()==0,"old spawn grant never attacks replacement");
        }
    }
    private static void otherMob()throws Exception {
        try(ControlFixture f=armed()){
            spawn(f,8,32,8.5,1,12);f.control.start(attack(1,7,epoch(f)),()->true);
            check("failed".equals(f.control.poll())&&f.output.size()==0,"excluding target cannot exclude another nearby monster");
        }
    }
    private static void weaponBefore()throws Exception {
        try(ControlFixture f=armed()){
            f.handle(ControlFixture.packet(0x12,d->d.write(BareHandInventoryTest.full(36,false))));
            f.control.start(attack(1,7,epoch(f)),()->true);
            check("failed".equals(f.control.poll())&&f.output.size()==0,"observed weapon rejects bare-hand controller");
        }
    }
    private static void jumpHeadroom()throws Exception {
        try(ControlFixture f=new ControlFixture(true)){
            f.handle(ControlFixture.block(8,3,8,1));JsonObject jump=ControlFixture.look(1,0,0);jump.addProperty("action","jump");jump.remove("yaw");jump.remove("pitch");
            f.control.start(jump,()->true);check("failed".equals(await(f,1500)),"jump rejects solid headroom");
            check(f.output.size()==0&&closed(f),"blocked jump sends no input and closes");
        }
    }
    private static void halfSlab()throws Exception {
        try(ControlFixture f=new ControlFixture(true)){
            f.handle(ControlFixture.block(9,1,8,15252));JsonObject command=ControlFixture.navigate(1,9,1,8);command.addProperty("target_y",1.5);
            f.control.start(command,()->true);check("succeeded".equals(await(f,5000)),"known bottom slab reaches half height");
            check(Math.abs(f.control.pose().y()-1.5)<1e-7&&Math.abs(f.control.pose().x()-9.5)<=.1500001,"half slab arrival uses stated geometry and tolerance");
            check(f.connection.observation().position().y()==1,"simulation never overwrites observed correction");
        }
    }
    private static void stairs()throws Exception {
        try(ControlFixture f=new ControlFixture(true)){
            f.handle(ControlFixture.block(8,1,7,5474));f.handle(ControlFixture.block(8,1,6,1));
            f.control.start(ControlFixture.navigate(1,8,2,6),()->true);
            check("succeeded".equals(await(f,6000)),"dry north stair leads to raised full block");
            check(Math.abs(f.control.pose().y()-2)<1e-7&&Math.abs(f.control.pose().z()-6.5)<=.1500001,"stairs preserve full target body support");
        }
    }
    private static void waterTags()throws Exception {
        for(int mode=0;mode<4;mode++)try(ControlFixture f=new ControlFixture(true)){
            fillWater(f);if(mode>0)tags(f,mode>=2,mode!=3);
            f.control.start(ControlFixture.navigate(1,8,1,8),()->true);String result=mode==2?await(f,3000):f.control.poll();
            check(mode==2?"succeeded".equals(result):"failed".equals(result),"water requires both pinned tag registries, mode="+mode);
            if(mode!=2)check(f.output.size()==0,"unconfirmed fluid semantics sends no movement");
        }
    }
    private static void waterMaintenance(boolean ttl)throws Exception {
        try(ControlFixture f=waterReady()){
            check(f.control.observation().getAsJsonObject("locomotion").get("maintenance").getAsBoolean(),"water arrival retains explicit maintenance");
            int before=f.output.size();Thread.sleep(55);f.control.idle(()->true);
            check(f.output.size()>before&&!closed(f),"live original lease maintains water pose");
            if(ttl){Object movement=ControlFixture.get(f.control,"movement");ControlFixture.set(movement,"deadline",System.nanoTime()-1);Thread.sleep(55);f.control.idle(()->true);}
            else f.control.idle(()->false);
            check(closed(f),"water maintenance ends on "+(ttl?"original deadline":"lost lease"));
            int size=f.output.size();f.control.idle(()->true);check(size==f.output.size(),"closed water host never resumes maintenance");
        }
    }
    private static void waterCancel()throws Exception {
        try(ControlFixture f=new ControlFixture(true)){
            fillWater(f);tags(f,true);f.control.start(ControlFixture.navigate(1,9,1,8),()->true);f.control.poll();
            f.control.fence(1);f.control.stop();int before=f.output.size();f.control.idle(()->true);f.control.poll();
            check(closed(f)&&f.output.size()==before,"water cancellation closes ownership without drift replay");
        }
    }
    private static void maintenanceCancel(boolean encrypted)throws Exception {
        try(ControlFixture f=waterReady()){
            PartialOutput raw=new PartialOutput();ClosingSocket socket=new ClosingSocket(raw);OutputStream output=raw;
            if(encrypted){Cipher cipher=Cipher.getInstance("AES/CFB8/NoPadding");byte[] key=new byte[16];cipher.init(Cipher.ENCRYPT_MODE,new SecretKeySpec(key,"AES"),new IvParameterSpec(key));output=new CipherOutputStream(raw,cipher);}
            ControlFixture.set(f.connection,"socket",socket);ControlFixture.set(f.connection,"out",output);
            MovementController.Pose before=f.control.pose();AtomicReference<Throwable> failure=new AtomicReference<>();Thread.sleep(55);
            Thread writer=new Thread(()->{try{f.control.idle(()->true);}catch(Throwable t){failure.set(t);}},"owned-maintenance-writer");writer.setDaemon(true);writer.start();
            check(raw.first.await(2,TimeUnit.SECONDS),"terminal water maintenance reached partial write");
            Thread cancel=new Thread(()->f.control.fence(1),"owned-maintenance-cancel");cancel.setDaemon(true);cancel.start();cancel.join(1000);writer.join(1000);
            check(!writer.isAlive()&&!cancel.isAlive(),"terminal maintenance cancellation immediately unblocks writer without deadlock");
            check(closed(f)&&socket.closes.get()==1,"terminal water fence closes exactly its owned socket");
            check(raw.bytes.get()==1&&failure.get() instanceof IOException&&f.control.pose().equals(before),"partial maintenance commits no pose or following byte");
            f.control.idle(()->true);check(raw.bytes.get()==1,"cancelled maintenance never resumes from retained lease");
        }
    }
    private static void waterAir()throws Exception {
        try(ControlFixture f=waterReady()){
            f.handle(air(60));check(!closed(f),"documented air threshold itself remains accepted");
            boolean rejected=false;try{f.handle(air(59));}catch(VanillaConnection.Failure expected){rejected=expected.code().equals("low_air_supply");}
            check(rejected,"low server-observed air rejects continued immersion");
            f.control.idle(()->true);check(closed(f),"idle sees low air and closes even before receive-loop unwinding");
        }
    }
    private static void maintenanceBundle()throws Exception {
        try(ControlFixture f=waterReady()){
            f.handle(new byte[]{0});int before=f.output.size();
            f.control.idle(()->true);
            check(!closed(f),"normal bundle suspends terminal water maintenance without disconnect");
            check(f.output.size()==before,"open bundle permits no maintenance write");
            Thread.sleep(55);f.handle(new byte[]{0});f.control.idle(()->true);
            check(f.connection.controlReady(),"bounded bundle end retains original water ownership");
            check(f.output.size()>before,"maintenance resumes only after complete bundle publication");
        }
        try(ControlFixture f=waterReady()){
            f.handle(new byte[]{0});int before=f.output.size();f.control.idle(()->false);
            check(closed(f),"bundle suspension cannot delay lost lease cleanup");
            check(f.output.size()==before,"cancel during bundle sends no maintenance frame");
        }
    }
    private static ControlFixture waterReady()throws Exception {
        ControlFixture f=new ControlFixture(true);fillWater(f);tags(f,true);
        f.control.start(ControlFixture.navigate(1,8,1,8),()->true);
        check("succeeded".equals(await(f,3000)),"bounded water hold arrives");return f;
    }
    private static void terminalEnvironment()throws Exception {
        try(ControlFixture f=new ControlFixture(true)){
            f.control.idle(()->false);
            check(f.connection.controlReady(),"initial sequence-zero idle remains connected without authority");
            check(f.output.size()==0,"initial sequence-zero idle is a no-op");
        }
        List<String> rejected=new ArrayList<>();
        for(boolean water:List.of(false,true))for(boolean lease:List.of(false,true))try(ControlFixture f=new ControlFixture(true)){
            f.control.start(ControlFixture.look(1,0,0),()->true);
            check("succeeded".equals(await(f,2000)),"dry explicit look completes before external environment change");
            check(!f.control.observation().getAsJsonObject("locomotion").get("maintenance").getAsBoolean(),"completed dry look has no cached maintenance");
            if(water){tags(f,true);fillWater(f);}else f.handle(ControlFixture.block(8,0,8,0));
            int before=f.output.size();f.control.idle(()->lease);
            check(closed(f),"terminal physical change closes without stale cached grounding: water="+water+", lease="+lease);
            check(f.output.size()==before,"environment change grants no autonomous movement");
            f.control.idle(()->true);check(f.output.size()==before,"closed terminal environment never restarts maintenance");
        }catch(AssertionError failure){rejected.add(failure.getMessage());}
        if(!rejected.isEmpty())throw new AssertionError(rejected.toString());
    }
    private static void fillWater(ControlFixture f)throws Exception {
        f.handle(new byte[]{0});for(int x=5;x<=13;x++)for(int z=5;z<=11;z++)for(int y=1;y<=3;y++)f.handle(ControlFixture.block(x,y,z,89));f.handle(new byte[]{0});
    }
    private static void tags(ControlFixture f,boolean fluid)throws Exception {
        tags(f,fluid,true);
    }
    private static void tags(ControlFixture f,boolean fluid,boolean correct)throws Exception {
        var ids=BlockCatalog.loadPinned().defaultFlowBlockIds();f.handle(ControlFixture.packet(0x89,d->{
            ControlFixture.vi(d,fluid?2:1);ControlFixture.string(d,"minecraft:block");ControlFixture.vi(d,1);ControlFixture.string(d,"minecraft:blocks_fluid_flow");ControlFixture.vi(d,ids.size());for(int id:ids)ControlFixture.vi(d,id);
            if(fluid){ControlFixture.string(d,"minecraft:fluid");ControlFixture.vi(d,1);ControlFixture.string(d,"minecraft:water");ControlFixture.vi(d,correct?2:1);ControlFixture.vi(d,1);if(correct)ControlFixture.vi(d,2);}
        }));
    }
    private static byte[] air(int value)throws IOException{return ControlFixture.packet(0x65,d->{ControlFixture.vi(d,42);d.writeByte(1);ControlFixture.vi(d,1);ControlFixture.vi(d,value);d.writeByte(255);});}
    private static byte[] healthMetadata(int id,float value)throws IOException{return ControlFixture.packet(0x65,d->{ControlFixture.vi(d,id);d.writeByte(9);ControlFixture.vi(d,3);d.writeFloat(value);d.writeByte(255);});}
    private static void spawn(ControlFixture f,int id,int type,double x,double y,double z)throws Exception{f.handle(ControlFixture.packet(1,d->d.write(EntityTrackerTest.spawn(id,type,x,y,z))));}
    private static long epoch(ControlFixture f){return f.control.observation().getAsJsonArray("targets").get(0).getAsJsonObject().get("spawn_epoch").getAsLong();}
    private static int attacks(ControlFixture f){return f.control.observation().getAsJsonObject("combat").get("attacks_sent").getAsInt();}
    private static boolean closed(ControlFixture f){return f.connection.snapshot().stage()==VanillaConnection.Stage.CLOSED;}
    private static JsonObject attack(int seq,int target,long epoch){JsonObject c=new JsonObject();c.addProperty("seq",seq);c.addProperty("action","attack");c.addProperty("run_ms",5000);c.addProperty("target_id",target);c.addProperty("target_epoch",epoch);return c;}
    private static String await(ControlFixture f,long millis)throws Exception{long end=System.nanoTime()+millis*1_000_000;do{String result=f.control.poll();if(result!=null)return result;Thread.sleep(25);}while(System.nanoTime()<end);throw new AssertionError("bounded goal did not finish");}
    private static List<byte[]> frames(ByteArrayOutputStream output)throws IOException{ByteArrayInputStream in=new ByteArrayInputStream(output.toByteArray());List<byte[]> result=new ArrayList<>();while(in.available()>0){int size=0,shift=0,b;do{b=in.read();if(b<0||shift>28)throw new IOException("bad_frame");size|=(b&127)<<shift;shift+=7;}while((b&128)!=0);byte[] p=in.readNBytes(size);if(p.length!=size)throw new IOException("truncated_frame");result.add(p);}return result;}
    private static final class HookOutput extends ByteArrayOutputStream {private final Checked hook;private boolean fired;HookOutput(Checked hook){this.hook=hook;}@Override public void flush()throws IOException{if(!fired){fired=true;try{hook.run();}catch(IOException e){throw e;}catch(Exception e){throw new IOException("owned_hook",e);}}}}
    private static final class PartialOutput extends OutputStream {
        final CountDownLatch first=new CountDownLatch(1),closed=new CountDownLatch(1);final AtomicInteger bytes=new AtomicInteger();
        @Override public void write(int value)throws IOException{if(closed.getCount()==0)throw new IOException("owned_closed");bytes.incrementAndGet();first.countDown();try{if(!closed.await(3,TimeUnit.SECONDS))throw new IOException("owned_timeout");}catch(InterruptedException interrupted){Thread.currentThread().interrupt();}throw new IOException("owned_closed");}
    }
    private static final class ClosingSocket extends Socket {
        final PartialOutput output;final AtomicInteger closes=new AtomicInteger();ClosingSocket(PartialOutput output){this.output=output;}
        @Override public synchronized void close(){closes.incrementAndGet();output.closed.countDown();}
    }
    private static void check(boolean condition,String why){if(!condition)throw new AssertionError(why);checks++;}
    @FunctionalInterface interface Checked{void run()throws Exception;}
}
