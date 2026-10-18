package dev.evelyn.minecraft;

import com.google.gson.*;
import java.io.IOException;
import java.lang.reflect.*;
import java.util.*;

/** Shared-budget counterexamples are independent of any recipe-state implementation. */
public final class ComponentDecodeSessionTest {
    private static int checks;
    private static final HexFormat HEX=HexFormat.of();
    private static Object call(Object target,String name,Class<?>[] types,Object... args)throws Exception {
        try {
            Method method=target instanceof Class<?> type?type.getDeclaredMethod(name,types):target.getClass().getDeclaredMethod(name,types);
            method.setAccessible(true);return method.invoke(target instanceof Class<?>?null:target,args);
        } catch(NoSuchMethodException missing) { throw new AssertionError("required shared DecodeSession API missing: "+name,missing); }
        catch(InvocationTargetException failed) { if(failed.getCause() instanceof Exception cause)throw cause;throw failed; }
    }
    private static Object open(byte[] bytes,int offset,Map<String,Integer> registries)throws Exception {
        return call(ComponentWireCodec.class,"open",new Class<?>[]{byte[].class,int.class,Map.class},bytes,offset,registries);
    }
    private static Object open(String hex)throws Exception{return open(HEX.parseHex(hex),0,Map.of());}
    private static int number(Object session,String method)throws Exception{return (int)call(session,method,new Class<?>[0]);}
    private static void reserve(Object session,int nodes)throws Exception{call(session,"reserve",new Class<?>[]{int.class},nodes);}
    private static ComponentWireCodec.Decoded component(Object session,int id,int depth)throws Exception {
        return (ComponentWireCodec.Decoded)call(session,"readComponent",new Class<?>[]{int.class,int.class},id,depth);
    }
    private static ComponentWireCodec.Decoded schema(Object session,JsonObject schema,int depth)throws Exception {
        return (ComponentWireCodec.Decoded)call(session,"readTrustedSchema",new Class<?>[]{JsonObject.class,int.class},schema,depth);
    }
    private static JsonObject json(String text){return JsonParser.parseString(text).getAsJsonObject();}
    private static void eq(Object actual,Object expected,String label){checks++;if(!Objects.equals(actual,expected))throw new AssertionError(label+": "+actual+" != "+expected);}
    @FunctionalInterface private interface Action{void run()throws Exception;}
    private static void reject(Action action,String label)throws Exception {
        try{action.run();throw new AssertionError("accepted "+label);}catch(IOException expected){checks++;}
    }
    public static void main(String[] args)throws Exception {
        byte[] original={0x7e,1,2};var copied=open(original,1,Map.of());original[1]=99;
        eq(number(copied,"position"),1,"nonzero position");eq(number(copied,"remaining"),2,"remaining payload");
        eq(number(copied,"vi"),1,"session owns immutable input copy");eq(number(copied,"position"),2,"primitive advances same cursor");
        eq(number(copied,"u8"),2,"unsigned byte forwarding");eq(number(copied,"remaining"),0,"exact remaining");
        eq(number(copied,"nodesUsed"),0,"raw primitive caller reserves outer structures");
        var shared=open("");reserve(shared,65534);
        var first=component(shared,4,1);var second=component(shared,4,1);
        eq(first.nodeCount(),1,"zero-wire component still allocates a node");eq(second.nodeCount(),1,"per-value delta");
        eq(number(shared,"nodesUsed"),65536,"outer and both components share one budget");
        reject(()->component(shared,4,1),"third zero-wire component after exhaustion");
        eq(number(shared,"nodesUsed"),65536,"failed allocation does not overshoot");eq(number(shared,"position"),0,"zero-wire attack consumes no bytes");
        var budgetBeforeDecode=open("044019999a00");reserve(budgetBeforeDecode,65529);
        reject(()->component(budgetBeforeDecode,23,0),"valid food after outer budget leaves only seven nodes");
        eq(number(budgetBeforeDecode,"nodesUsed")<=65536,true,"component allocation stops at shared limit");
        var normal=open("044019999a00");reserve(normal,17);var food=component(normal,23,0);
        eq(food.nodeCount(),8,"official food node fixture");eq(number(normal,"nodesUsed"),25,"outer plus food count");
        eq(food.start(),0,"food span start");eq(food.end(),6,"food span end");
        eq(food.value().get("syntax_known").getAsBoolean(),true,"component syntax only");
        eq(food.value().get("effective_known").getAsBoolean(),false,"no effective promotion");

        JsonObject holders=json("{\"kind\":\"union\",\"form\":\"holder_set\",\"registry\":\"minecraft:item\"}");
        var holderSession=open("030107");reserve(holderSession,65532);var held=schema(holderSession,holders,1);
        eq(held.nodeCount(),4,"HolderSet objects and raw entries");eq(number(holderSession,"nodesUsed"),65536,"HolderSet shares outer packet budget");
        eq(held.end(),3,"HolderSet full consumption");
        var overHolder=open("030107");reserve(overHolder,65533);reject(()->schema(overHolder,holders,1),"same valid HolderSet with insufficient shared nodes");
        var patch=open("01010405");reserve(patch,11);
        var patched=(ComponentWireCodec.Decoded)call(patch,"readPatch",new Class<?>[]{int.class},1);
        eq(patched.nodeCount(),6,"patch adds and removals counted");eq(number(patch,"nodesUsed"),17,"patch budget shared");eq(patched.end(),4,"patch consumption");
        var overPatch=open("01010405");reserve(overPatch,65531);reject(()->call(overPatch,"readPatch",new Class<?>[]{int.class},1),"patch cannot reset outer budget");

        JsonObject template=json("{\"kind\":\"nested-stack\",\"form\":\"item_template\",\"semantic_owner\":\"net.minecraft.world.item.ItemStackTemplate\",\"fields\":[{\"kind\":\"registry-holder\",\"registry\":\"minecraft:item\"},{\"kind\":\"primitive\",\"type\":\"varint\"},{\"kind\":\"nested-stack\",\"form\":\"component_patch\"}],\"field_names\":[\"item_id\",\"count\",\"component_patch\"]}");
        String templateBefore=template.toString();var stack=open("07010000");reserve(stack,3);var decoded=schema(stack,template,1);
        eq(decoded.end(),4,"ItemStackTemplate item then count then patch");
        var fields=decoded.value().getAsJsonArray("fields");
        eq(fields.get(0).getAsJsonObject().getAsJsonObject("value").get("id").getAsInt(),7,"template item ID independent literal");
        eq(fields.get(1).getAsJsonObject().getAsJsonObject("value").get("value").getAsInt(),1,"template count independent literal");
        eq(template.toString(),templateBefore,"trusted schema never returned or modified");
        eq(number(stack,"nodesUsed"),3+decoded.nodeCount(),"nested template shares one cumulative count");
        reject(()->schema(open("07010000"),template,32),"nested template cannot reset outer depth");
        reject(()->component(open("044019999a00"),23,32),"component children cannot reset outer depth");
        reject(()->call(open("01010405"),"readPatch",new Class<?>[]{int.class},32),"patch children cannot reset outer depth");
        JsonObject dynamic=json("{\"kind\":\"registry-holder\",\"registry\":\"minecraft:trim_pattern\"}");
        reject(()->schema(open("00"),dynamic,0),"dynamic registry without supplied context");
        var context=open(new byte[]{0},0,Map.of("minecraft:trim_pattern",1));eq(schema(context,dynamic,0).end(),1,"supplied dynamic context");
        var copiedRegistry=new HashMap<String,Integer>();copiedRegistry.put("minecraft:trim_pattern",1);var immutableContext=open(new byte[]{0},0,copiedRegistry);copiedRegistry.put("minecraft:trim_pattern",0);
        eq(schema(immutableContext,dynamic,0).end(),1,"registry counts copied at open");
        JsonObject nbt=json("{\"kind\":\"NBT\"}");var tag=schema(open("0a030001780000000100"),nbt,0);eq(tag.nodeCount(),4,"shared original NBT parser");eq(tag.end(),10,"NBT full span");
        JsonObject unit=json("{\"kind\":\"primitive\",\"type\":\"unit\"}");
        eq(schema(open(""),unit,32).nodeCount(),1,"shared depth boundary inclusive");
        reject(()->schema(open(""),unit,33),"external recipe depth cannot reset component depth");
        reject(()->component(open(""),4,-1),"negative depth");reject(()->call(open(""),"checkDepth",new Class<?>[]{int.class},33),"explicit shared depth");
        reject(()->reserve(open(""),-1),"negative reserve");reject(()->reserve(open(""),65537),"oversized reserve before caller allocation");
        var primitives=open("ac0201020a6d696e6563726166743a");eq(number(primitives,"vi"),300,"VarInt300");
        eq(call(primitives,"bool",new Class<?>[0]),true,"bool forwarding");eq(call(primitives,"count",new Class<?>[]{int.class},2),2,"count forwarding");
        eq(call(primitives,"identifier",new Class<?>[0]),"minecraft:","identifier semantics same as original reader");eq(number(primitives,"remaining"),0,"primitive envelope fully consumed");
        reject(()->call(open("02"),"bool",new Class<?>[0]),"invalid bool");
        var boundedOverlong=open("8000");eq(number(boundedOverlong,"vi"),0,"preserve original bounded overlong VarInt behavior");eq(number(boundedOverlong,"position"),2,"forwarded VarInt consumes original bytes");
        reject(()->number(open("808080808000"),"vi"),"VarInt beyond five bytes");
        reject(()->call(open("03"),"count",new Class<?>[]{int.class},2),"count policy bound");
        reject(()->open(null,0,Map.of()),"null bytes");reject(()->open(new byte[1048577],0,Map.of()),"one MiB input boundary");
        reject(()->open(new byte[1],-1,Map.of()),"negative offset");reject(()->open(new byte[1],2,Map.of()),"offset past end");
        reject(()->open(new byte[0],0,null),"null registry context");
        reject(()->schema(open(""),null,0),"missing trusted schema");
        eq(Modifier.isPublic(shared.getClass().getModifiers()),false,"facade stays package private");
        for(Field field:shared.getClass().getDeclaredFields())eq(Modifier.isPrivate(field.getModifiers()),true,"private Reader and internal state");
        System.out.println("COMPONENT_DECODE_SESSION_PASS "+checks);
    }
}
