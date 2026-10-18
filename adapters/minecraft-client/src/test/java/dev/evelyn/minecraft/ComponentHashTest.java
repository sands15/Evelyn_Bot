package dev.evelyn.minecraft;

import com.google.gson.*;
import java.util.*;

/** Constants are independent official HashOps and component codec oracles; no runtime fixture files. */
public final class ComponentHashTest {
    private static int checks;
    private static final String BASE="0:0a00:-982207288,1:01:1565579036,2:01:1565579036,3:00:-2146075228,4::-982207288,5:00013e4ccccd:-982207288,6:080000:1615905556,7:00000000:-264789252,9:080000:1615905556,10:0a6d696e6563726166743a:-958944429,11:00:-1978007022,12:00:75150990,17:00000000:-982207288,18:0000:-982207288,19:00:-2146075228,21:00:828198337,22:0a00:-982207288,23:044019999a00:1470844630,28:003f8000000101:937295970,29:0100000000:-982207288,34::-982207288,35:0a6d696e6563726166743a:-958944429,44:04:-964794316,46:00:1286006417,47:00000000:-2146075228,48:00:-2146075228,54:00000000:-264789252,61:0a00:-982207288,65:00:-2146075228,73:0a6d696e6563726166743a:-958944429,75:00:1286006417,78:00:-982207288,91:00:1286006417,92:00:-1939582294,93:00:-159711913,94:00:1784290859,95:00:-474873657,96:00:1286006417,97:00:1286006417,98:00:-1939582294,99:00:-541737949,108:00:1286006417,110:00:1294579017,111:00:1962468702,114:00:1286006417,115:00:1286006417,116:00:1286006417,120::-982207288,121:00:1286006417";
    private static final String EXTRA="1:63:-1632321551,2:8001:-759843685,3:07:-1726626450,5:01003f400000:432073642,6:08000178:-1651626295,7:3f000000:-1631070359,9:080003eda080:-423001966,10:0e6d696e6563726166743a74657374:-32915978,11:020800017808000179:1388650676,12:03:-292715907,17:013fa00000020100010178020011223300ffffff:-2040724875,18:0100:14016722,19:07:-1726626450,21:01:-1019818302,23:063fc0000001:-119906037,28:00400000000200:1483096218,29:023f000000:-2004522911,44:09:1425837784,47:00112233:1580896708,48:07:-1726626450,54:3fc00000:-1709970540,65:04:-80262057,78:0104617869730178:-1019276418,95:810a:1395160473,99:63:1619861300,108:06:-337512376,110:03:-727074367,111:04:199633461,46:0e:-1939582294,92:01:-1511535024,93:02:-886537313,94:04:-727074367,98:01:-541737949,0:0a010001620100:892175700,61:0a030001780000000100:-1621257062,0:0a080003737472000441c0804201000162ff0200017301000a000163000600016440020000000000000c00026c61000000020000000000000001ffffffffffffffff050001663fc000000b000269610000000200000001ffffffff03000169000000010400016c00000000000000010900026c69030000000200000001ffffffff0700026261000000030102ff00:2043856745";
    public static void main(String[] args)throws Exception {
        core();vectors(BASE,49);vectors(EXTRA,36);unsupportedAndBounds();numericCanonicalBoundaries();enchantmentContexts();System.out.println("COMPONENT_HASH_PASS "+checks);
    }
    private static void core(){
        eq(ComponentHash.scalar(1,0,0),-1609117614,"empty");eq(ComponentHash.map(),-982207288,"empty map");eq(ComponentHash.list(),-1978007022,"empty list");
        eq(ComponentHash.scalar(13,0,1),828198337,"false");eq(ComponentHash.scalar(13,1,1),-1019818302,"true");
        eq(ComponentHash.scalar(6,1,1),1791337955,"byte width");eq(ComponentHash.scalar(7,1,2),121003876,"short width");eq(ComponentHash.scalar(8,1,4),1565579036,"int width");
        eq(ComponentHash.scalar(8,-1,4),932039068,"negative int");eq(ComponentHash.scalar(9,1,8),-1097584293,"long width");
        eq(ComponentHash.scalar(10,Float.floatToRawIntBits(2.4f),4),-1806857447,"float32");eq(ComponentHash.scalar(10,0x80000000L,4),1925290372,"negative zero");
        eq(ComponentHash.scalar(11,Double.doubleToRawLongBits(2.4),8),-1362876604,"float64");
        eq(ComponentHash.string(""),1615905556,"empty string");eq(ComponentHash.string("A\uD83D\uDE00"),-55927684,"UTF16 surrogate pair");eq(ComponentHash.string("\uD800"),-423001966,"unpaired UTF16 code unit");
        eq(ComponentHash.list(ComponentHash.scalar(8,1,4),ComponentHash.scalar(8,2,4)),-1373960668,"ordered list");
        eq(ComponentHash.map(new int[]{ComponentHash.string("b"),ComponentHash.scalar(8,2,4)},new int[]{ComponentHash.string("a"),ComponentHash.scalar(8,1,4)}),837818764,"unsigned digest map ordering");
        eq(ComponentHash.array(14,1,new long[]{1,2,-1}),2141781086,"byte array distinct from list");
        eq(ComponentHash.array(16,4,new long[]{1,-1}),-1024574055,"int array");eq(ComponentHash.array(18,8,new long[]{1,-1}),-2095440496,"long array");
    }
    private static void vectors(String source,int expected)throws Exception {
        Set<Integer> types=new HashSet<>();int count=0;
        for(String row:source.split(",")){
            String[] columns=row.split(":",-1);int id=Integer.parseInt(columns[0]),expectedHash=Integer.parseInt(columns[2]);JsonObject wire=decode(id,columns[1]),saved=wire.deepCopy();
            var result=ComponentHash.component(id,wire);check(result.isPresent(),"supported literal missing id="+id);eq(result.getAsInt(),expectedHash,"official persistent hash id="+id);check(wire.equals(saved),"hash cannot mutate observed typed value");types.add(id);count++;
        }
        check(count==expected,"all independent literal values checked");if(source==BASE)check(types.size()==49,"exact initial supported type count");
    }
    private static JsonObject decode(int id,String hex)throws Exception{return ComponentWireCodec.decode(id,HexFormat.of().parseHex(hex),0).value();}
    private static void unsupportedAndBounds()throws Exception {
        check(ComponentHash.component(24,decode(24,"3fcccccd01bf050100")).isEmpty(),"valid consumable needs unimplemented holder/default canonicalization");
        check(ComponentHash.component(20,decode(20,"")).isEmpty(),"transient type has no persistent hash");
        check(ComponentHash.component(13,decode(13,"00")).isEmpty(),"unsupported enchantment normalization is explicit");
        check(ComponentHash.component(1,decode(1,"00")).isEmpty(),"wire-known invalid persistent max stack size is unhashable");
        check(ComponentHash.component(3,decode(3,"ffffffff0f")).isEmpty(),"negative damage remains observed but unhashable");
        check(ComponentHash.component(6,decode(6,"0a0800047465787400017800")).isEmpty(),"component compound cannot be hashed as raw NBT");
        check(ComponentHash.component(28,decode(28,"010100003f8000000101")).isEmpty(),"nonempty tool rules remain explicitly unsupported");
        check(ComponentHash.component(-1,new JsonObject()).isEmpty()&&ComponentHash.component(122,new JsonObject()).isEmpty()&&ComponentHash.component(1,null).isEmpty(),"invalid root input returns empty");
        check(ComponentHash.component(3,decode(1,"01")).isEmpty(),"root component identity must agree");
        JsonObject invalid=decode(23,"044019999a00");invalid.getAsJsonArray("fields").get(1).getAsJsonObject().getAsJsonObject("value").addProperty("bits","3f800000");
        check(ComponentHash.component(23,invalid).isEmpty(),"typed float bits and value must agree");
        JsonObject cycle=new JsonObject();cycle.add("cycle",cycle);check(ComponentHash.component(0,cycle).isEmpty(),"cyclic graphs fail closed without recursion overflow");
        JsonObject oversized=decode(0,"0a00");JsonArray values=oversized.getAsJsonArray("value");for(int i=0;i<65537;i++)values.add(new JsonObject());check(ComponentHash.component(0,oversized).isEmpty(),"bounded input graph prevents oversized normalization");
        check(ComponentHash.component(1,new JsonObject()).isEmpty(),"malformed typed value never invents a hash");
    }
    private static void numericCanonicalBoundaries()throws Exception {
        // Official persistent encode rejects each value although its wire syntax is valid.
        for(String row:"44:00,5:000180000000,7:80000000,29:0180000000,54:80000000".split(",")){
            String[] parts=row.split(":");int id=Integer.parseInt(parts[0]);
            check(ComponentHash.component(id,decode(id,parts[1])).isEmpty(),"official persistent validator rejects id="+id+" wire="+parts[1]);
        }
        // Adjacent Codec.FLOAT fields accept negative zero; range predicates must remain local.
        vectors("23:008000000000:-1201875276,28:00800000000101:-1758111897,17:0180000000000000:-111568290,29:017f7fffff:1054875721,54:7f7fffff:1050335045,7:00000001:762447940,44:01:-326140849,2:ffffffff07:-1250168092",8);
    }
    private static void enchantmentContexts()throws Exception {
        String[] contexts={"test:alpha,test:beta","test:gamma,test:beta","test:beta,test:alpha"};
        int[][] expected={{-982207288,-1587611718,-1027238536,705174259,705174259},{-982207288,-1315823333,-771144743,1938577862,1938577862},{-982207288,2054193173,434889431,-589251182,-589251182}};
        String[] wires={"00","010001","0100ff01","0200030105","0201050003"};
        for(int id:new int[]{13,45})for(int context=0;context<contexts.length;context++)for(int vector=0;vector<wires.length;vector++){
            Map<String,List<String>> keys=Map.of("minecraft:enchantment",List.of(contexts[context].split(",")));JsonObject value=enchantment(id,wires[vector]),saved=value.deepCopy();
            OptionalInt result=ComponentHash.component(id,value,keys);check(result.isPresent(),"observed enchantment keys are hashable id="+id);eq(result.getAsInt(),expected[context][vector],"official enchantment persistent hash");check(value.equals(saved),"context hashing cannot mutate observed wire");
        }
        JsonObject value=enchantment(13,"010001");Map<String,List<String>> mutable=new HashMap<>();List<String> names=new ArrayList<>(List.of("test:alpha","test:beta"));mutable.put("minecraft:enchantment",names);
        eq(ComponentHash.component(13,value,mutable).orElseThrow(),-1587611718,"first current name");names.set(0,"test:gamma");eq(ComponentHash.component(13,value,mutable).orElseThrow(),-1315823333,"no cache of previous name");
        for(Map<String,List<String>> keys:List.of(Map.<String,List<String>>of(),Map.of("minecraft:other",List.of("test:alpha")),Map.of("minecraft:enchantment",List.<String>of()),Map.of("minecraft:enchantment",List.of("test:dup","test:dup")),Map.of("minecraft:enchantment",List.of("Bad:key")),Map.of("Bad:registry",List.of("test:alpha"))))check(ComponentHash.component(13,value,keys).isEmpty(),"missing, duplicate or invalid registry context unavailable");
        check(ComponentHash.component(13,value,null).isEmpty(),"null context unavailable");check(ComponentHash.component(45,enchantment(45,"00")).isEmpty(),"old overload still requires explicit context even for empty enchantments");
        check(ComponentHash.component(13,enchantment(13,"010101"),Map.of("minecraft:enchantment",List.of("test:alpha"))).isEmpty(),"ID cannot exceed current registry size");
        for(int id:new int[]{13,45})for(String wire:new String[]{"010000","01008002"})check(ComponentHash.component(id,enchantment(id,wire),Map.of("minecraft:enchantment",List.of("test:alpha","test:beta"))).isEmpty(),"persistent levels1..255 required");
        JsonObject wrong=enchantment(13,"010001");wrong.getAsJsonArray("fields").get(0).getAsJsonObject().getAsJsonObject("value").getAsJsonArray("entries").get(0).getAsJsonObject().getAsJsonObject("key").addProperty("registry","minecraft:other");check(ComponentHash.component(13,wrong,mutable).isEmpty(),"holder registry identity required");
        JsonObject duplicate=enchantment(13,"010001");JsonArray entries=duplicate.getAsJsonArray("fields").get(0).getAsJsonObject().getAsJsonObject("value").getAsJsonArray("entries");entries.add(entries.get(0).deepCopy());check(ComponentHash.component(13,duplicate,mutable).isEmpty(),"duplicate observed holder cannot produce a hash");
        List<String> boundary=new ArrayList<>();boundary.add("test:alpha");for(int i=1;i<4096;i++)boundary.add("test:unused_"+i);
        eq(ComponentHash.component(13,value,Map.of("minecraft:enchantment",boundary)).orElseThrow(),-1587611718,"4096 name availability boundary preserves referenced key");
        boundary.add("test:too_many");check(ComponentHash.component(13,value,Map.of("minecraft:enchantment",boundary)).isEmpty(),"larger official-valid registry remains conservatively unavailable");
        Map<String,List<String>> manyRegistries=new HashMap<>();manyRegistries.put("minecraft:enchantment",List.of("test:alpha"));for(int i=0;i<256;i++)manyRegistries.put("test:registry_"+i,List.of());check(ComponentHash.component(13,value,manyRegistries).isEmpty(),"registry-map count bounded before traversal");
        Map<String,List<String>> nullList=new HashMap<>();nullList.put("minecraft:enchantment",null);check(ComponentHash.component(13,value,nullList).isEmpty(),"null registry list unavailable");
        check(ComponentHash.component(13,value,Map.of("minecraft:enchantment",Arrays.asList((String)null))).isEmpty(),"null registry key unavailable");
        check(ComponentHash.component(13,value,Map.of("minecraft:enchantment",List.of("test:"+"x".repeat(32768)))).isEmpty(),"registry key length bounded");
        eq(ComponentHash.component(13,enchantment(13,"00"),Map.of("minecraft:enchantment",List.of())).orElseThrow(),-982207288,"explicit known empty registry has canonical empty map");
    }
    private static JsonObject enchantment(int id,String wire)throws Exception {return ComponentWireCodec.decode(id,HexFormat.of().parseHex(wire),0,Map.of("minecraft:enchantment",2)).value();}
    private static void eq(int actual,int expected,String message){check(actual==expected,message+" expected="+expected+" actual="+actual);}
    private static void check(boolean condition,String message){if(!condition)throw new AssertionError(message);checks++;}
}
