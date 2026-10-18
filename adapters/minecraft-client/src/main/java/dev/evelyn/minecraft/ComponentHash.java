package dev.evelyn.minecraft;

import com.google.gson.*;
import java.util.*;
import java.util.zip.CRC32C;

/** Confirmed persistent value encodings only. Wire syntax and item defaults are separate authorities. */
final class ComponentHash {
    private static final int MAX_NODES=65536,MAX_COLLECTION=4096,MAX_DEPTH=96;
    private static final String[] DYES="white,orange,magenta,light_blue,yellow,lime,pink,gray,light_gray,cyan,purple,blue,brown,green,red,black".split(",");
    private static final class Unavailable extends RuntimeException {}
    private static void require(boolean condition){if(!condition)throw new Unavailable();}

    static OptionalInt component(int typeId,JsonObject typedWire){
        return component(typeId,typedWire,Map.of());
    }
    static OptionalInt component(int typeId,JsonObject typedWire,Map<String,List<String>> registryKeys){
        try {
            require(typeId>=0&&typeId<122&&typedWire!=null&&registryKeys!=null);
            if(typedWire.has("component_id"))require(integer(typedWire.get("component_id"))==typeId);
            inspect(typedWire);JsonObject value=unwrap(typedWire);int hash;
            switch(typeId){
                case 0,61 -> {require(kind(value,"nbt")&&integer(value.get("tag"))==10);hash=nbt(value,0);}
                case 1 -> {int n=intNode(value);require(n>=1&&n<=99);hash=scalar(8,n,4);}
                case 2 -> {int n=intNode(value);require(n>0);hash=scalar(8,n,4);}
                case 3,19 -> {int n=intNode(value);require(n>=0);hash=scalar(8,n,4);}
                case 4,34,120 -> {require(kind(value,"unit"));hash=map();}
                case 5 -> {
                    JsonObject[] f=fields(value,"UseEffects","canSprint","interactVibrations","speedMultiplier");List<int[]> entries=new ArrayList<>();
                    boolean sprint=bool(f[0]),vibrations=bool(f[1]);float speed=floating(f[2]);require(Float.compare(speed,0.0f)>=0&&speed<=1);
                    if(sprint)put(entries,"can_sprint",scalar(13,1,1));if(!vibrations)put(entries,"interact_vibrations",scalar(13,0,1));
                    if(!same(speed,0.2f))put(entries,"speed_multiplier",floatHash(speed));hash=map(entries.toArray(int[][]::new));
                }
                case 6,9 -> {require(kind(value,"nbt")&&integer(value.get("tag"))==8);hash=string(text(value.get("value")));}
                case 7 -> {float n=floating(value);require(Float.compare(n,0.0f)>=0&&n<=1);hash=floatHash(n);}
                case 10,35,73 -> {String id=stringNode(value);require(id.matches("[a-z0-9_.-]+:[a-z0-9/._-]*"));hash=string(id);}
                case 11 -> {
                    JsonObject list=value;if(kind(list,"record"))list=fields(list,"ItemLore","lines")[0];JsonArray values=list(list);int[] hashes=new int[values.size()];
                    for(int i=0;i<hashes.length;i++){JsonObject line=values.get(i).getAsJsonObject();require(kind(line,"nbt")&&integer(line.get("tag"))==8);hashes[i]=string(text(line.get("value")));}hash=list(hashes);
                }
                case 12,46,75,91,92,93,94,95,96,97,98,99,108,110,111,114,115,116,121 -> hash=enumHash(typeId,value);
                case 13,45 -> hash=enchantments(value,registryKeys);
                case 17 -> {
                    JsonObject[] f=fields(value,"CustomModelData","floats","flags","strings","colors");List<int[]> entries=new ArrayList<>();String[] names={"floats","flags","strings","colors"};
                    for(int field=0;field<4;field++){JsonArray values=list(f[field]);if(values.isEmpty())continue;int[] hashes=new int[values.size()];
                        for(int i=0;i<hashes.length;i++){JsonObject child=values.get(i).getAsJsonObject();hashes[i]=switch(field){case 0 -> floatHash(floating(child));case 1 -> scalar(13,bool(child)?1:0,1);case 2 -> string(stringNode(child));default -> scalar(8,intNode(child),4);};}put(entries,names[field],list(hashes));}
                    hash=map(entries.toArray(int[][]::new));
                }
                case 18 -> {
                    JsonObject[] f=fields(value,"TooltipDisplay","hideTooltip","hiddenComponents");require(list(f[1]).isEmpty());hash=bool(f[0])?map(pair("hide_tooltip",scalar(13,1,1))):map();
                }
                case 21 -> hash=scalar(13,bool(value)?1:0,1);
                case 22 -> {require(kind(value,"nbt")&&integer(value.get("tag"))==10&&value.getAsJsonArray("value").isEmpty());hash=map();}
                case 23 -> {
                    JsonObject[] f=fields(value,"FoodProperties","nutrition","saturation","can_always_eat");int nutrition=intNode(f[0]);float saturation=floating(f[1]);require(nutrition>=0&&saturation>=0);
                    List<int[]> entries=new ArrayList<>();put(entries,"nutrition",scalar(8,nutrition,4));put(entries,"saturation",floatHash(saturation));if(bool(f[2]))put(entries,"can_always_eat",scalar(13,1,1));hash=map(entries.toArray(int[][]::new));
                }
                case 28 -> {
                    JsonObject[] f=fields(value,"Tool","rules","default_mining_speed","damage_per_block","can_destroy_blocks_in_creative");require(list(f[0]).isEmpty());
                    float speed=floating(f[1]);int damage=intNode(f[2]);boolean creative=bool(f[3]);require(speed>=0&&damage>=0);List<int[]> entries=new ArrayList<>();put(entries,"rules",list());
                    if(!same(speed,1.0f))put(entries,"default_mining_speed",floatHash(speed));if(damage!=1)put(entries,"damage_per_block",scalar(8,damage,4));if(!creative)put(entries,"can_destroy_blocks_in_creative",scalar(13,0,1));hash=map(entries.toArray(int[][]::new));
                }
                case 29 -> {
                    JsonObject[] f=fields(value,"Weapon","item_damage_per_attack","disable_blocking_for_seconds");int damage=intNode(f[0]);float seconds=floating(f[1]);require(damage>=0&&Float.compare(seconds,0.0f)>=0);List<int[]> entries=new ArrayList<>();
                    if(damage!=1)put(entries,"item_damage_per_attack",scalar(8,damage,4));if(!same(seconds,0.0f))put(entries,"disable_blocking_for_seconds",floatHash(seconds));hash=map(entries.toArray(int[][]::new));
                }
                case 44 -> {int nutrition=intNode(fields(value,"VillagerFood","nutrition")[0]);require(nutrition>0);hash=map(pair("nutrition",scalar(8,nutrition,4)));}
                case 47 -> hash=scalar(8,intNode(fields(value,"DyedItemColor","rgb")[0]),4);
                case 48 -> hash=scalar(8,intNode(value),4);
                case 54 -> {float n=floating(value);require(Float.compare(n,0.0f)>=0);hash=floatHash(n);}
                case 65 -> {int n=intNode(fields(value,"OminousBottleAmplifier","value")[0]);require(n>=0&&n<=4);hash=scalar(8,n,4);}
                case 78 -> {
                    require(kind(value,"map"));JsonArray values=value.getAsJsonArray("entries");require(values.size()<=MAX_COLLECTION);List<int[]> entries=new ArrayList<>();Set<String> seen=new HashSet<>();
                    for(JsonElement item:values){JsonObject entry=item.getAsJsonObject();String key=stringNode(entry.getAsJsonObject("key"));require(seen.add(key));put(entries,key,string(stringNode(entry.getAsJsonObject("value"))));}hash=map(entries.toArray(int[][]::new));
                }
                default -> {return OptionalInt.empty();}
            }
            return OptionalInt.of(hash);
        }catch(RuntimeException unavailable){return OptionalInt.empty();}
    }

    private record Pending(JsonElement value,int depth) {}
    private static void inspect(JsonElement root){
        ArrayDeque<Pending> pending=new ArrayDeque<>();IdentityHashMap<JsonElement,Boolean> seen=new IdentityHashMap<>();pending.push(new Pending(root,0));int nodes=0;
        while(!pending.isEmpty()){
            Pending next=pending.pop();require(++nodes<=MAX_NODES&&next.depth<=MAX_DEPTH);JsonElement value=next.value;
            if(value.isJsonObject()||value.isJsonArray()){require(seen.put(value,true)==null);if(value.isJsonObject()){for(var entry:value.getAsJsonObject().entrySet())queue(pending,entry.getValue(),next.depth+1,nodes);}else for(JsonElement child:value.getAsJsonArray())queue(pending,child,next.depth+1,nodes);}
            else if(value.isJsonPrimitive()&&value.getAsJsonPrimitive().isString())require(value.getAsString().length()<=65535);
        }
    }
    private static void queue(ArrayDeque<Pending> pending,JsonElement value,int depth,int visited){require(depth<=MAX_DEPTH&&pending.size()<MAX_NODES-visited);pending.push(new Pending(value,depth));}
    private static JsonObject unwrap(JsonObject value){for(int depth=0;kind(value,"mapped");depth++){require(depth<32);value=value.getAsJsonObject("value");}return value;}
    private static boolean kind(JsonObject value,String kind){return value!=null&&value.has("kind")&&value.get("kind").getAsString().equals(kind);}
    private static boolean identifier(String value){return value!=null&&value.length()<=32767&&value.matches("[a-z0-9_.-]+:[a-z0-9/._-]+");}
    private static int enchantments(JsonObject value,Map<String,List<String>> context){
        require(context.size()<=256);for(String registry:context.keySet())require(identifier(registry));
        List<String> observed=context.get("minecraft:enchantment");require(observed!=null&&observed.size()<=MAX_COLLECTION);
        // shortcut: contexts over4096 stay unavailable; validate once per packet before supporting larger contexts.
        String[] names=new String[observed.size()];Set<String> unique=new HashSet<>();long utfBytes=0;
        for(int i=0;i<names.length;i++){String name=observed.get(i);require(identifier(name)&&unique.add(name));utfBytes+=name.length();require(utfBytes<=64L*1024*1024);names[i]=name;}
        require(kind(value,"record")&&text(value.get("type")).equals("net.minecraft.world.item.enchantment.ItemEnchantments"));
        JsonArray fields=value.getAsJsonArray("fields");require(fields.size()==1);JsonObject field=fields.get(0).getAsJsonObject();require(text(field.get("name")).equals("0"));
        JsonObject map=field.getAsJsonObject("value");require(kind(map,"map"));JsonArray entries=map.getAsJsonArray("entries");require(entries.size()<=MAX_COLLECTION);
        List<int[]> hashes=new ArrayList<>();Set<Integer> ids=new HashSet<>();
        for(JsonElement element:entries){JsonObject entry=element.getAsJsonObject(),key=entry.getAsJsonObject("key");require(kind(key,"registry")&&text(key.get("registry")).equals("minecraft:enchantment"));
            int id=integer(key.get("id")),level=intNode(entry.getAsJsonObject("value"));require(id>=0&&id<names.length&&ids.add(id)&&level>=1&&level<=255);put(hashes,names[id],scalar(8,level,4));}
        return map(hashes.toArray(int[][]::new));
    }
    private static int integer(JsonElement value){require(value!=null&&value.isJsonPrimitive()&&value.getAsJsonPrimitive().isNumber());return value.getAsBigDecimal().intValueExact();}
    private static long longInteger(JsonElement value){require(value.isJsonPrimitive()&&value.getAsJsonPrimitive().isNumber());return value.getAsBigDecimal().longValueExact();}
    private static String text(JsonElement value){require(value!=null&&value.isJsonPrimitive()&&value.getAsJsonPrimitive().isString());String text=value.getAsString();require(text.length()<=65535);return text;}
    private static int intNode(JsonObject value){value=unwrap(value);require(kind(value,"number")&&text(value.get("type")).equals("int32"));return integer(value.get("value"));}
    private static boolean bool(JsonObject value){value=unwrap(value);require(kind(value,"bool")&&value.get("value").isJsonPrimitive()&&value.get("value").getAsJsonPrimitive().isBoolean());return value.get("value").getAsBoolean();}
    private static String stringNode(JsonObject value){value=unwrap(value);require(kind(value,"string"));return text(value.get("value"));}
    private static float floating(JsonObject value){value=unwrap(value);require((kind(value,"number")||kind(value,"nbt"))&&text(value.get("type")).equals("float32")&&value.get("value").isJsonPrimitive()&&value.get("value").getAsJsonPrimitive().isNumber());String bits=text(value.get("bits"));require(bits.matches("[0-9a-f]{8}"));float number=value.get("value").getAsFloat();require(Float.isFinite(number)&&Float.floatToRawIntBits(number)==(int)Long.parseLong(bits,16));return number;}
    private static double doubling(JsonObject value){require(text(value.get("type")).equals("float64")&&value.get("value").isJsonPrimitive()&&value.get("value").getAsJsonPrimitive().isNumber());String bits=text(value.get("bits"));require(bits.matches("[0-9a-f]{16}"));double number=value.get("value").getAsDouble();require(Double.isFinite(number)&&Double.doubleToRawLongBits(number)==Long.parseUnsignedLong(bits,16));return number;}
    private static JsonArray list(JsonObject value){value=unwrap(value);require(kind(value,"list"));JsonArray values=value.getAsJsonArray("values");require(values.size()<=MAX_COLLECTION);return values;}
    private static JsonObject[] fields(JsonObject value,String owner,String... names){
        String expected=(owner.equals("FoodProperties")||owner.equals("VillagerFood")?"net.minecraft.world.food.":"net.minecraft.world.item.component.")+owner;
        require(kind(value,"record")&&text(value.get("type")).equals(expected));JsonArray fields=value.getAsJsonArray("fields");require(fields.size()==names.length);JsonObject[] output=new JsonObject[names.length];
        for(int i=0;i<names.length;i++){JsonObject field=fields.get(i).getAsJsonObject();require(text(field.get("name")).equals(names[i]));output[i]=field.getAsJsonObject("value");}return output;
    }
    private static int enumHash(int id,JsonObject value){
        require(kind(value,"enum"));int index=integer(value.get("id"));String[] names=switch(id){
            case 12 -> new String[]{"common","uncommon","rare","epic"};case 92 -> new String[]{"red","snow"};case 93 -> new String[]{"small","medium","large"};
            case 94 -> new String[]{"red_blue","blue","green","yellow_blue","gray"};case 95 -> new String[]{"kob","sunstreak","snooper","dasher","brinely","spotty","flopper","stripey","glitter","blockfish","betty","clayfish"};
            case 98 -> new String[]{"red","brown"};case 99 -> new String[]{"brown","white","black","white_splotched","gold","salt","evil"};
            case 108 -> new String[]{"white","creamy","chestnut","brown","black","gray","dark_brown"};case 110 -> new String[]{"creamy","white","brown","gray"};case 111 -> new String[]{"lucy","wild","gold","cyan","blue"};default -> DYES;
        };
        if(id==95){require(index>=0&&(index&255)<=1&&(index>>>8)<=5);index=(index&255)*6+(index>>>8);}if(id==99&&index==99)index=6;
        require(index>=0&&index<names.length&&text(value.get("name")).equals(names[index]));return string(names[index]);
    }
    private static int nbt(JsonObject value,int depth){
        require(depth<=32&&kind(value,"nbt"));int tag=integer(value.get("tag"));JsonElement data=value.get("value");
        switch(tag){
            case 0: return scalar(1,0,0);
            case 1: {int n=integer(data);require(n>=-128&&n<=127);return scalar(6,n,1);}
            case 2: {int n=integer(data);require(n>=-32768&&n<=32767);return scalar(7,n,2);}
            case 3: return scalar(8,integer(data),4);
            case 4: return scalar(9,longInteger(data),8);
            case 5: return floatHash(floating(value));
            case 6: return scalar(11,Double.doubleToRawLongBits(doubling(value)),8);
            case 8: return string(text(data));
            case 7,11,12: {
                JsonArray values=data.getAsJsonArray();require(values.size()<=MAX_COLLECTION);long[] numbers=new long[values.size()];for(int i=0;i<numbers.length;i++){numbers[i]=longInteger(values.get(i));if(tag==7)require(numbers[i]>=-128&&numbers[i]<=127);if(tag==11)require(numbers[i]>=Integer.MIN_VALUE&&numbers[i]<=Integer.MAX_VALUE);}return array(tag==7?14:tag==11?16:18,tag==7?1:tag==11?4:8,numbers);
            }
            case 9: {
                JsonArray values=data.getAsJsonArray();require(values.size()<=MAX_COLLECTION);int element=integer(value.get("element_tag"));require(element>=0&&element<=12&&(element!=0||values.isEmpty()));int[] hashes=new int[values.size()];for(int i=0;i<hashes.length;i++){JsonObject child=values.get(i).getAsJsonObject();require(integer(child.get("tag"))==element);hashes[i]=nbt(child,depth+1);}return list(hashes);
            }
            case 10: {
                JsonArray values=data.getAsJsonArray();require(values.size()<=MAX_COLLECTION);List<int[]> entries=new ArrayList<>();Set<String> names=new HashSet<>();for(JsonElement element:values){JsonObject field=element.getAsJsonObject();String name=text(field.get("name"));require(names.add(name));JsonObject child=field.getAsJsonObject("value");require(integer(child.get("tag"))!=0);put(entries,name,nbt(child,depth+1));}return map(entries.toArray(int[][]::new));
            }
            default: throw new Unavailable();
        }
    }
    private static int[] pair(String key,int value){return new int[]{string(key),value};}
    private static void put(List<int[]> entries,String key,int value){entries.add(pair(key,value));}
    private static boolean same(float left,float right){return Float.floatToIntBits(left)==Float.floatToIntBits(right);}
    private static int floatHash(float value){return scalar(10,Float.floatToRawIntBits(value),4);}
    private static void little(CRC32C crc,long value,int width){for(int i=0;i<width;i++)crc.update((int)(value>>>(8*i))&255);}
    static int scalar(int tag,long value,int width){CRC32C crc=new CRC32C();crc.update(tag);little(crc,value,width);return (int)crc.getValue();}
    static int string(String value){CRC32C crc=new CRC32C();crc.update(12);little(crc,value.length(),4);for(int i=0;i<value.length();i++)little(crc,value.charAt(i),2);return (int)crc.getValue();}
    static int list(int... values){CRC32C crc=new CRC32C();crc.update(4);for(int value:values)little(crc,value,4);crc.update(5);return (int)crc.getValue();}
    static int map(int[]... pairs){Arrays.sort(pairs,Comparator.<int[]>comparingLong(pair->Integer.toUnsignedLong(pair[0])).thenComparingLong(pair->Integer.toUnsignedLong(pair[1])));CRC32C crc=new CRC32C();crc.update(2);for(int[] pair:pairs){little(crc,pair[0],4);little(crc,pair[1],4);}crc.update(3);return (int)crc.getValue();}
    static int array(int tag,int width,long[] values){CRC32C crc=new CRC32C();crc.update(tag);for(long value:values)little(crc,value,width);crc.update(tag+1);return (int)crc.getValue();}
}
