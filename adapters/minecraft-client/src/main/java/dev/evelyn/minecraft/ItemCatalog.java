package dev.evelyn.minecraft;

import com.google.gson.*;
import com.google.gson.stream.JsonReader;
import com.google.gson.stream.JsonToken;
import java.io.*;
import java.math.BigDecimal;
import java.nio.ByteBuffer;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.*;

/** Pinned vanilla reference metadata, never effective server components, tags, or action authority. */
final class ItemCatalog {
    static final String RESOURCE_SHA256="7cdc823283ba8bc58bfe1fca67f91b35f04a607d1e0dc9e0eca1a1af515d01ab";
    static final String SOURCE_SHA256="0e38833ec5e0ab8a87f2770768e15e7b5335a5098ab0b2b9bfa059dc4ed0d9e1";
    private static final String SERVER_SHA1="cc3964451a3b32a0488110d5e3e6ad8d75fe6a66";
    static final int ITEM_COUNT=1658;
    private static final int TYPE_COUNT=122, MAX_BYTES=4*1024*1024;
    private static final Set<String> ROOT_KEYS=Set.of("schema","game_version","protocol","source_sha256","server_sha1","provenance","component_types","items");
    private static final Set<String> SEMANTICS=Set.of("food","consumable","tool","weapon","equippable");
    enum Provenance { VANILLA_REFERENCE }
    record ComponentType(int id,String name,boolean persistent) { }
    record StackDefaults(boolean empty,int count,int maxStackSize,int maxDamage,JsonObject components,JsonObject resolvedSemantics) {
        StackDefaults { components=components.deepCopy();resolvedSemantics=resolvedSemantics.deepCopy(); }
        @Override public JsonObject components(){return components.deepCopy();}
        @Override public JsonObject resolvedSemantics(){return resolvedSemantics.deepCopy();}
        Provenance provenance(){return Provenance.VANILLA_REFERENCE;}
    }
    record Item(int id,String name,String blockName,JsonObject components,JsonObject resolvedSemantics,StackDefaults defaultStack,boolean hasDefaultStackOverride) {
        Item { components=components.deepCopy();resolvedSemantics=resolvedSemantics.deepCopy(); }
        @Override public JsonObject components(){return components.deepCopy();}
        @Override public JsonObject resolvedSemantics(){return resolvedSemantics.deepCopy();}
        Provenance provenance(){return Provenance.VANILLA_REFERENCE;}
    }
    private static ItemCatalog pinned;
    private final Item[] items;
    private final ComponentType[] types;
    private final Map<String,Item> names,blocks;
    private final Map<String,ComponentType> typeNames;
    private ItemCatalog(Item[] items,ComponentType[] types,Map<String,Item> names,Map<String,Item> blocks,Map<String,ComponentType> typeNames) {
        this.items=items.clone();this.types=types.clone();this.names=Map.copyOf(names);this.blocks=Map.copyOf(blocks);this.typeNames=Map.copyOf(typeNames);
    }
    static synchronized ItemCatalog loadPinned()throws IOException {
        if(pinned==null)try(InputStream in=ItemCatalog.class.getResourceAsStream("/dev/evelyn/minecraft/item-catalog-26.3.json")){
            if(in==null)throw failure("missing");pinned=read(in.readNBytes(MAX_BYTES+1),RESOURCE_SHA256);
        }
        return pinned;
    }
    int size(){return items.length;}
    int componentTypeCount(){return types.length;}
    Provenance provenance(){return Provenance.VANILLA_REFERENCE;}
    Item lookup(int id){if(id<0||id>=items.length)throw unknown();return items[id];}
    Item byName(String name){if(name==null)throw unknown();Item item=names.get(name);if(item==null)throw unknown();return item;}
    /** Exact BlockItem.getBlock mapping; wall variants and placement rules are not inferred. */
    Item byBlockName(String name){if(name==null)throw unknown();Item item=blocks.get(name);if(item==null)throw unknown();return item;}
    ComponentType componentType(int id){if(id<0||id>=types.length)throw unknown();return types[id];}
    ComponentType componentType(String name){if(name==null)throw unknown();ComponentType type=typeNames.get(name);if(type==null)throw unknown();return type;}

    // Separate from classpath loading to validate malformed data independently of the fixed resource hash.
    static ItemCatalog read(byte[] bytes,String expectedSha256)throws IOException {
        if(bytes==null||bytes.length>MAX_BYTES||expectedSha256==null)throw failure("size");
        try {
            if(!HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)).equals(expectedSha256))throw failure("hash");
            String text=StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT).onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString();
            JsonObject root;
            try(JsonReader reader=new JsonReader(new StringReader(text))){reader.setStrictness(Strictness.STRICT);root=object(tree(reader,0,new int[]{100_000}));if(reader.peek()!=JsonToken.END_DOCUMENT)throw failure("trailing");}
            fields(root,ROOT_KEYS);
            if(integer(root.get("schema"))!=1||integer(root.get("protocol"))!=777||!string(root.get("game_version")).equals("26.3")
                ||!string(root.get("source_sha256")).equals(SOURCE_SHA256)||!string(root.get("server_sha1")).equals(SERVER_SHA1)
                ||!string(root.get("provenance")).equals("VANILLA_REFERENCE"))throw failure("version");
            ComponentType[] types=new ComponentType[TYPE_COUNT];Map<String,ComponentType> typeNames=new HashMap<>();
            JsonArray typeRows=array(root.get("component_types"),TYPE_COUNT);
            for(int i=0;i<TYPE_COUNT;i++){
                JsonArray row=array(typeRows.get(i),3);if(integer(row.get(0))!=i)throw failure("type_id");
                ComponentType type=new ComponentType(i,identifier(row.get(1)),bool(row.get(2)));types[i]=type;
                if(typeNames.putIfAbsent(type.name(),type)!=null)throw failure("duplicate_type");
            }
            Item[] items=new Item[ITEM_COUNT];Map<String,Item> names=new HashMap<>(),blocks=new HashMap<>();
            JsonArray rows=array(root.get("items"),ITEM_COUNT);
            for(int i=0;i<ITEM_COUNT;i++){
                JsonArray row=array(rows.get(i),7);if(integer(row.get(0))!=i)throw failure("item_id");
                String name=identifier(row.get(1)),block=row.get(2).isJsonNull()?null:identifier(row.get(2));
                JsonObject components=componentMap(row.get(3),typeNames),semantics=semanticMap(row.get(4),components);
                boolean override=!row.get(6).isJsonNull();JsonObject stackComponents=components,stackSemantics=semantics;
                if(override){JsonArray value=array(row.get(6),2);stackComponents=componentMap(value.get(0),typeNames);stackSemantics=semanticMap(value.get(1),stackComponents);}
                JsonArray stack=array(row.get(5),4);boolean empty=bool(stack.get(0));int count=integer(stack.get(1)),max=integer(stack.get(2)),damage=integer(stack.get(3));
                if(max<1||max>99||count>max||empty!=(count==0)||empty!=(i==0)||(!empty&&count!=1)
                    ||(empty&&(!name.equals("minecraft:air")||stackComponents.size()!=0||max!=1||damage!=0))
                    ||(!empty&&(!stackComponents.has("minecraft:max_stack_size")||integer(stackComponents.get("minecraft:max_stack_size"))!=max))
                    ||(stackComponents.has("minecraft:max_damage")?integer(stackComponents.get("minecraft:max_damage"))!=damage:damage!=0))throw failure("default_stack");
                Item item=new Item(i,name,block,components,semantics,new StackDefaults(empty,count,max,damage,stackComponents,stackSemantics),override);items[i]=item;
                if(names.putIfAbsent(name,item)!=null||(block!=null&&blocks.putIfAbsent(block,item)!=null))throw failure("duplicate_item");
            }
            return new ItemCatalog(items,types,names,blocks,typeNames);
        }catch(IOException failure){throw failure;}
        catch(RuntimeException|NoSuchAlgorithmException invalid){throw failure("invalid");}
    }

    private static JsonObject componentMap(JsonElement value,Map<String,ComponentType> types)throws IOException {
        JsonObject map=object(value);
        for(var entry:map.entrySet()){
            ComponentType type=types.get(entry.getKey());if(type==null||!type.persistent()||entry.getValue().isJsonNull())throw failure("component_type");
        }
        if(map.has("minecraft:max_stack_size")){int max=integer(map.get("minecraft:max_stack_size"));if(max<1||max>99)throw failure("max_stack");}
        if(map.has("minecraft:max_damage")&&integer(map.get("minecraft:max_damage"))<1)throw failure("max_damage");
        return map;
    }
    private static JsonObject semanticMap(JsonElement value,JsonObject components)throws IOException {
        JsonObject map=object(value);Set<String> expected=new HashSet<>();for(String name:SEMANTICS)if(components.has("minecraft:"+name))expected.add(name);fields(map,expected);
        for(String name:expected){JsonObject data=object(map.get(name)),raw=object(components.get("minecraft:"+name));
            switch(name){
                case "food" -> {fields(data,Set.of("nutrition","saturation","can_always_eat"));integer(data.get("nutrition"));number(data.get("saturation"));bool(data.get("can_always_eat"));integer(raw.get("nutrition"));number(raw.get("saturation"));agree(raw,data,"nutrition","saturation","can_always_eat");}
                case "consumable" -> {
                    fields(data,Set.of("consume_seconds","consume_ticks","animation","sound","has_consume_particles","on_consume_effect_count"));number(data.get("consume_seconds"));integer(data.get("consume_ticks"));identifier(data.get("sound"));bool(data.get("has_consume_particles"));
                    if(!Set.of("none","eat","drink","block","bow","spear","crossbow","spyglass","toot_horn","brush","bundle").contains(string(data.get("animation"))))throw failure("animation");
                    int effects=integer(data.get("on_consume_effect_count"));if(raw.has("on_consume_effects")?arrayAny(raw.get("on_consume_effects")).size()!=effects:effects!=0)throw failure("consume_effects");agree(raw,data,"consume_seconds","animation","sound","has_consume_particles");
                }
                case "tool" -> {
                    fields(data,Set.of("default_mining_speed","damage_per_block","can_destroy_blocks_in_creative","rules"));number(data.get("default_mining_speed"));integer(data.get("damage_per_block"));bool(data.get("can_destroy_blocks_in_creative"));agree(raw,data,"default_mining_speed","damage_per_block","can_destroy_blocks_in_creative");
                    JsonArray rules=arrayAny(data.get("rules")),rawRules=array(raw.get("rules"),rules.size());
                    for(int i=0;i<rules.size();i++){JsonObject rule=object(rules.get(i)),original=object(rawRules.get(i));fields(rule,Set.of("blocks_codec_json","speed_override","correct_for_drops_override"));
                        if(!rule.get("blocks_codec_json").equals(original.get("blocks")))throw failure("tool_blocks");
                        JsonElement speed=rule.get("speed_override"),drops=rule.get("correct_for_drops_override");if(!speed.isJsonNull())number(speed);if(!drops.isJsonNull())bool(drops);
                        if(!speed.equals(original.has("speed")?original.get("speed"):JsonNull.INSTANCE)||!drops.equals(original.has("correct_for_drops")?original.get("correct_for_drops"):JsonNull.INSTANCE))throw failure("tool_rule");}
                }
                case "weapon" -> {fields(data,Set.of("item_damage_per_attack","disable_blocking_for_seconds"));integer(data.get("item_damage_per_attack"));number(data.get("disable_blocking_for_seconds"));agree(raw,data,"item_damage_per_attack","disable_blocking_for_seconds");}
                case "equippable" -> {
                    fields(data,Set.of("slot","dispensable","swappable","damage_on_hurt","equip_on_interact","can_be_sheared","equip_sound","shearing_sound","asset_id","camera_overlay","allowed_entities_present"));
                    if(!Set.of("mainhand","offhand","feet","legs","chest","head","body","saddle").contains(string(data.get("slot"))))throw failure("equipment_slot");
                    for(String flag:List.of("dispensable","swappable","damage_on_hurt","equip_on_interact","can_be_sheared","allowed_entities_present"))bool(data.get(flag));
                    for(String key:List.of("equip_sound","shearing_sound"))identifier(data.get(key));for(String key:List.of("asset_id","camera_overlay"))if(!data.get(key).isJsonNull())identifier(data.get(key));
                    if(raw.has("allowed_entities")!=bool(data.get("allowed_entities_present")))throw failure("equipment_entities");agree(raw,data,"slot","dispensable","swappable","damage_on_hurt","equip_on_interact","can_be_sheared","equip_sound","shearing_sound","asset_id","camera_overlay");
                }
                default -> throw failure("semantics");
            }
        }
        return map;
    }
    private static void agree(JsonObject raw,JsonObject resolved,String... fields)throws IOException {for(String field:fields)if(raw.has(field)&&!raw.get(field).equals(resolved.get(field)))throw failure("semantic_mismatch");}
    private static JsonElement tree(JsonReader reader,int depth,int[] budget)throws IOException {
        if(depth>16||--budget[0]<0)throw failure("json_budget");
        return switch(reader.peek()){
            case BEGIN_OBJECT -> {JsonObject o=new JsonObject();reader.beginObject();while(reader.hasNext()){String key=reader.nextName();if(key.length()>128||o.has(key)||o.size()>=128)throw failure("json_field");o.add(key,tree(reader,depth+1,budget));}reader.endObject();yield o;}
            case BEGIN_ARRAY -> {JsonArray a=new JsonArray();reader.beginArray();while(reader.hasNext()){if(a.size()>=ITEM_COUNT)throw failure("json_array");a.add(tree(reader,depth+1,budget));}reader.endArray();yield a;}
            case STRING -> {String text=reader.nextString();if(text.length()>256)throw failure("json_string");yield new JsonPrimitive(text);}
            case NUMBER -> {String text=reader.nextString();if(text.length()>40)throw failure("json_number");yield new JsonPrimitive(new BigDecimal(text));}
            case BOOLEAN -> new JsonPrimitive(reader.nextBoolean());
            case NULL -> {reader.nextNull();yield JsonNull.INSTANCE;}
            default -> throw failure("json_token");
        };
    }
    private static void fields(JsonObject object,Set<String> fields)throws IOException {if(!object.keySet().equals(fields))throw failure("fields");}
    private static JsonObject object(JsonElement value)throws IOException {if(value==null||!value.isJsonObject())throw failure("object");return value.getAsJsonObject();}
    private static JsonArray arrayAny(JsonElement value)throws IOException {if(value==null||!value.isJsonArray())throw failure("array");return value.getAsJsonArray();}
    private static JsonArray array(JsonElement value,int size)throws IOException {JsonArray array=arrayAny(value);if(array.size()!=size)throw failure("array_size");return array;}
    private static String string(JsonElement value)throws IOException {if(value==null||!value.isJsonPrimitive()||!value.getAsJsonPrimitive().isString())throw failure("string");return value.getAsString();}
    private static String identifier(JsonElement value)throws IOException {String name=string(value);if(!name.matches("minecraft:[a-z0-9_./-]{1,100}"))throw failure("identifier");return name;}
    private static int integer(JsonElement value)throws IOException {
        if(value==null||!value.isJsonPrimitive()||!value.getAsJsonPrimitive().isNumber()||!value.getAsString().matches("0|[1-9][0-9]{0,9}"))throw failure("integer");
        try{return Integer.parseInt(value.getAsString());}catch(NumberFormatException invalid){throw failure("integer");}
    }
    private static double number(JsonElement value)throws IOException {if(value==null||!value.isJsonPrimitive()||!value.getAsJsonPrimitive().isNumber())throw failure("number");double result=value.getAsDouble();if(!Double.isFinite(result)||result<0||!Float.isFinite(value.getAsFloat()))throw failure("number");return result;}
    private static boolean bool(JsonElement value)throws IOException {if(value==null||!value.isJsonPrimitive()||!value.getAsJsonPrimitive().isBoolean())throw failure("boolean");return value.getAsBoolean();}
    private static IOException failure(String code){return new IOException("item_catalog_"+code);}
    private static IllegalArgumentException unknown(){return new IllegalArgumentException("unknown_vanilla_reference_item_or_component");}
}
