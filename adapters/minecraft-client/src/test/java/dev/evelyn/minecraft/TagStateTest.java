package dev.evelyn.minecraft;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

public final class TagStateTest {
    static int checks;
    public static void main(String[] args)throws Exception {normal();lifetime();invalid();bounds();System.out.println("TAG_STATE_CANDIDATE_PASS "+checks);}
    static final byte[] NORMAL=HexFormat.of().parseHex("0106746573743a720106746573743a61020001");
    static void apply(TagState state,byte[] bytes,Map<String,Integer> extents)throws Exception {state.accept(state.snapshot().generation(),new VanillaConnection.Cursor(bytes),extents);}
    static void normal()throws Exception {
        TagState state=new TagState();var initial=state.snapshot();eq(initial.presence("test:r","test:a"),TagState.Presence.UNOBSERVED);eq(initial.membership("test:r","test:a",0),TagState.Membership.UNKNOWN_CONTEXT);
        apply(state,NORMAL,Map.of());var unknown=state.snapshot();eq(unknown.presence("test:r","test:a"),TagState.Presence.NONEMPTY);eq(unknown.membership("test:r","test:a",0),TagState.Membership.UNKNOWN_CONTEXT);check(unknown.members("test:r","test:a").isEmpty(),"unknown context cannot expose usable membership");
        apply(state,new byte[]{0},Map.of("test:r",2));var known=state.snapshot();eq(known.membership("test:r","test:a",0),TagState.Membership.MEMBER);eq(known.membership("test:r","test:a",1),TagState.Membership.MEMBER);eq(known.membership("test:r","test:a",2),TagState.Membership.INVALID_ID);eq(known.membership("test:r","test:missing",0),TagState.Membership.MISSING_TAG);
        eq(known.presence("test:r","test:missing"),TagState.Presence.MISSING);check(known.members("test:r","test:missing").isEmpty(),"missing tag is not known empty");
        rejectMutation(()->known.members("test:r","test:a").orElseThrow().clear());
        Set<String> supplied=state.accept(state.snapshot().generation(),new VanillaConnection.Cursor(packet("test:s",Map.of("test:a",new int[0]))),Map.of("test:r",2,"test:s",2));eq(supplied,Set.of("test:s"));rejectMutation(()->supplied.clear());check(!supplied.contains("test:r"),"packet-local old flow guard can preserve its missing-in-packet result");eq(state.snapshot().membership("test:r","test:a",1),TagState.Membership.MEMBER);eq(state.snapshot().presence("test:s","test:a"),TagState.Presence.EMPTY);eq(state.snapshot().membership("test:s","test:a",0),TagState.Membership.NOT_MEMBER);
        apply(state,packet("test:r",Map.of("test:b",new int[]{1})),Map.of("test:r",2,"test:s",2));eq(state.snapshot().presence("test:r","test:a"),TagState.Presence.MISSING);eq(state.snapshot().membership("test:r","test:b",1),TagState.Membership.MEMBER);eq(state.snapshot().presence("test:s","test:a"),TagState.Presence.EMPTY);
        apply(state,packet("test:r",Map.of()),Map.of("test:r",2,"test:s",2));eq(state.snapshot().presence("test:r","test:b"),TagState.Presence.MISSING);
        TagState duplicates=new TagState();apply(duplicates,HexFormat.of().parseHex("0106746573743a720106746573743a6103000001"),Map.of("test:r",2));check(duplicates.snapshot().duplicateMembers("test:r","test:a"),"official repeated IDs remain observable for old flow guard");eq(duplicates.snapshot().members("test:r","test:a").orElseThrow(),Set.of(0,1));
        TagState emptyUnknown=new TagState();apply(emptyUnknown,packet("test:r",Map.of("test:a",new int[0])),Map.of());eq(emptyUnknown.snapshot().presence("test:r","test:a"),TagState.Presence.EMPTY);eq(emptyUnknown.snapshot().membership("test:r","test:a",0),TagState.Membership.UNKNOWN_CONTEXT);
        TagState actualIds=new TagState();apply(actualIds,packet("minecraft:block",Map.of("test:a",new int[]{1285})),Map.of("minecraft:block",1286));eq(actualIds.snapshot().membership("minecraft:block","test:a",1285),TagState.Membership.MEMBER);
        apply(actualIds,packet("minecraft:item",Map.of("test:a",new int[]{1657})),Map.of("minecraft:block",1286,"minecraft:item",1658));eq(actualIds.snapshot().membership("minecraft:item","test:a",1657),TagState.Membership.MEMBER);
    }
    static void lifetime()throws Exception {
        TagState state=new TagState();byte[] source=NORMAL.clone();Map<String,Integer> context=new HashMap<>(Map.of("test:r",2));apply(state,source,context);var before=state.snapshot();Arrays.fill(source,(byte)0);context.clear();eq(before.membership("test:r","test:a",1),TagState.Membership.MEMBER);
        state.clear();var cleared=state.snapshot();check(cleared.generation()>before.generation()&&cleared.revision()>before.revision(),"clear advances lifetime and revision");eq(cleared.presence("test:r","test:a"),TagState.Presence.UNOBSERVED);eq(before.membership("test:r","test:a",1),TagState.Membership.MEMBER);
        apply(state,packet("test:r",Map.of("test:b",new int[]{0})),Map.of("test:r",2));reject(()->state.accept(before.generation(),new VanillaConnection.Cursor(NORMAL),Map.of("test:r",2)),"tag_generation");eq(state.snapshot().membership("test:r","test:b",0),TagState.Membership.MEMBER);
        apply(state,new byte[]{0},Map.of());eq(state.snapshot().membership("test:r","test:b",0),TagState.Membership.UNKNOWN_CONTEXT);
    }
    static void invalid()throws Exception {
        for(int n=0;n<NORMAL.length;n++){TagState state=new TagState();apply(state,NORMAL,Map.of("test:r",2));byte[] truncated=Arrays.copyOf(NORMAL,n);reject(()->apply(state,truncated,Map.of("test:r",2)),null);eq(state.snapshot().presence("test:r","test:a"),TagState.Presence.UNOBSERVED);}
        for(String hex:new String[]{"0106746573743a720206746573743a61010006746573743a610101","0206746573743a720106746573743a61010006746573743a720106746573743a620101","0106746573743a720106746573743a61020200","0106746573743a720106746573743a6101ffffffff0f","0106746573743a720106746573743a6102000100"}){
            TagState state=new TagState();reject(()->apply(state,HexFormat.of().parseHex(hex),Map.of("test:r",2)),null);eq(state.snapshot().presence("test:r","test:a"),TagState.Presence.UNOBSERVED);
        }
        for(var entry:Map.of("minecraft:block",1286,"minecraft:item",1658).entrySet()){TagState state=new TagState();reject(()->apply(state,packet(entry.getKey(),Map.of("test:a",new int[]{entry.getValue()})),Map.of(entry.getKey(),entry.getValue())),"tag_member_id");}
        TagState later=new TagState();apply(later,packet("test:r",Map.of("test:a",new int[]{2})),Map.of());reject(()->apply(later,new byte[]{0},Map.of("test:r",2)),"tag_member_id");eq(later.snapshot().presence("test:r","test:a"),TagState.Presence.UNOBSERVED);
        Map<String,Integer> invalid=new HashMap<>();invalid.put("test:r",null);reject(()->apply(new TagState(),NORMAL,invalid),"tag_context");reject(()->apply(new TagState(),NORMAL,Map.of("BAD:r",2)),"tag_context");reject(()->apply(new TagState(),NORMAL,Map.of("test:r",-1)),"tag_context");
    }
    static void bounds()throws Exception {
        TagState state=new TagState();int[] large=new int[65000];apply(state,packet("test:r",Map.of("test:a",large)),Map.of("test:r",2));check(state.snapshot().retainedNodes()>=65000,"all repeated wire IDs charged conservatively");apply(state,NORMAL,Map.of("test:r",2));check(state.snapshot().retainedNodes()<100,"replacement releases prior retained charge");
        byte[] oversized=packet("test:r",Map.of("test:a",new int[67000]));var cursor=new VanillaConnection.Cursor(oversized);reject(()->new TagState().accept(0,cursor,Map.of("test:r",2)),"tag_nodes");check(cursor.bytes.available()==67000,"node budget rejects before reading/boxing member IDs");
        TagState cumulative=new TagState();apply(cumulative,packet("test:r",Map.of("test:a",new int[35000])),Map.of("test:r",2,"test:s",2));reject(()->apply(cumulative,packet("test:s",Map.of("test:a",new int[35000])),Map.of("test:r",2,"test:s",2)),"tag_nodes");eq(cumulative.snapshot().presence("test:r","test:a"),TagState.Presence.UNOBSERVED);
        Map<String,int[]> manyNames=new LinkedHashMap<>();for(int i=0;i<70;i++)manyNames.put("test:t_"+i+"x".repeat(8000),new int[0]);TagState bytes=new TagState();apply(bytes,packet("test:r",manyNames),Map.of());reject(()->apply(bytes,packet("test:s",manyNames),Map.of()),"tag_bytes");eq(bytes.snapshot().presence("test:r","test:a"),TagState.Presence.UNOBSERVED);
        byte[] tooLarge=new byte[1024*1024+1];var full=new VanillaConnection.Cursor(tooLarge);reject(()->new TagState().accept(0,full,Map.of()),"tag_packet_bytes");check(full.bytes.available()==tooLarge.length,"packet byte bound checks before cursor reads");
        var field=TagState.class.getDeclaredField("revision");field.setAccessible(true);TagState exhausted=new TagState();apply(exhausted,NORMAL,Map.of("test:r",2));field.setLong(exhausted,Protocol.MAX_SEQUENCE);reject(()->apply(exhausted,NORMAL,Map.of("test:r",2)),"tag_lifetime");check(exhausted.snapshot().exhausted(),"overflow fails closed");eq(exhausted.snapshot().presence("test:r","test:a"),TagState.Presence.UNOBSERVED);
    }
    static byte[] packet(String registry,Map<String,int[]> tags)throws Exception {ByteArrayOutputStream bytes=new ByteArrayOutputStream();DataOutputStream out=new DataOutputStream(bytes);vi(out,1);text(out,registry);vi(out,tags.size());for(var tag:tags.entrySet()){text(out,tag.getKey());vi(out,tag.getValue().length);for(int id:tag.getValue())vi(out,id);}return bytes.toByteArray();}
    static void text(DataOutputStream out,String value)throws Exception {byte[] bytes=value.getBytes(StandardCharsets.UTF_8);vi(out,bytes.length);out.write(bytes);}
    static void vi(DataOutputStream out,int n)throws Exception {do{int b=n&127;n>>>=7;out.writeByte(n==0?b:b|128);}while(n!=0);}
    interface Action {void run()throws Exception;}
    static void reject(Action action,String code)throws Exception {try{action.run();throw new AssertionError("expected rejection "+code);}catch(IOException error){if(code!=null)eq(error.getMessage(),code);checks++;}}
    static void rejectMutation(Action action)throws Exception {try{action.run();throw new AssertionError("mutable snapshot");}catch(UnsupportedOperationException expected){checks++;}}
    static void eq(Object actual,Object expected){check(Objects.equals(actual,expected),"expected="+expected+" actual="+actual);}
    static void check(boolean valid,String why){if(!valid)throw new AssertionError(why);checks++;}
}
