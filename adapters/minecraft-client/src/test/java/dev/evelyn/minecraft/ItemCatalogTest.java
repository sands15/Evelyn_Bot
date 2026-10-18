package dev.evelyn.minecraft;

import com.google.gson.*;
import java.io.IOException;
import java.io.InputStream;
import java.lang.reflect.InvocationTargetException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.function.Consumer;

/** Independent fixed official-metadata expectations; no Minecraft runtime is on this test's classpath. */
public final class ItemCatalogTest {
    private static Class<?> type;
    private static int checks;
    public static void main(String[] args)throws Exception {
        try{type=Class.forName("dev.evelyn.minecraft.ItemCatalog");}
        catch(ClassNotFoundException missing){throw new AssertionError("vanilla reference item catalog is not implemented",missing);}
        Object catalog=call(null,"loadPinned",new Class[0]);
        check(number(catalog,"size")==1658&&number(catalog,"componentTypeCount")==122,"fixed official extents");
        check(call(catalog,"provenance",new Class[0]).toString().equals("VANILLA_REFERENCE"),"catalog does not claim server state");
        Object air=item(catalog,0),stone=item(catalog,1),apple=item(catalog,1007);
        check(text(air,"name").equals("minecraft:air")&&text(stone,"name").equals("minecraft:stone"),"fixed id/name oracle");
        Object empty=call(air,"defaultStack",new Class[0]);
        check((boolean)call(empty,"empty",new Class[0])&&number(empty,"count")==0&&number(empty,"maxStackSize")==1&&json(empty,"components").size()==0,"air stack differs from prototype");
        check(json(air,"components").has("minecraft:max_stack_size"),"air prototype retained independently");
        check(text(call(catalog,"byBlockName",new Class[]{String.class},"minecraft:oak_log"),"name").equals("minecraft:oak_log")&&number(call(catalog,"byBlockName",new Class[]{String.class},"minecraft:oak_log"),"id")==163,"block/item mapping oracle");
        check(number(call(catalog,"byName",new Class[]{String.class},"minecraft:water_bucket"),"id")==1129&&call(item(catalog,1129),"blockName",new Class[0])==null,"bucket is not BlockItem mapping");
        JsonObject food=json(apple,"resolvedSemantics").getAsJsonObject("food"),consume=json(apple,"resolvedSemantics").getAsJsonObject("consumable");
        check(food.get("nutrition").getAsInt()==4&&food.get("saturation").getAsDouble()==2.4,"apple food oracle");
        check(consume.get("consume_ticks").getAsInt()==32&&consume.get("consume_seconds").getAsDouble()==1.6,"omitted consumable defaults retained");
        check(json(apple,"components").getAsJsonObject("minecraft:consumable").size()==0,"official empty consumable codec representation retained");
        for(int id:new int[]{1135,1136,1137,1138})check(json(item(catalog,id),"components").has("minecraft:food")&&!json(item(catalog,id),"components").has("minecraft:consumable"),"food-bearing fish bucket is not a consumable");
        Object pick=item(catalog,1052),chest=item(catalog,1081);
        check(number(call(pick,"defaultStack",new Class[0]),"maxDamage")==1561&&json(pick,"resolvedSemantics").getAsJsonObject("tool").get("default_mining_speed").getAsInt()==1,"pickaxe durability/tool oracle");
        check(json(pick,"resolvedSemantics").getAsJsonObject("weapon").get("item_damage_per_attack").getAsInt()==2,"pickaxe weapon oracle");
        check(json(item(catalog,1050),"resolvedSemantics").getAsJsonObject("tool").getAsJsonArray("rules").get(1).getAsJsonObject().get("speed_override").getAsFloat()==Float.MAX_VALUE,"official rounded decimal Float.MAX_VALUE stays finite");
        check(number(call(chest,"defaultStack",new Class[0]),"maxDamage")==240&&json(chest,"resolvedSemantics").getAsJsonObject("equippable").get("slot").getAsString().equals("chest"),"equipment oracle");
        int[] potionIds={1271,1442,1444,1445};String[] potionNames={"minecraft:water","minecraft:water","minecraft:poison","minecraft:water"};
        for(int i=0;i<potionIds.length;i++){
            Object potion=item(catalog,potionIds[i]);
            check(json(potion,"components").getAsJsonObject("minecraft:potion_contents").size()==0,"prototype potion contents empty");
            check(json(call(potion,"defaultStack",new Class[0]),"components").getAsJsonObject("minecraft:potion_contents").get("potion").getAsString().equals(potionNames[i]),"actual default-stack potion override");
        }
        int overrides=0,blocks=0;
        for(int id=0;id<1658;id++){
            Object value=item(catalog,id);check(number(value,"id")==id&&number(call(catalog,"byName",new Class[]{String.class},text(value,"name")),"id")==id,"full id/name roundtrip");
            check(call(value,"provenance",new Class[0]).toString().equals("VANILLA_REFERENCE"),"entry reference provenance");
            if((boolean)call(value,"hasDefaultStackOverride",new Class[0]))overrides++;
            if(call(value,"blockName",new Class[0])!=null)blocks++;
        }
        check(overrides==5&&blocks==1141,"official override and block-item counts");
        Object component=call(catalog,"componentType",new Class[]{int.class},24);
        check(text(component,"name").equals("minecraft:consumable")&&number(call(catalog,"componentType",new Class[]{String.class},"minecraft:food"),"id")==23,"component registry bidirectional oracle");
        for(int id:new int[]{-1,1658,Integer.MAX_VALUE})rejectArgument(()->item(catalog,id));
        rejectArgument(()->call(catalog,"byName",new Class[]{String.class},"minecraft:not_registered"));
        rejectArgument(()->call(catalog,"byBlockName",new Class[]{String.class},"minecraft:water"));
        rejectArgument(()->call(catalog,"componentType",new Class[]{int.class},122));
        rejectArgument(()->call(catalog,"componentType",new Class[]{String.class},"minecraft:unknown"));
        rejectArgument(()->call(catalog,"byName",new Class[]{String.class},(Object)null));
        json(apple,"components").remove("minecraft:food");json(apple,"resolvedSemantics").getAsJsonObject("food").addProperty("nutrition",0);
        check(json(apple,"components").has("minecraft:food")&&json(apple,"resolvedSemantics").getAsJsonObject("food").get("nutrition").getAsInt()==4,"prototype and nested semantics defensive copies");
        Object potionStack=call(item(catalog,1444),"defaultStack",new Class[0]);json(potionStack,"components").getAsJsonObject("minecraft:potion_contents").addProperty("potion","minecraft:water");
        check(json(potionStack,"components").getAsJsonObject("minecraft:potion_contents").get("potion").getAsString().equals("minecraft:poison"),"default stack defensive copy");
        byte[] bytes;try(InputStream in=ItemCatalogTest.class.getResourceAsStream("/dev/evelyn/minecraft/item-catalog-26.3.json")){if(in==null)throw new AssertionError("item resource missing");bytes=in.readAllBytes();}
        rejectIO(()->read(bytes,"0".repeat(64)));
        JsonObject original=JsonParser.parseString(new String(bytes,StandardCharsets.UTF_8)).getAsJsonObject();
        check(original.get("source_sha256").getAsString().equals("0e38833ec5e0ab8a87f2770768e15e7b5335a5098ab0b2b9bfa059dc4ed0d9e1"),"fixed official extraction pin");
        malformed(original,r->r.addProperty("game_version","26.2"));malformed(original,r->r.addProperty("protocol",776));
        malformed(original,r->r.addProperty("source_sha256","0".repeat(64)));malformed(original,r->r.addProperty("server_sha1","0".repeat(40)));
        malformed(original,r->r.addProperty("provenance","SERVER_KNOWN"));malformed(original,r->r.remove("schema"));malformed(original,r->r.addProperty("extra",true));
        malformed(original,r->r.getAsJsonArray("items").remove(0));malformed(original,r->row(r,1).set(0,new JsonPrimitive(0)));
        malformed(original,r->row(r,1).set(1,new JsonPrimitive("minecraft:air")));malformed(original,r->row(r,1).set(1,new JsonPrimitive("unknown name")));
        malformed(original,r->row(r,2).set(2,new JsonPrimitive("minecraft:stone")));malformed(original,r->row(r,1).remove(6));
        malformed(original,r->r.getAsJsonArray("component_types").remove(0));
        malformed(original,r->r.getAsJsonArray("component_types").get(1).getAsJsonArray().set(0,new JsonPrimitive(0)));
        malformed(original,r->r.getAsJsonArray("component_types").get(1).getAsJsonArray().set(1,new JsonPrimitive("minecraft:custom_data")));
        malformed(original,r->r.getAsJsonArray("component_types").get(1).getAsJsonArray().set(2,new JsonPrimitive("true")));
        malformed(original,r->row(r,1007).get(3).getAsJsonObject().add("minecraft:unknown",new JsonObject()));
        malformed(original,r->row(r,1007).get(4).getAsJsonObject().getAsJsonObject("food").remove("nutrition"));
        malformed(original,r->row(r,1007).get(4).getAsJsonObject().getAsJsonObject("consumable").addProperty("consume_ticks","32"));
        malformed(original,r->row(r,1052).get(4).getAsJsonObject().getAsJsonObject("tool").addProperty("default_mining_speed",1e50));
        malformed(original,r->row(r,1).get(5).getAsJsonArray().set(0,new JsonPrimitive("false")));
        malformed(original,r->row(r,1).get(5).getAsJsonArray().set(1,new JsonPrimitive(-1)));
        malformed(original,r->row(r,1).get(5).getAsJsonArray().set(2,new JsonPrimitive(0)));
        malformed(original,r->row(r,1).get(5).getAsJsonArray().set(3,new JsonPrimitive(0.5)));
        malformed(original,r->row(r,1444).set(6,new JsonArray()));
        malformed(original,r->row(r,1444).get(6).getAsJsonArray().set(0,new JsonPrimitive("not_components")));
        textMalformed("{\"schema\":1,\"schema\":1}");textMalformed(new String(bytes,StandardCharsets.UTF_8)+" {}");
        textMalformed("[".repeat(40)+"0"+"]".repeat(40));textMalformed("{\"n\":NaN}");
        byte[] invalidUTF8={(byte)0xc3,(byte)0x28};rejectIO(()->read(invalidUTF8,sha(invalidUTF8)));
        byte[] oversized=new byte[4*1024*1024+1];rejectIO(()->read(oversized,sha(oversized)));
        System.out.println("ITEM_CATALOG_PASS "+checks);
    }
    private static JsonArray row(JsonObject r,int id){return r.getAsJsonArray("items").get(id).getAsJsonArray();}
    private static Object item(Object catalog,int id)throws Exception{return call(catalog,"lookup",new Class[]{int.class},id);}
    private static int number(Object value,String name)throws Exception{return (int)call(value,name,new Class[0]);}
    private static String text(Object value,String name)throws Exception{return (String)call(value,name,new Class[0]);}
    private static JsonObject json(Object value,String name)throws Exception{return (JsonObject)call(value,name,new Class[0]);}
    private static Object read(byte[] bytes,String hash)throws Exception{return call(null,"read",new Class[]{byte[].class,String.class},bytes,hash);}
    private static Object call(Object target,String name,Class<?>[] signature,Object... args)throws Exception{
        try{return(target==null?type:target.getClass()).getDeclaredMethod(name,signature).invoke(target,args);}
        catch(InvocationTargetException failure){if(failure.getCause() instanceof Exception e)throw e;throw failure;}
    }
    private static void malformed(JsonObject original,Consumer<JsonObject> mutation)throws Exception{JsonObject changed=original.deepCopy();mutation.accept(changed);textMalformed(changed.toString());}
    private static void textMalformed(String text)throws Exception{byte[] bytes=text.getBytes(StandardCharsets.UTF_8);rejectIO(()->read(bytes,sha(bytes)));}
    private static String sha(byte[] bytes)throws Exception{return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));}
    private static void check(boolean value,String why){if(!value)throw new AssertionError(why);checks++;}
    private static void rejectIO(Checked action)throws Exception{try{action.run();}catch(IOException expected){checks++;return;}throw new AssertionError("invalid catalog accepted");}
    private static void rejectArgument(Checked action)throws Exception{try{action.run();}catch(IllegalArgumentException expected){checks++;return;}throw new AssertionError("unknown catalog value accepted");}
    private interface Checked{void run()throws Exception;}
}
