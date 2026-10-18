package dev.evelyn.minecraft;

import java.io.*;
import java.net.Socket;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import javax.crypto.Cipher;
import javax.crypto.CipherOutputStream;
import javax.crypto.spec.IvParameterSpec;
import javax.crypto.spec.SecretKeySpec;

/** Actual connection/controller glue over owned memory streams, never a game connection. */
public final class VanillaControlTest {
    private static int checks;
    public static void main(String[] args)throws Exception{
        if(args.length==1){switch(args[0]){case "client-ticks"->clientTickBoundary();case "tick-baseline"->tickBaseline();case "metadata-drop"->metadataDrop();case "metadata-zero"->metadataZero();case "metadata-tail"->metadataTail();case "game-mode"->gameModeChanged();case "game-event-normal"->gameEventNormal();case "game-event-invalid"->gameEventInvalid();case "bundle"->bundleAtomic();case "bundle-off"->bundleOff();case "entity-segment"->entitySegment();default->throw new IllegalArgumentException();}
            System.out.println("VANILLA_CONTROL_TARGET_PASS "+checks);return;}
        navigate();look();beforeCommit();relativeCorrection();blockedAndUnknown();damageAndMonster();metadataDrop();metadataZero();metadataTail();selfBaselines();tickBaseline();clientTickBoundary();gameModeChanged();gameEventNormal();gameEventInvalid();bundleAtomic();bundleOff();entitySegment();off();partialWrite(false);partialWrite(true);
        System.out.println("VANILLA_CONTROL_PASS "+checks+" checks (owned memory streams, no game/auth)");
    }
    private static void navigate()throws Exception{
        try(ControlFixture f=new ControlFixture()){
            check(f.connection.controlReady(),"wire fixture reached controlled readiness");
            check(f.connection.observation().position().x()==8.5,"received initial correction");
            f.control.start(ControlFixture.navigate(1,9,1,8),()->true);
            check("succeeded".equals(await(f,7000)),"short flat navigate reaches goal");
            check("goal_reached_client_simulated".equals(f.control.resultCode()),"success labels local simulation");
            check(Math.abs(f.control.pose().x()-9.5)<=.15,"predicted position meets tolerance");
            check(f.connection.observation().position().x()==8.5,"movement never invents a server correction");
            List<byte[]> frames=frames(f.output.toByteArray());check(frames.getFirst()[0]==0x2c,"loaded precedes first voluntary frame");
            check(frames.stream().filter(b->b[0]==0x2c).count()==1,"loaded sent exactly once");
            check(frames.stream().anyMatch(b->b[0]==0x2b&&b.length==2&&b[1]==1),"forward semantic input sent");
            for(byte[] b:frames)if(b[0]==0x1f){check(b.length==34,"posrot packet has exact layout");DataInputStream d=new DataInputStream(new ByteArrayInputStream(b,1,b.length-1));
                double x=d.readDouble(),y=d.readDouble(),z=d.readDouble();check(x>=8.5&&x<10&&y==1&&Math.abs(z-8.5)<.01,"finite incremental flat motion");}
        }
    }
    private static void look()throws Exception{
        try(ControlFixture f=new ControlFixture()){
            f.control.start(ControlFixture.look(1,90,30),()->true);check("succeeded".equals(await(f,3000)),"look completes");
            check(f.control.pose().yaw()==90&&f.control.pose().pitch()==30,"look angles applied");
            check(f.control.pose().x()==8.5&&f.control.pose().z()==8.5,"look does not translate");
        }
    }
    private static void beforeCommit()throws Exception{
        try(ControlFixture f=new ControlFixture()){
            AtomicInteger commits=new AtomicInteger();boolean sent=f.connection.sendControl(step(),()->false,commits::incrementAndGet);
            check(!sent&&commits.get()==0&&f.output.size()==0,"revoked permit before commit sends zero bytes");
            f.control.start(ControlFixture.navigate(1,9,1,8),()->true);f.control.fence(1);
            check("failed".equals(f.control.poll())&&f.output.size()==0,"cancel fence before poll sends zero bytes");
        }
    }
    private static void relativeCorrection()throws Exception{
        try(ControlFixture f=new ControlFixture()){
            f.control.start(ControlFixture.navigate(1,10,1,8),()->true);long until=System.nanoTime()+3_000_000_000L;
            while(f.control.pose().x()<8.55&&System.nanoTime()<until){f.control.poll();Thread.sleep(25);}
            double predicted=f.control.pose().x();check(predicted>8.55,"movement advanced before relative correction");
            f.handle(ControlFixture.position(1,1,8.5,1));
            check(Math.abs(f.control.pose().x()-(predicted+1))<1e-9,"relative correction uses predicted current pose");
            check(Math.abs(f.connection.observation().position().x()-(predicted+1))<1e-9,"corrected readback shares resolved base");
            check("failed".equals(f.control.poll())&&f.control.resultCode().equals("server_correction"),"correction terminates old goal");
        }
    }
    private static void blockedAndUnknown()throws Exception{
        try(ControlFixture f=new ControlFixture()){
            f.handle(ControlFixture.block(9,1,8,1));f.control.start(ControlFixture.navigate(1,9,1,8),()->true);
            check("failed".equals(await(f,1000)),"solid target fails bounded route");check(f.output.size()==0,"blocked target emits no voluntary packet");
        }
        try(ControlFixture f=new ControlFixture()){
            f.handle(ControlFixture.packet(0x26,d->{d.writeInt(0);d.writeInt(0);}));f.control.start(ControlFixture.navigate(1,9,1,8),()->true);
            check("failed".equals(f.control.poll())&&f.output.size()==0,"unknown support fails without movement");
        }
    }
    private static void damageAndMonster()throws Exception{
        try(ControlFixture f=new ControlFixture()){
            f.control.start(ControlFixture.navigate(1,11,1,8),()->true);f.control.poll();
            f.handle(ControlFixture.health(19));f.handle(ControlFixture.health(20));f.control.poll();
            var obs=f.control.observation();check(obs.getAsJsonObject("risk").get("damage_epoch").getAsLong()==1,"damage then heal retains damage epoch");
            check(obs.getAsJsonObject("planner").get("preemptions").getAsInt()==1,"healed damage still preempts goal");
        }
        try(ControlFixture f=new ControlFixture()){
            f.control.start(ControlFixture.navigate(1,11,1,8),()->true);f.control.poll();
            f.handle(new byte[]{0});f.handle(ControlFixture.packet(1,d->d.write(EntityTrackerTest.spawn(99,32,8.5,1,13))));
            f.handle(ControlFixture.packet(0x4e,d->d.write(EntityTrackerTest.remove(99))));f.handle(new byte[]{0});f.control.poll();
            var obs=f.control.observation();check(!obs.getAsJsonObject("risk").get("near_monster").getAsBoolean(),"removed monster not claimed currently near");
            check(obs.getAsJsonObject("risk").get("monster_epoch").getAsLong()>0,"bundle retains brief monster encounter");
            check(obs.getAsJsonObject("planner").get("preemptions").getAsInt()==1,"brief bundled encounter still preempts");
        }
    }
    private static void off()throws Exception{
        try(ControlFixture f=new ControlFixture()){
            f.control.off();f.control.start(ControlFixture.look(1,90,0),()->true);
            check("failed".equals(f.control.poll())&&f.output.size()==0,"OFF refuses fresh command");check(f.connection.snapshot().stage()==VanillaConnection.Stage.CLOSED,"OFF closes owned connection");
        }
    }
    private static void selfBaselines()throws Exception{
        try(ControlFixture f=new ControlFixture()){
            f.handle(attribute(26,(double).1f));
            f.handle(metadata(6,20,0));f.handle(metadata(15,42,1));
            f.control.start(ControlFixture.look(1,30,0),()->true);
            check("succeeded".equals(await(f,3000)),"official float promoted speed and standing/right-hand metadata remain ready");
        }
        byte[][] unsupported={attribute(26,.1d),attribute(30,2d),metadata(6,20,1),metadata(5,8,1),metadata(0,0,2),
            ControlFixture.packet(0x82,d->{d.writeFloat(20);d.writeBoolean(true);})};
        for(byte[] packet:unsupported)try(ControlFixture f=new ControlFixture()){
            boolean rejected=false;try{f.handle(packet);}catch(IOException expected){rejected=true;f.connection.close();}
            check(rejected,"unsupported physics baseline rejects packet");check(f.output.size()==0,"unsupported state emits no voluntary frame");
            check(f.connection.snapshot().stage()==VanillaConnection.Stage.CLOSED,"receive caller closes rejected state");
        }
        try(ControlFixture f=new ControlFixture()){
            f.control.start(ControlFixture.look(1,30,0),()->true);f.handle(ControlFixture.health(19));
            check("failed".equals(f.control.poll())&&f.output.size()==0,"damage has priority over first shallow look send");
        }
    }
    private static void clientTickBoundary()throws Exception{
        for(boolean attackFirst:new boolean[]{false,true})try(ControlFixture f=new ControlFixture()){
            long before=f.connection.snapshot().packetsSent();
            if(attackFirst){
                f.connection.sendAttack(99,step().pose(),()->true,()->{});
                f.connection.sendAttack(99,step().pose(),()->true,()->{});
            }else{
                f.connection.sendControl(step(),()->true,()->{});
                f.connection.sendControl(step(),()->true,()->{});
                f.connection.sendAttack(99,step().pose(),()->true,()->{});
            }
            List<byte[]> packets=frames(f.output.toByteArray());boolean positionThisTick=false;int positions=0,ends=0;
            for(byte[] packet:packets){
                if(packet[0]==0x1f){check(!positionThisTick,"second position requires prior CLIENT_TICK_END");positionThisTick=true;positions++;}
                if(packet[0]==0x0d){check(packet.length==1&&positionThisTick,"tick end has empty payload and follows position");positionThisTick=false;ends++;}
            }
            check(!positionThisTick&&ends==positions&&positions==(attackFirst?2:3),"every control and attack step completes its client tick");
            check(packets.stream().filter(p->p[0]==0x2c).count()==1,"tick boundaries preserve one loaded notification");
            check(f.connection.snapshot().packetsSent()-before==packets.size(),"packet accounting includes tick ends");
        }
    }
    private static void tickBaseline()throws Exception{
        try(ControlFixture f=new ControlFixture()){
            f.control.start(ControlFixture.look(1,30,0),()->true);
            f.handle(ControlFixture.packet(0x82,d->{d.writeFloat(20);d.writeBoolean(false);}));
            f.handle(ControlFixture.packet(0x83,d->ControlFixture.vi(d,0)));
            check(f.connection.controlReady()&&f.output.size()==0,"ordinary joining tick baseline preserves readiness without sending");
            check("succeeded".equals(await(f,3000)),"zero pending frozen ticks preserves queued control");
        }
        byte[][] unsupported={ControlFixture.packet(0x83,d->ControlFixture.vi(d,1)),
            ControlFixture.packet(0x83,d->ControlFixture.vi(d,Integer.MAX_VALUE)),
            ControlFixture.packet(0x83,d->ControlFixture.vi(d,-1)),
            ControlFixture.packet(0x83,d->{ControlFixture.vi(d,0);d.writeByte(0);}),
            ControlFixture.packet(0x82,d->{d.writeFloat(19);d.writeBoolean(false);}),
            ControlFixture.packet(0x82,d->{d.writeFloat(20);d.writeBoolean(true);})};
        for(byte[] packet:unsupported)try(ControlFixture f=new ControlFixture()){
            f.control.start(ControlFixture.look(1,30,0),()->true);
            boolean rejected=false;try{f.handle(packet);}catch(IOException expected){rejected=true;f.connection.close();}
            check(rejected,"tick stepping, malformed input and altered ticking remain rejected");
            check(f.output.size()==0&&f.connection.snapshot().stage()==VanillaConnection.Stage.CLOSED,"rejected tick state closes without voluntary frames");
        }
    }
    private static void metadataDrop()throws Exception{
        try(ControlFixture f=new ControlFixture()){
            f.handle(metadataHealth(20,false));f.control.start(ControlFixture.navigate(1,11,1,8),()->true);f.control.poll();
            f.handle(metadataHealth(19,false));f.handle(metadataHealth(20,false));f.control.poll();
            var observation=f.control.observation();check(observation.getAsJsonObject("risk").get("damage_epoch").getAsLong()==1,"metadata health drop then heal retains damage epoch");
            check(observation.getAsJsonObject("planner").get("preemptions").getAsInt()==1,"metadata damage preempts running goal");
            check(f.connection.observation().health().health()==20,"metadata risk never overwrites HUD-only observation provenance");
        }
        try(ControlFixture f=new ControlFixture()){
            f.control.start(ControlFixture.look(1,30,0),()->true);f.handle(metadataHealth(19,false));f.handle(metadataHealth(20,false));
            check(f.control.observation().getAsJsonObject("risk").get("damage_epoch").getAsLong()>0,"first metadata health decrease is compared with received HUD baseline");
            check("failed".equals(f.control.poll())&&f.output.size()==0,"first metadata damage fences unsent look");
        }
    }
    private static void metadataZero()throws Exception{
        try(ControlFixture f=new ControlFixture()){
            f.handle(metadataHealth(0,false));
            check(f.connection.snapshot().stage()==VanillaConnection.Stage.CLOSED,"metadata death immediately closes controlled connection");
            check(f.output.size()==0,"metadata death emits no voluntary movement");
        }
    }
    private static void metadataTail()throws Exception{
        try(ControlFixture f=new ControlFixture()){
            f.handle(metadataHealth(20,false));long before=f.control.observation().getAsJsonObject("risk").get("damage_epoch").getAsLong();
            boolean rejected=false;try{f.handle(metadataHealth(19,true));}catch(IOException expected){rejected=true;}
            check(rejected,"trailing metadata body rejects packet");
            check(f.control.observation().getAsJsonObject("risk").get("damage_epoch").getAsLong()==before,"malformed metadata health prefix does not commit damage");
            check(f.connection.snapshot().stage()==VanillaConnection.Stage.CLOSED,"malformed known control packet closes before decoder releases authority");
            check(((Float)ControlFixture.get(f.control,"lastMetadataHealth"))==20f,"failed prefix cannot replace metadata health baseline");
        }
    }
    private static byte[] metadataHealth(float health,boolean trailing)throws IOException{return ControlFixture.packet(0x65,d->{ControlFixture.vi(d,42);d.writeByte(9);ControlFixture.vi(d,3);d.writeFloat(health);d.writeByte(255);if(trailing)d.writeByte(0);});}
    private static void gameModeChanged()throws Exception{
        try(ControlFixture f=new ControlFixture()){
            f.control.start(ControlFixture.navigate(1,9,1,8),()->true);
            try{f.handle(gameEvent(3,2));}catch(IOException rejected){/* Authority must already be closed inside the lifetime boundary. */}
            check(f.connection.snapshot().stage()==VanillaConnection.Stage.CLOSED,"adventure mode closes survival-only connection despite unchanged abilities");
            check("failed".equals(f.control.poll())&&f.output.size()==0,"game-mode change fences old goal before any movement");
            f.control.start(ControlFixture.look(2,90,0),()->true);
            check("failed".equals(f.control.poll())&&f.output.size()==0,"mode invalidation cannot admit a fresh goal");
        }
    }
    private static void gameEventNormal()throws Exception{
        try(ControlFixture f=new ControlFixture()){
            f.control.start(ControlFixture.look(1,30,0),()->true);
            f.handle(gameEvent(3,0));f.handle(gameEvent(3,0));
            f.handle(gameEvent(1,0));f.handle(gameEvent(2,0));f.handle(gameEvent(7,.5f));f.handle(gameEvent(8,.5f));
            check(f.connection.controlReady(),"duplicate survival and weather events preserve readiness");
            check("succeeded".equals(await(f,3000)),"normal unrelated game events preserve active look");
            check(f.control.pose().x()==8.5&&f.control.pose().z()==8.5,"normal event handling never teleports");
        }
        // The read-only branch retains its prior finite-event behavior; no controller is installed.
        try(VanillaConnection connection=new VanillaConnection(new java.util.UUID(0,10),"Synthetic",hash->{throw new AssertionError("no auth");})){
            ControlFixture.set(connection,"stage",VanillaConnection.Stage.PLAY);
            java.lang.reflect.Method handle=VanillaConnection.class.getDeclaredMethod("handlePacket",byte[].class);handle.setAccessible(true);
            handle.invoke(connection,(Object)gameEvent(3,2));
            check(connection.snapshot().stage()==VanillaConnection.Stage.PLAY,"v2 read-only adventure event remains observational");
        }
    }
    private static void gameEventInvalid()throws Exception{
        for(float value:new float[]{Float.NaN,1.5f,-1,4})try(ControlFixture f=new ControlFixture()){
            f.control.start(ControlFixture.look(1,90,0),()->true);boolean rejected=false;
            try{f.handle(gameEvent(3,value));}catch(IOException expected){rejected=true;f.connection.close();}
            check(rejected,"malformed or out-of-range mode is rejected");
            check(f.connection.snapshot().stage()==VanillaConnection.Stage.CLOSED&&f.output.size()==0,"invalid mode closes without a movement frame");
        }
    }
    private static byte[] gameEvent(int event,float value)throws IOException{return ControlFixture.packet(0x27,d->{d.writeByte(event);d.writeFloat(value);});}
    private static void bundleAtomic()throws Exception{
        try(ControlFixture f=new ControlFixture()){
            f.control.start(ControlFixture.navigate(1,11,1,8),()->true);
            var before=f.control.observation();f.handle(new byte[]{0});f.handle(ControlFixture.position(9.5,1,8.5,0));
            f.handle(ControlFixture.health(19));f.handle(ControlFixture.health(20));
            var during=f.control.observation();
            check(during.get("seq").getAsLong()>before.get("seq").getAsLong(),"bundle wrapper sequence remains fresh");
            check(stableProjection(before).equals(stableProjection(during)),"open bundle cannot mix prior self with live motion planner or risk");
            check(f.control.pose().x()==9.5,"live correction applies immediately despite frozen public projection");
            check("failed".equals(f.control.poll()),"live correction fences prior goal within bundle");
            check(stableProjection(before).equals(stableProjection(f.control.observation())),"poll cannot leak invalidated planner into open bundle");
            f.handle(new byte[]{0});var after=f.control.observation();
            check(after.getAsJsonObject("self").getAsJsonObject("position").get("x").getAsDouble()==9.5
                    &&after.getAsJsonObject("motion").get("x").getAsDouble()==9.5,"bundle release publishes matching self and motion");
            check(after.getAsJsonObject("risk").get("damage_epoch").getAsLong()==1,"bundle release retains drop then heal damage");
            check(!after.get("enabled").getAsBoolean(),"bundle release publishes correction-invalidated authority");
            check(frames(f.output.toByteArray()).stream().noneMatch(b->b[0]==0x1f||b[0]==0x2b||b[0]==0x2c),"bundle correction response never permits voluntary frames");
        }
    }
    private static com.google.gson.JsonObject stableProjection(com.google.gson.JsonObject value){var stable=value.deepCopy();stable.remove("seq");stable.remove("sampled_at_ms");return stable;}
    private static void bundleOff()throws Exception{
        try(ControlFixture f=new ControlFixture()){
            f.control.start(ControlFixture.look(1,30,0),()->true);f.handle(new byte[]{0});f.control.off();var value=f.control.observation();
            check(!value.getAsJsonObject("self").get("available").getAsBoolean(),"OFF overrides frozen bundle self immediately");
            check(value.get("motion").isJsonNull()&&!value.get("enabled").getAsBoolean(),"OFF clears frozen motion and authority immediately");
            check(f.connection.snapshot().stage()==VanillaConnection.Stage.CLOSED&&f.output.size()==0,"OFF inside bundle closes without a voluntary frame");
        }
    }
    private static void entitySegment()throws Exception{
        try(ControlFixture f=new ControlFixture()){
            // The first walking increment is 0.098 m. Both endpoints lie outside the 2 m
            // buffer, but their midpoint penetrates it; a non-monster avoids the separate danger policy.
            f.handle(ControlFixture.packet(1,d->d.write(EntityTrackerTest.spawn(99,11,8.549,1,10.4998))));
            f.control.start(ControlFixture.navigate(1,9,1,8),()->true);
            check("failed".equals(await(f,3000)),"swept entity buffer rejects first translated frame");
            List<byte[]> sent=frames(f.output.toByteArray());
            check(sent.stream().noneMatch(b->b[0]==0x2b&&b[1]==1),"no forward input is committed through segment interior");
            for(byte[] frame:sent)if(frame[0]==0x1f){DataInputStream d=new DataInputStream(new ByteArrayInputStream(frame,1,frame.length-1));check(d.readDouble()==8.5,"safe look frames never hide a translated segment");}
            check(f.connection.snapshot().stage()==VanillaConnection.Stage.CLOSED,"unsafe swept movement closes one-shot connection");
        }
    }
    private static byte[] attribute(int id,double base)throws IOException{return ControlFixture.packet(0x86,d->{ControlFixture.vi(d,42);ControlFixture.vi(d,1);ControlFixture.vi(d,id);d.writeDouble(base);ControlFixture.vi(d,0);});}
    private static byte[] metadata(int key,int serializer,int value)throws IOException{return ControlFixture.packet(0x65,d->{ControlFixture.vi(d,42);d.writeByte(key);ControlFixture.vi(d,serializer);d.writeByte(value);d.writeByte(255);});}
    private static void partialWrite(boolean encrypted)throws Exception{
        try(ControlFixture f=new ControlFixture()){
            BlockingOutput raw=new BlockingOutput();ClosingSocket socket=new ClosingSocket(raw);
            OutputStream out=raw;
            if(encrypted){Cipher cipher=Cipher.getInstance("AES/CFB8/NoPadding");byte[] key=new byte[16];cipher.init(Cipher.ENCRYPT_MODE,new SecretKeySpec(key,"AES"),new IvParameterSpec(key));out=new CipherOutputStream(raw,cipher);}
            ControlFixture.set(f.connection,"socket",socket);ControlFixture.set(f.connection,"out",out);
            f.control.start(ControlFixture.navigate(1,9,1,8),()->true);AtomicReference<Throwable> error=new AtomicReference<>();AtomicInteger commits=new AtomicInteger();
            Thread writer=new Thread(()->{try{f.connection.sendControl(step(),()->true,commits::incrementAndGet);}catch(Throwable t){error.set(t);}},"owned-partial-writer");writer.setDaemon(true);writer.start();
            check(raw.first.await(2,TimeUnit.SECONDS),"writer reached first partial byte");
            Thread cancel=new Thread(()->f.control.fence(1),"owned-cancel");cancel.setDaemon(true);cancel.start();cancel.join(2000);writer.join(2000);
            check(!cancel.isAlive()&&!writer.isAlive(),"partial-write cancellation has no lock deadlock");
            check(socket.closes.get()==1&&f.connection.snapshot().stage()==VanillaConnection.Stage.CLOSED,"partial commit closes owned socket");
            check(raw.bytes.get()==1&&commits.get()==0&&error.get() instanceof IOException,"no following frame or simulated commit after partial write");
            int count=raw.bytes.get();try{f.connection.sendControl(step(),()->true,commits::incrementAndGet);}catch(IOException expected){}
            check(raw.bytes.get()==count,"closed stream never sends follow-up frame");
        }
    }
    private static MovementController.PreparedStep step(){return new MovementController.PreparedStep(1,1,1,1,System.nanoTime(),new MovementController.Pose(8.5,1,8.5,0,0),new MovementController.Velocity(0,0,0),true,false,false,false);}
    private static String await(ControlFixture f,long millis)throws Exception{long end=System.nanoTime()+millis*1_000_000;String result;do{result=f.control.poll();if(result!=null)return result;Thread.sleep(25);}while(System.nanoTime()<end);throw new AssertionError("bounded control did not terminate");}
    private static List<byte[]> frames(byte[] encoded)throws IOException{List<byte[]> out=new ArrayList<>();ByteArrayInputStream in=new ByteArrayInputStream(encoded);while(in.available()>0){int length=0,shift=0,b;do{b=in.read();if(b<0||shift>28)throw new IOException();length|=(b&127)<<shift;shift+=7;}while((b&128)!=0);byte[] p=in.readNBytes(length);if(p.length!=length)throw new IOException();out.add(p);}return out;}
    private static final class BlockingOutput extends OutputStream{
        final CountDownLatch first=new CountDownLatch(1),closed=new CountDownLatch(1);final AtomicInteger bytes=new AtomicInteger();
        @Override public void write(int b)throws IOException{if(closed.getCount()==0)throw new IOException("owned_closed");bytes.incrementAndGet();first.countDown();try{if(!closed.await(3,TimeUnit.SECONDS))throw new IOException("owned_timeout");}catch(InterruptedException e){Thread.currentThread().interrupt();}throw new IOException("owned_closed");}
    }
    private static final class ClosingSocket extends Socket{
        final BlockingOutput output;final AtomicInteger closes=new AtomicInteger();ClosingSocket(BlockingOutput output){this.output=output;}
        @Override public synchronized void close(){closes.incrementAndGet();output.closed.countDown();}
    }
    private static void check(boolean good,String why){if(!good)throw new AssertionError(why);checks++;}
}
