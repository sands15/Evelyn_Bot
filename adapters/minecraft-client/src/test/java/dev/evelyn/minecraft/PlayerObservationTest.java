package dev.evelyn.minecraft;

import java.lang.reflect.*;
import java.util.UUID;
import java.util.concurrent.*;

/** Synthetic state-model checks only: no account, network, or game execution. */
public final class PlayerObservationTest {
    private static Class<?> type;
    private static int checks;
    private static final UUID CONNECTION = new UUID(0, 23);
    public static void main(String[] args) throws Exception {
        try { type = Class.forName("dev.evelyn.minecraft.PlayerObservation"); }
        catch (ClassNotFoundException e) { throw new AssertionError("player observation model is not implemented", e); }
        Object model = model(-100);
        Object initial = snapshot(model);
        equal(CONNECTION, value(initial,"connectionId")); equal(1,value(initial,"generation"));
        equal(1,value(initial,"sequence")); equal(0,value(initial,"eventSequence"));
        equal(-100L,value(initial,"observedAtNanos")); equal(false,value(initial,"available"));
        check(value(initial,"dimension")==null && value(initial,"position")==null && value(initial,"health")==null,"initial unknown");
        reject(()->position(model,1,-99,1,2,3,0,0)); reject(()->health(model,1,-99,20,20,5));
        player(model,1,-99,"minecraft:overworld");
        Object bound=snapshot(model); equal(true,value(bound,"available")); equal(2,value(bound,"sequence"));
        check(value(bound,"position")==null && value(bound,"health")==null,"binding is not full readiness");
        position(model,2,-98,1,64,3,90,20); health(model,3,-97,20,20,5);
        Object complete=snapshot(model); equal(4,value(complete,"sequence")); equal(3,value(complete,"eventSequence"));
        equal(64d,value(value(complete,"position"),"y")); equal(20f,value(value(complete,"health"),"health"));
        check(value(initial,"position")==null && !(boolean)value(initial,"available"),"returned record stays immutable");
        reject(()->player(model,4,-96,"minecraft:the_nether")); equal(complete,snapshot(model));
        reject(()->position(model,3,-96,0,0,0,0,0)); reject(()->position(model,4,-98,0,0,0,0,0));
        reject(()->position(model,4,-96,Double.NaN,0,0,0,0));
        reject(()->position(model,4,-96,0,Double.POSITIVE_INFINITY,0,0,0));
        reject(()->position(model,4,-96,0,0,0,Float.NaN,0));
        reject(()->position(model,4,-96,0,0,0,0,91));
        reject(()->health(model,4,-96,-1,20,5)); reject(()->health(model,4,-96,20,-1,5));
        reject(()->health(model,4,-96,20,20,Float.NaN)); equal(complete,snapshot(model));
        bundleAtomicity(model);
        invalidationAndThreadBoundary();
        budgetsAndOverflow();
        signedClockAndValueBounds();
        terrainBoundary();
        System.out.println("PLAYER_OBSERVATION_PASS " + checks + " checks (synthetic, no server)");
    }
    private static void bundleAtomicity(Object model) throws Exception {
        Object before=snapshot(model); call(model,"beginBundle",new Class[0]);
        position(model,4,-96,2,64,3,90,0); health(model,5,-95,18,19,4);
        equal(before,snapshot(model)); reject(()->call(model,"beginBundle",new Class[0]));
        reject(()->health(model,6,-96,10,10,1)); equal(before,snapshot(model));
        call(model,"endBundle",new Class[0]);Object after=snapshot(model);
        equal(5,value(after,"sequence")); equal(5,value(after,"eventSequence")); equal(-95L,value(after,"observedAtNanos"));
        equal(2d,value(value(after,"position"),"x")); equal(18f,value(value(after,"health"),"health"));
        reject(()->call(model,"endBundle",new Class[0]));
        call(model,"beginBundle",new Class[0]);call(model,"endBundle",new Class[0]);equal(after,snapshot(model));
    }
    private static void invalidationAndThreadBoundary() throws Exception {
        Object model=model(10);player(model,1,11,"minecraft:overworld");position(model,2,12,1,2,3,0,0);
        call(model,"beginBundle",new Class[0]);health(model,3,13,10,10,2);
        ExecutorService worker=Executors.newSingleThreadExecutor();
        try { worker.submit(()->{try{invalidate(model,4,14);}catch(Exception e){throw new CompletionException(e);}}).get(2,TimeUnit.SECONDS); }
        finally { worker.shutdownNow(); }
        Object cleared=snapshot(model);
        equal(2,value(cleared,"generation"));equal(4,value(cleared,"sequence"));equal(4,value(cleared,"eventSequence"));
        equal(false,value(cleared,"available"));check(value(cleared,"dimension")==null && value(cleared,"position")==null && value(cleared,"health")==null,"invalidate discards all staged observations");
        reject(()->call(model,"endBundle",new Class[0]));reject(()->position(model,5,15,9,9,9,0,0));reject(()->health(model,5,15,20,20,5));
        equal(cleared,snapshot(model));player(model,5,15,"minecraft:the_nether");
        equal(2,value(snapshot(model),"generation"));check(value(snapshot(model),"position")==null,"new player does not inherit position");
        Object current=snapshot(model);reject(()->invalidate(model,6,14));equal(current,snapshot(model));
    }
    private static void budgetsAndOverflow() throws Exception {
        Object model=model(0);player(model,1,1,"minecraft:overworld");Object baseline=snapshot(model);
        call(model,"beginBundle",new Class[0]);
        for(int i=2;i<=4097;i++)health(model,i,i,20,20,5);
        reject(()->health(model,4098,4098,19,19,4));equal(baseline,snapshot(model));
        call(model,"endBundle",new Class[0]);equal(3,value(snapshot(model),"sequence"));equal(4097,value(snapshot(model),"eventSequence"));
        Object maxEvent=model(0);player(maxEvent,Integer.MAX_VALUE,1,"minecraft:overworld");Object last=snapshot(maxEvent);
        reject(()->health(maxEvent,Integer.MIN_VALUE,2,20,20,5));reject(()->invalidate(maxEvent,Integer.MAX_VALUE,2));equal(last,snapshot(maxEvent));
        Object seq=model(0);injectPublished(seq,1,Integer.MAX_VALUE);
        last=snapshot(seq);reject(()->player(seq,1,1,"minecraft:overworld"));equal(last,snapshot(seq));
        Object gen=model(0);injectPublished(gen,Integer.MAX_VALUE,1);
        last=snapshot(gen);reject(()->invalidate(gen,1,1));equal(last,snapshot(gen));
    }
    private static void signedClockAndValueBounds() throws Exception {
        Object wrap=model(Long.MAX_VALUE-3);player(wrap,1,Long.MAX_VALUE-1,"minecraft:overworld");
        position(wrap,2,Long.MIN_VALUE+1,0,0,0,0,-90);equal(Long.MIN_VALUE+1,value(snapshot(wrap),"observedAtNanos"));
        health(wrap,3,Long.MIN_VALUE+1,100,Integer.MAX_VALUE,50);equal(100f,value(value(snapshot(wrap),"health"),"health"));
        Object model=model(0);reject(()->player(model,1,1,"minecraft:bad space"));reject(()->player(model,1,1,"minecraft:"+"x".repeat(119)));
        reject(()->player(model,0,1,"minecraft:overworld"));reject(()->player(model,1,-1,"minecraft:overworld"));
        player(model,1,0,"minecraft:overworld");equal(0L,value(snapshot(model),"observedAtNanos"));
    }
    private static void terrainBoundary() throws Exception {
        ChunkWorld.Snapshot world = new ChunkWorld.Snapshot(CONNECTION,1,2,1,1,true,"minecraft:overworld",0,16,1,16384,false);
        var zero = new ChunkWorld.Block(new ChunkWorld.BlockPos(0,0,0),0);
        composite(world,java.util.List.of(zero)); checks++;
        reject(()->composite(world,java.util.List.of(zero,zero)));
        reject(()->composite(world,java.util.Collections.nCopies(65,zero)));
        reject(()->composite(world,java.util.List.of(new ChunkWorld.Block(new ChunkWorld.BlockPos(0,0,0),-1))));
        reject(()->composite(world,java.util.List.of(new ChunkWorld.Block(new ChunkWorld.BlockPos(0,16,0),0))));
        reject(()->composite(world,java.util.List.of(new ChunkWorld.Block(new ChunkWorld.BlockPos(33554432,0,0),null))));
        reject(()->composite(new ChunkWorld.Snapshot(CONNECTION,1,2,1,1,true,"minecraft:overworld",0,16,0,0,false),java.util.List.of(zero)));
        reject(()->composite(new ChunkWorld.Snapshot(CONNECTION,1,2,1,1,true,"minecraft:overworld",1,16,1,16384,false),java.util.List.of(zero)));
    }
    private static PlayerObservation.Snapshot composite(ChunkWorld.Snapshot world,java.util.List<ChunkWorld.Block> blocks) {
        return new PlayerObservation.Snapshot(CONNECTION,1,3,2,2,true,"minecraft:overworld",null,null,new ChunkWorld.Sample(world,blocks));
    }
    private static void injectPublished(Object model,int generation,int sequence)throws Exception{
        Class<?> state=Class.forName("dev.evelyn.minecraft.PlayerObservation$Snapshot");
        Class<?> pos=Class.forName("dev.evelyn.minecraft.PlayerObservation$Position"),health=Class.forName("dev.evelyn.minecraft.PlayerObservation$Health");
        Object value=state.getConstructor(UUID.class,int.class,int.class,int.class,long.class,boolean.class,String.class,pos,health).newInstance(CONNECTION,generation,sequence,0,0L,false,null,null,null);
        Field f=type.getDeclaredField("published");f.setAccessible(true);f.set(model,value);
    }
    private static Object model(long now)throws Exception{return type.getConstructor(UUID.class,long.class).newInstance(CONNECTION,now);}
    private static void player(Object m,int e,long t,String d)throws Exception{call(m,"player",new Class[]{int.class,long.class,String.class},e,t,d);}
    private static void position(Object m,int e,long t,double x,double y,double z,float yaw,float pitch)throws Exception{call(m,"position",new Class[]{int.class,long.class,double.class,double.class,double.class,float.class,float.class},e,t,x,y,z,yaw,pitch);}
    private static void health(Object m,int e,long t,float h,int f,float s)throws Exception{call(m,"health",new Class[]{int.class,long.class,float.class,int.class,float.class},e,t,h,f,s);}
    private static void invalidate(Object m,int e,long t)throws Exception{call(m,"invalidate",new Class[]{int.class,long.class},e,t);}
    private static Object snapshot(Object m)throws Exception{return call(m,"snapshot",new Class[0]);}
    private static Object value(Object r,String field)throws Exception{return r.getClass().getMethod(field).invoke(r);}
    private static Object call(Object target,String name,Class<?>[] parameters,Object...args)throws Exception{
        try{return type.getMethod(name,parameters).invoke(target,args);}catch(InvocationTargetException e){if(e.getCause() instanceof Exception x)throw x;throw (Error)e.getCause();}
    }
    private static void equal(Object expected,Object actual){check(expected.equals(actual),"snapshot contract mismatch");}
    private static void check(boolean value,String label){if(!value)throw new AssertionError(label);checks++;}
    private static void reject(Checked action)throws Exception{try{action.run();}catch(IllegalArgumentException|IllegalStateException expected){checks++;return;}throw new AssertionError("invalid observation accepted");}
    @FunctionalInterface private interface Checked{void run()throws Exception;}
}
