package dev.evelyn.minecraft;

import com.google.gson.JsonObject;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.BooleanSupplier;

/** Deterministic owned control callbacks only; no game or socket. */
public final class AdvancedSessionTest {
    private static int checks;
    private static int testedVersion=4;
    private static void check(boolean value,String message) { if(!value)throw new AssertionError(message);checks++; }
    private static final class Effect implements DirectClient.ControlExecutor {
        int starts,stops,offs,fences,idleTrue,idleFalse; String result,code="arrived";
        BooleanSupplier activePermit,idlePermit; Runnable duringIdle; boolean failIdle;
        public void start(JsonObject command,BooleanSupplier permit) {starts++;activePermit=permit;}
        public String poll(){return result;}
        public void stop(){stops++;}
        public void off(){offs++;}
        public void fence(int sequence){fences++;}
        public String resultCode(){return code;}
        public void idle(BooleanSupplier permit) throws Exception {
            idlePermit=permit;
            if(permit.getAsBoolean()) {idleTrue++;if(failIdle)throw new java.io.IOException("synthetic");}
            else idleFalse++;
            Runnable callback=duringIdle;duringIdle=null;if(callback!=null)callback.run();
        }
    }
    private static final class H {
        final AtomicLong clock=new AtomicLong(1_000_000_000L);final Effect effect=new Effect();
        final List<JsonObject> output=new ArrayList<>();final SessionEngine engine;
        H(){this(testedVersion);}
        H(int version){engine=new SessionEngine(effect,clock::get,output::add,version);observe(1,1);engine.transportOpened();engine.offer(base("welcome"));engine.tick();}
        void observe(int seq,int generation){JsonObject p=new JsonObject(),self=new JsonObject();p.addProperty("seq",seq);self.addProperty("connection_id","connection");self.addProperty("generation",generation);p.add("self",self);engine.observeControl(p);}
        JsonObject base(String type){JsonObject h=engine.hello(),f=new JsonObject();f.addProperty("v",h.get("v").getAsInt());f.addProperty("type",type);f.add("session",h.get("session"));f.add("world",h.get("world"));return f;}
        JsonObject command(int seq){JsonObject c=base("command");c.addProperty("seq",seq);c.addProperty("request_id","r"+seq);c.addProperty("action","jump");c.addProperty("observation_seq",1);c.addProperty("max_age_ms",2000);c.addProperty("run_ms",1000);return c;}
        JsonObject request(String type,int seq){JsonObject c=base(type);c.addProperty("seq",seq);c.addProperty("request_id",type+seq);return c;}
        void start(){engine.offer(command(1));engine.tick();}
        void complete(){effect.result="succeeded";engine.tick();}
        JsonObject result(){return engine.hello().getAsJsonArray("receipts").get(0).getAsJsonObject();}
    }
    public static void main(String[] args) throws Exception {
        for(int version:new int[]{4,5}){testedVersion=version;checkVersion();}
        System.out.println("AdvancedSessionTest "+checks+" checks PASS");
    }
    private static void checkVersion() throws Exception {
        DirectClient.class.getMethod("advancedControl",java.net.URI.class,String.class,DirectClient.ControlExecutor.class);
        H h=new H();check(h.engine.hello().get("v").getAsInt()==testedVersion,"explicit version");
        String caps=testedVersion==4?"[\"attack\",\"jump\",\"look\",\"navigate\"]":"[\"attack\",\"inventory_page\",\"inventory_transfer\",\"jump\",\"look\",\"navigate\",\"swap_hands\"]";
        check(h.engine.hello().getAsJsonArray("capabilities").toString().equals(caps),"caps sorted and exact");
        check(h.effect.idleFalse==1 && !h.effect.idlePermit.getAsBoolean(),"no authority before an action");
        h.start();check(h.effect.starts==1 && h.effect.activePermit.getAsBoolean(),"running action lease");
        int idle=h.effect.idleTrue+h.effect.idleFalse;h.engine.tick();check(idle==h.effect.idleTrue+h.effect.idleFalse,"no idle while active");
        h.clock.addAndGet(100_000_000);h.complete();check(h.effect.idleTrue==1 && h.effect.idlePermit.getAsBoolean(),"success retains only original deadline");
        check(!h.effect.activePermit.getAsBoolean(),"completed command start permit no longer active");
        check(h.result().get("evidence").getAsString().equals("client_simulated"),"movement result provenance");
        h.clock.addAndGet(700_000_000);h.observe(2,1);h.engine.tick();check(h.effect.idlePermit.getAsBoolean(),"fresh observations do not reset original TTL");
        h.clock.addAndGet(200_000_000);check(!h.effect.idlePermit.getAsBoolean(),"lease expires without another tick");
        int cleanup=h.effect.idleFalse;h.engine.tick();check(h.effect.idleFalse>cleanup,"expired maintenance receives false cleanup");

        H cancelled=new H();cancelled.start();cancelled.complete();BooleanSupplier permit=cancelled.effect.idlePermit;
        cancelled.engine.offer(cancelled.request("cancel",1));check(!permit.getAsBoolean(),"terminal cancel fences maintenance immediately");
        cancelled.engine.tick();check(!cancelled.effect.idlePermit.getAsBoolean() && cancelled.effect.fences==1,"cancel remains revoked after fence dequeued");
        check(cancelled.result().get("status").getAsString().equals("succeeded"),"cancel cannot rewrite completed action outcome");

        H replaced=new H();replaced.start();replaced.complete();BooleanSupplier previous=replaced.effect.idlePermit;
        replaced.engine.offer(replaced.command(1));replaced.engine.offer(replaced.request("query",1));replaced.engine.tick();
        check(previous.getAsBoolean() && replaced.effect.starts==1,"duplicate/query preserve owned maintenance");
        replaced.engine.offer(replaced.command(4));replaced.engine.tick();check(previous.getAsBoolean(),"rejected sequence gap does not revoke owner");
        replaced.effect.result=null;replaced.engine.offer(replaced.command(2));replaced.engine.tick();
        check(!previous.getAsBoolean() && replaced.effect.starts==2 && replaced.effect.activePermit.getAsBoolean(),"accepted new command replaces old owner");

        H failed=new H();failed.start();failed.effect.result="failed";failed.engine.tick();
        check(failed.effect.idleTrue==0 && !failed.effect.idlePermit.getAsBoolean(),"failed result never grants maintenance");
        H world=new H();world.start();world.complete();BooleanSupplier worldPermit=world.effect.idlePermit;world.observe(2,2);
        check(!worldPermit.getAsBoolean(),"world change immediate invalidation");world.engine.tick();check(!world.effect.idlePermit.getAsBoolean(),"world cleanup");
        H lost=new H();lost.start();lost.complete();BooleanSupplier lostPermit=lost.effect.idlePermit;lost.engine.transportLost();
        check(!lostPermit.getAsBoolean(),"transport loss immediate invalidation");lost.engine.transportOpened();lost.engine.tick();check(!lost.effect.idlePermit.getAsBoolean(),"no reconnect replay");
        H off=new H();off.start();off.complete();JsonObject offFrame=off.base("off");offFrame.addProperty("request_id","off");off.engine.offer(offFrame);
        check(off.effect.offs==1 && !off.effect.idlePermit.getAsBoolean(),"OFF revokes synchronously");off.engine.tick();check(!off.engine.offer(off.command(2)),"OFF is final");

        H boundary=new H();boundary.start();boundary.effect.duringIdle=()->boundary.clock.addAndGet(1_000_000_000L);boundary.complete();
        check(boundary.effect.idleTrue==1 && boundary.effect.idleFalse==2,"deadline crossed inside idle cleaned in same tick");
        H callback=new H();callback.start();callback.effect.duringIdle=callback.engine::transportLost;callback.complete();
        check(callback.effect.idleTrue==1 && callback.effect.idleFalse==2,"disconnect inside idle cleaned in same tick");
        H cancellation=new H();cancellation.start();cancellation.effect.duringIdle=()->cancellation.engine.offer(cancellation.request("cancel",1));cancellation.complete();
        check(cancellation.effect.idleTrue==1 && cancellation.effect.idleFalse==2,"cancel inside idle cleaned in same tick");
        H generation=new H();generation.start();generation.effect.duringIdle=()->generation.observe(2,2);generation.complete();
        check(generation.effect.idleTrue==1 && generation.effect.idleFalse==2,"generation change inside idle cleaned in same tick");
        H locking=new H();locking.start();locking.complete();
        var lockField=SessionEngine.class.getDeclaredField("inboxLock");lockField.setAccessible(true);
        var done=new java.util.concurrent.CountDownLatch(1);var permitted=new java.util.concurrent.atomic.AtomicBoolean();
        Thread probe=new Thread(()->{permitted.set(locking.effect.idlePermit.getAsBoolean());done.countDown();});
        boolean nonblocking;
        synchronized(lockField.get(locking.engine)) {probe.start();nonblocking=done.await(300,java.util.concurrent.TimeUnit.MILLISECONDS);}
        probe.join(1000);
        check(nonblocking && permitted.get(),"permit must not take inbox lock while game callback owns game lock");
        H thrown=new H();thrown.start();thrown.effect.failIdle=true;thrown.complete();
        check(thrown.effect.offs==1 && !thrown.effect.idlePermit.getAsBoolean(),"idle effect exception closes host and revokes lease");

        H combat=new H();combat.start();combat.effect.code="target_defeated_observed";combat.complete();
        check(combat.result().get("evidence").getAsString().equals("server_observed"),"v4 observed defeat provenance");
        combat.engine.offer(combat.request("query",1));combat.engine.tick();
        check(combat.output.stream().filter(x->x.has("request_id")&&!x.get("request_id").isJsonNull()&&x.get("request_id").getAsString().equals("query1")).reduce((a,b)->b).orElseThrow().get("evidence").getAsString().equals("server_observed"),"cached defeat provenance");
        H old=new H(3);JsonObject look=old.command(1);look.addProperty("action","look");look.addProperty("yaw",0);look.addProperty("pitch",0);old.engine.offer(look);old.engine.tick();old.effect.code="target_defeated_observed";old.complete();
        check(old.effect.idleTrue==0 && old.effect.idleFalse==0 && old.result().get("evidence").getAsString().equals("client_simulated"),"v3 does not gain idle or observed receipts");
        if(testedVersion==5){
            H readOnly=new H();readOnly.start();readOnly.complete();BooleanSupplier oldPermit=readOnly.effect.idlePermit;
            JsonObject page=readOnly.command(2);page.addProperty("action","inventory_page");page.addProperty("page_start",0);page.addProperty("inventory_revision",1);
            readOnly.engine.offer(page);readOnly.engine.tick();
            check(!oldPermit.getAsBoolean()&&!readOnly.effect.idlePermit.getAsBoolean(),"inventory page revokes prior lease without granting movement maintenance");
            H onlyRead=new H();JsonObject first=onlyRead.command(1);first.addProperty("action","inventory_page");first.addProperty("page_start",0);first.addProperty("inventory_revision",1);
            onlyRead.effect.result="succeeded";onlyRead.engine.offer(first);onlyRead.engine.tick();
            check(onlyRead.effect.idleTrue==0&&!onlyRead.effect.idlePermit.getAsBoolean(),"inventory page alone grants no movement authority");
        }
    }
}
