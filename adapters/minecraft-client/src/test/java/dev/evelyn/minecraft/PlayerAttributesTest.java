package dev.evelyn.minecraft;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

public final class PlayerAttributesTest {
    static int checks;
    // Independent literal: entity7, movement_speed26 base0.1, test:boost +0.5 multiplied-base.
    static final byte[] NORMAL=HexFormat.of().parseHex("07011a3fb999999999999a010a746573743a626f6f73743fe000000000000001");
    public static void main(String[] args)throws Exception {normal();lifetime();invalid();bounds();packetHooks();System.out.println("PLAYER_ATTRIBUTES_PASS "+checks);}
    static PlayerAttributes bound()throws Exception {PlayerAttributes state=new PlayerAttributes();state.bind(7);return state;}
    static boolean apply(PlayerAttributes state,byte[] body)throws Exception {return state.accept(state.snapshot().generation(),new VanillaConnection.Cursor(body));}
    static PlayerAttributes.Entry entry(PlayerAttributes state,int id){return state.snapshot().entry(id).orElseThrow(()->new AssertionError("expected known attribute "+id));}
    static PlayerAttributes.Modifier modifier(String id,double amount,int operation){return new PlayerAttributes.Modifier(id,amount,operation);}
    static PlayerAttributes.Entry value(double base,PlayerAttributes.Modifier...mods){return new PlayerAttributes.Entry(base,List.of(mods));}
    static void normal()throws Exception {
        PlayerAttributes state=new PlayerAttributes();eq(state.snapshot().entityId(),null);check(!state.snapshot().received(),"identity is not received attributes");eq(state.snapshot().entries(),Map.of());state.bind(7);
        check(state.snapshot().entry(26).isEmpty(),"binding self does not manufacture default attributes");check(!state.snapshot().received(),"binding self is not packet evidence");check(apply(state,NORMAL),"self packet accepted");
        var snapshot=state.snapshot();var speed=entry(state,26);eq(speed.base(),.1);eq(speed.origin(),"SERVER_OBSERVED");eq(speed.modifiers(),List.of(modifier("test:boost",.5,1)));check(snapshot.received(),"received means actual self packet");check(snapshot.entry(7).isEmpty(),"missing attribute remains unknown");
        rejectMutation(()->snapshot.entries().clear());rejectMutation(()->speed.modifiers().clear());
        apply(state,packet(7,Map.of(7,value(3))));eq(entry(state,26),speed);eq(entry(state,7).base(),3d);long revision=state.snapshot().revision();
        check(!apply(state,packet(8,Map.of(26,value(99)))),"wrong entity packet ignored after complete validation");eq(entry(state,26),speed);eq(state.snapshot().revision(),revision);
        apply(state,packet(7,Map.of(26,value(.2))));check(entry(state,26).modifiers().isEmpty(),"included entry replaces all modifiers");eq(entry(state,7).base(),3d);eq(snapshot.entry(26).orElseThrow(),speed);
        apply(state,packet(7,Map.of(26,value(-100,modifier("test:a",2,0),modifier("test:b",.5,1),modifier("test:c",-.25,2)))));eq(entry(state,26).base(),-100d);eq(entry(state,26).modifiers().stream().map(PlayerAttributes.Modifier::operation).toList(),List.of(0,1,2));
        apply(state,packet(7,Map.of(26,value(-0d,modifier("test:zero",-0d,0)))));eq(Double.doubleToRawLongBits(entry(state,26).base()),Long.MIN_VALUE);eq(Double.doubleToRawLongBits(entry(state,26).modifiers().get(0).amount()),Long.MIN_VALUE);
        apply(state,packet(7,Map.of(26,value(Double.MAX_VALUE,modifier("test:tiny",Double.MIN_VALUE,2)))));eq(entry(state,26).base(),Double.MAX_VALUE);eq(entry(state,26).modifiers().get(0).amount(),Double.MIN_VALUE);
        byte[] source=NORMAL.clone();apply(state,source);Arrays.fill(source,(byte)0);eq(entry(state,26).base(),.1);
        apply(state,packet(7,Map.of()));eq(entry(state,26).base(),.1);check(state.snapshot().entry(0).isEmpty(),"empty packet does not promote static default");
        PlayerAttributes empty=bound();apply(empty,new byte[]{7,0});check(empty.snapshot().received(),"empty self packet is real receipt evidence");check(empty.snapshot().entry(26).isEmpty(),"empty self receipt still has unknown attribute values");
    }
    static void lifetime()throws Exception {
        PlayerAttributes state=bound();apply(state,NORMAL);var old=state.snapshot();state.clear();check(state.snapshot().generation()>old.generation(),"clear changes generation");check(state.snapshot().revision()>old.revision(),"clear changes revision");eq(state.snapshot().entityId(),null);eq(state.snapshot().entries(),Map.of());check(!state.snapshot().received(),"clear resets receipt");eq(old.entry(26).orElseThrow().base(),.1);
        state.bind(7);apply(state,packet(7,Map.of(7,value(4))));reject(()->state.accept(old.generation(),new VanillaConnection.Cursor(NORMAL)),"attribute_generation");eq(entry(state,7).base(),4d);
        state.bind(7);check(state.snapshot().entries().isEmpty(),"rebind same ID never exposes prior generation");check(!state.snapshot().received(),"rebind clears actual receive flag");
        reject(()->new PlayerAttributes().accept(0,new VanillaConnection.Cursor(NORMAL)),"attribute_identity");reject(()->state.bind(-1),"attribute_identity");eq(state.snapshot().entityId(),null);
        PlayerAttributes maximum=new PlayerAttributes();maximum.bind(Integer.MAX_VALUE);check(apply(maximum,packet(Integer.MAX_VALUE,Map.of(39,value(0)))),"positive maximum entity id allowed");eq(entry(maximum,39).base(),0d);
    }
    static void invalid()throws Exception {
        for(int length=0;length<NORMAL.length;length++){PlayerAttributes state=bound();apply(state,NORMAL);byte[] bad=Arrays.copyOf(NORMAL,length);reject(()->apply(state,bad),null);check(state.snapshot().entries().isEmpty(),"truncated packet invalidates atomically");eq(state.snapshot().entityId(),null);}
        for(double bad:new double[]{Double.NaN,Double.POSITIVE_INFINITY,Double.NEGATIVE_INFINITY}){
            failClosed(packet(7,Map.of(26,value(bad))));failClosed(packet(7,Map.of(26,value(.1,modifier("test:x",bad,0)))));
        }
        for(int op:new int[]{-1,3,128})failClosed(packet(7,Map.of(26,value(.1,modifier("test:x",1,op)))));
        failClosed(packet(7,Map.of(40,value(1))));failClosed(packet(7,Map.of(-1,value(1))));failClosed(packet(-1,Map.of()));
        failClosed(packet(7,Map.of(26,value(.1,modifier("test:x",1,0),modifier("test:x",2,2)))));
        failClosed(packet(7,Map.of(26,value(.1,modifier("BAD:x",1,0)))));
        byte[] trailing=Arrays.copyOf(NORMAL,NORMAL.length+1);failClosed(trailing);
        ByteArrayOutputStream bytes=new ByteArrayOutputStream();DataOutputStream out=new DataOutputStream(bytes);vi(out,7);vi(out,2);attribute(out,26,value(.1));attribute(out,26,value(.2));failClosed(bytes.toByteArray());
        // Wrong-entity malformed data is still a protocol error and cannot leave a usable self snapshot.
        failClosed(packet(8,Map.of(26,value(.1,modifier("test:x",Double.NaN,0)))));
    }
    static void bounds()throws Exception {
        PlayerAttributes state=bound();var all=new LinkedHashMap<Integer,PlayerAttributes.Entry>();for(int a=0;a<40;a++){var mods=new ArrayList<PlayerAttributes.Modifier>();for(int m=0;m<128;m++)mods.add(modifier("test:m_"+m,0,m%3));all.put(a,new PlayerAttributes.Entry(0,mods));}
        apply(state,packet(7,all));eq(state.snapshot().entries().size(),40);check(state.snapshot().retainedNodes()>=5120,"every modifier contributes retained node charge");check(state.snapshot().retainedNodes()<=65536,"retained node bound");
        int independentlyCounted=2;for(var observed:state.snapshot().entries().entrySet()){independentlyCounted+=4;for(var mod:observed.getValue().modifiers())independentlyCounted+=4;}check(state.snapshot().retainedNodes()>=independentlyCounted,"retained accounting covers independently walked typed records and primitive fields");
        byte[] overCount=packet(7,Map.of(26,new PlayerAttributes.Entry(1,Collections.nCopies(129,modifier("test:x",0,0)))));var cursor=new VanillaConnection.Cursor(overCount);reject(()->state.accept(state.snapshot().generation(),cursor),"count_budget");check(cursor.bytes.available()>129*10,"modifier count rejected before reading/allocating list contents");
        ByteArrayOutputStream badCount=new ByteArrayOutputStream();DataOutputStream header=new DataOutputStream(badCount);vi(header,7);vi(header,41);var c=new VanillaConnection.Cursor(badCount.toByteArray());reject(()->bound().accept(1,c),"count_budget");eq(c.bytes.available(),0);
        byte[] tooLarge=new byte[1048577];var full=new VanillaConnection.Cursor(tooLarge);reject(()->bound().accept(1,full),"attribute_packet_bytes");eq(full.bytes.available(),tooLarge.length);
        var longMods=List.of(modifier("test:a"+"x".repeat(30000),0,0),modifier("test:b"+"x".repeat(30000),0,1));PlayerAttributes retained=bound();for(int i=0;i<17;i++)apply(retained,packet(7,Map.of(i,new PlayerAttributes.Entry(1,longMods))));check(retained.snapshot().retainedBytes()>1000000,"retained budget accumulates across partial packets");reject(()->apply(retained,packet(7,Map.of(17,new PlayerAttributes.Entry(1,longMods)))),"attribute_bytes");check(retained.snapshot().entries().isEmpty(),"aggregate failure invalidates entire generation");
        PlayerAttributes release=bound();apply(release,packet(7,Map.of(26,new PlayerAttributes.Entry(1,longMods))));long before=release.snapshot().retainedBytes();apply(release,NORMAL);check(release.snapshot().retainedBytes()<before,"replacement releases retained modifier/name charge");
        var field=PlayerAttributes.class.getDeclaredField("revision");field.setAccessible(true);PlayerAttributes limit=bound();apply(limit,NORMAL);field.setLong(limit,Protocol.MAX_SEQUENCE);reject(()->apply(limit,NORMAL),"attribute_lifetime");check(limit.snapshot().exhausted(),"revision exhaustion fails closed");check(limit.snapshot().entries().isEmpty(),"exhaustion removes old values");
    }
    static void packetHooks()throws Exception {
        PlayerAttributes state=bound();apply(state,NORMAL);var before=state.snapshot();int[] calls={0};PlayerAttributes.DecodedPacket[] decoded={null};
        check(state.accept(before.generation(),new VanillaConnection.Cursor(NORMAL),packet->{calls[0]++;decoded[0]=packet;eq(state.snapshot(),before);}),"validated self packet accepted");
        eq(calls[0],1);check(decoded[0]!=null,"packet callback invoked exactly once");eq(decoded[0].entityId(),7);eq(decoded[0].entries().keySet(),Set.of(26));eq(decoded[0].entries().get(26),before.entry(26).orElseThrow());check(state.snapshot().revision()>before.revision(),"same-value retransmission remains packet-local inclusion");
        rejectMutation(()->decoded[0].entries().clear());rejectMutation(()->decoded[0].entries().get(26).modifiers().clear());
        state.accept(state.snapshot().generation(),new VanillaConnection.Cursor(packet(7,Map.of(7,value(5)))),packet->{calls[0]++;decoded[0]=packet;});eq(decoded[0].entries().keySet(),Set.of(7));check(state.snapshot().entry(26).isPresent(),"packet entries are separate from cumulative self state");
        var beforeForeign=state.snapshot();check(!state.accept(beforeForeign.generation(),new VanillaConnection.Cursor(packet(8,Map.of(7,value(99)))),packet->{calls[0]++;decoded[0]=packet;eq(state.snapshot(),beforeForeign);}),"foreign callback has no self publish");eq(decoded[0].entityId(),8);eq(decoded[0].entries().get(7).base(),99d);eq(state.snapshot(),beforeForeign);eq(calls[0],3);
        state.accept(state.snapshot().generation(),new VanillaConnection.Cursor(packet(7,Map.of(26,value(.2)))),packet->{calls[0]++;check(packet.entries().get(26).modifiers().isEmpty(),"callback sees whole empty replacement");check(!state.snapshot().entry(26).orElseThrow().modifiers().isEmpty(),"callback is before publish");});check(entry(state,26).modifiers().isEmpty(),"validated empty modifier list fully replaces old list");eq(calls[0],4);
        state.accept(state.snapshot().generation(),new VanillaConnection.Cursor(new byte[]{7,0}),packet->{calls[0]++;check(packet.entries().isEmpty(),"empty packet map is not cumulative snapshot");});check(state.snapshot().entry(26).isPresent(),"empty packet retains observed attributes");eq(calls[0],5);
        reject(()->state.accept(state.snapshot().generation(),new VanillaConnection.Cursor(NORMAL),packet->{calls[0]++;throw new IOException("validator_rejected");}),"validator_rejected");eq(calls[0],6);check(state.snapshot().entries().isEmpty(),"validator rejection atomically invalidates");eq(state.snapshot().entityId(),null);
        PlayerAttributes foreignReject=bound();apply(foreignReject,NORMAL);reject(()->foreignReject.accept(foreignReject.snapshot().generation(),new VanillaConnection.Cursor(packet(8,Map.of())),packet->{throw new IOException("foreign_rejected");}),"foreign_rejected");check(foreignReject.snapshot().entries().isEmpty(),"foreign rejection also invalidates current generation");
        PlayerAttributes malformed=bound();int[] malformedCalls={0};byte[] trailing=Arrays.copyOf(NORMAL,NORMAL.length+1);reject(()->malformed.accept(malformed.snapshot().generation(),new VanillaConnection.Cursor(trailing),packet->{malformedCalls[0]++;}),null);eq(malformedCalls[0],0);
        reentrantClear();reentrantBind();reentrantAccept();
    }
    static void reentrantClear()throws Exception {
        PlayerAttributes state=bound();apply(state,NORMAL);long generation=state.snapshot().generation();long[] callbackGeneration={-1};
        reject(()->state.accept(generation,new VanillaConnection.Cursor(NORMAL),packet->{state.clear();callbackGeneration[0]=state.snapshot().generation();}),"attribute_state_changed");eq(state.snapshot().generation(),callbackGeneration[0]);eq(state.snapshot().entityId(),null);check(state.snapshot().entries().isEmpty(),"reentrant clear cannot resurrect old parsed values");
    }
    static void reentrantBind()throws Exception {
        for(boolean rejectCallback:new boolean[]{false,true}){PlayerAttributes state=bound();apply(state,NORMAL);long generation=state.snapshot().generation();PlayerAttributes.Snapshot[] successor={null};
            reject(()->state.accept(generation,new VanillaConnection.Cursor(NORMAL),packet->{state.bind(9);successor[0]=state.snapshot();if(rejectCallback)throw new IOException("after_bind_rejected");}),rejectCallback?"after_bind_rejected":"attribute_state_changed");eq(state.snapshot(),successor[0]);eq(state.snapshot().entityId(),9);check(state.snapshot().entries().isEmpty(),"reentrant new identity stays unknown");}
    }
    static void reentrantAccept()throws Exception {
        for(boolean rejectCallback:new boolean[]{false,true}){PlayerAttributes state=bound();apply(state,NORMAL);long generation=state.snapshot().generation();PlayerAttributes.Snapshot[] successor={null};
            reject(()->state.accept(generation,new VanillaConnection.Cursor(packet(7,Map.of(26,value(123)))),packet->{state.accept(generation,new VanillaConnection.Cursor(packet(7,Map.of(7,value(8)))));successor[0]=state.snapshot();if(rejectCallback)throw new IOException("after_accept_rejected");}),rejectCallback?"after_accept_rejected":"attribute_state_changed");
            eq(state.snapshot(),successor[0]);eq(entry(state,26).base(),.1);eq(entry(state,7).base(),8d);eq(state.snapshot().generation(),generation);}
        PlayerAttributes runtime=bound();apply(runtime,NORMAL);reject(()->runtime.accept(runtime.snapshot().generation(),new VanillaConnection.Cursor(NORMAL),packet->{throw new IllegalArgumentException("synthetic rejection");}),"attribute_packet");check(runtime.snapshot().entries().isEmpty(),"unchecked validator rejection invalidates same state");
        PlayerAttributes successorRuntime=bound();apply(successorRuntime,NORMAL);PlayerAttributes.Snapshot[] next={null};reject(()->successorRuntime.accept(successorRuntime.snapshot().generation(),new VanillaConnection.Cursor(NORMAL),packet->{successorRuntime.bind(9);successorRuntime.accept(successorRuntime.snapshot().generation(),new VanillaConnection.Cursor(packet(9,Map.of(7,value(9)))));next[0]=successorRuntime.snapshot();throw new IllegalArgumentException("after successor");}),"attribute_packet");eq(successorRuntime.snapshot(),next[0]);eq(entry(successorRuntime,7).base(),9d);
    }
    static byte[] packet(int entity,Map<Integer,PlayerAttributes.Entry> values)throws IOException {ByteArrayOutputStream bytes=new ByteArrayOutputStream();DataOutputStream out=new DataOutputStream(bytes);vi(out,entity);vi(out,values.size());for(var value:values.entrySet())attribute(out,value.getKey(),value.getValue());return bytes.toByteArray();}
    static void attribute(DataOutputStream out,int id,PlayerAttributes.Entry entry)throws IOException {vi(out,id);out.writeDouble(entry.base());vi(out,entry.modifiers().size());for(var mod:entry.modifiers()){byte[] name=mod.id().getBytes(StandardCharsets.UTF_8);vi(out,name.length);out.write(name);out.writeDouble(mod.amount());vi(out,mod.operation());}}
    static void vi(DataOutputStream out,int n)throws IOException {do{int b=n&127;n>>>=7;out.writeByte(n==0?b:b|128);}while(n!=0);}
    static void failClosed(byte[] bad)throws Exception {PlayerAttributes state=bound();apply(state,NORMAL);reject(()->apply(state,bad),null);check(state.snapshot().entries().isEmpty(),"malformed candidate state cleared");check(!state.snapshot().received(),"malformed receive cannot preserve evidence");}
    interface Action {void run()throws Exception;}
    static void reject(Action action,String code)throws Exception {try{action.run();throw new AssertionError("expected rejection "+code);}catch(IOException error){if(code!=null)eq(error.getMessage(),code);checks++;}}
    static void rejectMutation(Action action)throws Exception {try{action.run();throw new AssertionError("mutable snapshot");}catch(UnsupportedOperationException expected){checks++;}}
    static void eq(Object actual,Object expected){check(Objects.equals(actual,expected),"expected="+expected+" actual="+actual);}
    static void check(boolean valid,String why){if(!valid)throw new AssertionError(why);checks++;}
}
