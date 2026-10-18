package dev.evelyn.minecraft;

import com.google.gson.*;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;

/** Exact vanilla raw mining reference; no target, geometry, effect or gameplay authority. */
final class MiningReference {
    private static final String RESOURCE="/dev/evelyn/minecraft/mining-reference-26.3.json";
    private static final String SHA256="b23af68b2404d110d6ad09f67a6bae9254b8d33551059041a53093cac3b81719";
    private static final String METADATA="95cb1630b09e6b3b49b5a3469cd02b97cbc8d59182d04127d92bf359d5b41d1f";
    private static final String CATALOG="36af92ca085999788941572aa22a9e4b1b7fd0408b22d5f784e216e25efa2a8f";
    record Block(int id,String name,float hardness,boolean requiresCorrectToolForDrops){String origin(){return "VANILLA_REFERENCE";}}
    private final Block[] blocks,states;
    private final List<String> keys;
    private MiningReference(Block[] blocks,Block[] states,List<String> keys){this.blocks=blocks;this.states=states;this.keys=List.copyOf(keys);}
    List<String> blockKeys(){return keys;}
    Block block(int id){if(id<0||id>=blocks.length)throw new IllegalArgumentException("unknown_block_id");return blocks[id];}
    Block state(int id){if(id<0||id>=states.length)throw new IllegalArgumentException("unknown_block_state");return states[id];}
    static MiningReference loadPinned(BlockCatalog catalog)throws IOException{
        byte[] bytes;
        try(var input=MiningReference.class.getResourceAsStream(RESOURCE)){
            if(input==null)throw failure();bytes=input.readNBytes(131073);
        }
        return read(bytes,SHA256,catalog);
    }
    static MiningReference read(byte[] bytes,String expectedSha256,BlockCatalog catalog)throws IOException{
        try{
            if(bytes==null||bytes.length>131072||!HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)).equals(expectedSha256))throw failure();
            // Only the exact, code-owned digest above reaches this parser in production.
            JsonObject data=JsonParser.parseString(new String(bytes,StandardCharsets.UTF_8)).getAsJsonObject();
            if(!data.keySet().equals(Set.of("schema","game_version","protocol","origin","source_metadata_sha256","block_catalog_sha256","rows"))
                ||!text(data.get("schema")).equals("evelyn.minecraft.mining-reference.v1")||!text(data.get("game_version")).equals("26.3")
                ||integer(data.get("protocol"))!=777||!text(data.get("origin")).equals("VANILLA_REFERENCE")
                ||!text(data.get("source_metadata_sha256")).equals(METADATA)||!text(data.get("block_catalog_sha256")).equals(CATALOG))throw failure();
            JsonArray rows=data.getAsJsonArray("rows");if(rows.size()!=1286||catalog.blockCount()!=1286||catalog.stateCount()!=35723)throw failure();
            Block[] blocks=new Block[1286],states=new Block[35723];Map<String,Block> names=new HashMap<>();List<String> keys=new ArrayList<>();
            for(int id=0;id<blocks.length;id++){
                JsonArray row=rows.get(id).getAsJsonArray();if(row.size()!=4||integer(row.get(0))!=id)throw failure();
                String name=text(row.get(1));JsonPrimitive hardness=row.get(2).getAsJsonPrimitive(),required=row.get(3).getAsJsonPrimitive();
                if(!name.matches("[a-z0-9_.-]+:[a-z0-9_./-]+")||!hardness.isNumber()||!required.isBoolean())throw failure();
                float value=hardness.getAsFloat();if(!Float.isFinite(value))throw failure();
                Block block=new Block(id,name,value,required.getAsBoolean());if(names.put(name,block)!=null)throw failure();blocks[id]=block;keys.add(name);
            }
            Set<String> observed=new HashSet<>();
            for(int id=0;id<states.length;id++){
                Block block=names.get(catalog.lookup(id).name());if(block==null)throw failure();states[id]=block;observed.add(block.name());
            }
            if(observed.size()!=blocks.length)throw failure();return new MiningReference(blocks,states,keys);
        }catch(IOException expected){throw expected;}catch(Exception malformed){throw failure();}
    }
    private static String text(JsonElement value)throws IOException{if(value==null||!value.isJsonPrimitive()||!value.getAsJsonPrimitive().isString())throw failure();return value.getAsString();}
    private static int integer(JsonElement value)throws IOException{
        if(value==null||!value.isJsonPrimitive()||!value.getAsJsonPrimitive().isNumber())throw failure();
        try{return value.getAsBigDecimal().intValueExact();}catch(ArithmeticException bad){throw failure();}
    }
    private static IOException failure(){return new IOException("invalid_mining_reference");}
}
