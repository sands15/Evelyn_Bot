package dev.evelyn.minecraft;

import com.google.gson.*;
import java.io.*;
import java.nio.*;
import java.nio.charset.*;
import java.security.*;
import java.util.*;

/** Pinned 26.3 wire structure only; effective item semantics and prediction hashes stay unknown. */
final class ComponentWireCodec {
    private static final int MAX_BYTES=1048576,MAX_DEPTH=32,MAX_NODES=65536,MAX_COLLECTION=4096;
    private static final String RESOURCE_SHA256="9059ff44946a7adcdc0e15fa54d9c804787ca7a0c345e54ef8ef57caca7b7e86";
    private static JsonObject pinned;
    /** Conservatively counts output containers and raw collection entries, including zero-wire units. */
    record Decoded(JsonObject value,int start,int end,int nodeCount){int consumed(){return end-start;}}

    /** One packet cursor/budget shared with trusted in-package outer grammars. Discard after any IOException. */
    static final class DecodeSession {
        private final Reader reader;
        private DecodeSession(Reader reader){this.reader=reader;}
        int position(){return reader.input.position();}
        int remaining(){return reader.input.remaining();}
        int nodesUsed(){return reader.nodes;}
        int vi()throws IOException{return reader.vi();}
        int u8()throws IOException{return reader.u8();}
        boolean bool()throws IOException{return reader.bool();}
        int count(int maximum)throws IOException{return reader.count(maximum);}
        String identifier()throws IOException{return reader.identifier();}
        /** Reserve output containers/raw entries before the outer grammar allocates them. */
        void reserve(int count)throws IOException{reader.reserve(count);}
        void checkDepth(int depth)throws IOException{if(depth<0)throw failure("component_structure_budget");reader.budget(depth);}
        Decoded readComponent(int id,int depth)throws IOException {
            checkDepth(depth);int start=position(),before=nodesUsed();
            JsonObject value=reader.read(reader.component(id),depth,null);
            value.addProperty("component_id",id);value.addProperty("syntax_known",true);value.addProperty("effective_known",false);
            return new Decoded(value,start,position(),nodesUsed()-before);
        }
        Decoded readPatch(int depth)throws IOException {
            checkDepth(depth);int start=position(),before=nodesUsed();
            JsonObject value=reader.patch(depth,null);return new Decoded(value,start,position(),nodesUsed()-before);
        }
        /** Fixed, trusted in-package schemas only; never schema input from a peer or public API. */
        Decoded readTrustedSchema(JsonObject schema,int depth)throws IOException {
            checkDepth(depth);if(schema==null)throw failure("component_trusted_schema");int start=position(),before=nodesUsed();
            try {
                JsonObject value=reader.read(schema,depth,null);return new Decoded(value,start,position(),nodesUsed()-before);
            }catch(RuntimeException malformed){throw failure("component_trusted_schema");}
        }
    }

    static DecodeSession open(byte[] bytes,int offset,Map<String,Integer> sessionRegistrySizes)throws IOException {
        if(bytes==null||bytes.length>MAX_BYTES||offset<0||offset>bytes.length)throw failure("component_input_budget");
        return new DecodeSession(new Reader(bytes.clone(),offset,tables(),sessionRegistrySizes));
    }

    static Decoded decode(int id,byte[] bytes,int offset)throws IOException{return decode(id,bytes,offset,Map.of());}
    static Decoded decode(int id,byte[] bytes,int offset,Map<String,Integer> sessionRegistrySizes)throws IOException {
        if(bytes==null||bytes.length>MAX_BYTES||offset<0||offset>bytes.length)throw failure("component_input_budget");
        JsonObject tables=tables();Reader reader=new Reader(bytes,offset,tables,sessionRegistrySizes);
        JsonObject value=reader.read(reader.component(id),0,null);
        value.addProperty("component_id",id);value.addProperty("syntax_known",true);value.addProperty("effective_known",false);
        return new Decoded(value,offset,reader.input.position(),reader.nodes);
    }
    private static synchronized JsonObject tables()throws IOException {
        if(pinned==null){
            try(InputStream input=ComponentWireCodec.class.getResourceAsStream("/dev/evelyn/minecraft/component-wire-26.3.json")){
                if(input==null)throw failure("component_schema_missing");byte[] bytes=input.readNBytes(262145);
                if(bytes.length>262144||!HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)).equals(RESOURCE_SHA256))throw failure("component_schema_pin");
                JsonObject parsed=JsonParser.parseString(new String(bytes,StandardCharsets.UTF_8)).getAsJsonObject();
                if(!parsed.get("version").getAsString().equals("26.3")||parsed.get("protocol").getAsInt()!=777||parsed.getAsJsonArray("components").size()!=122)throw failure("component_schema_identity");
                pinned=parsed;
            }catch(NoSuchAlgorithmException|RuntimeException malformed){throw failure("component_schema_invalid");}
        }
        return pinned;
    }
    private static final class Reader {
        final ByteBuffer input;final JsonObject tables;final Map<String,Integer> registries=new HashMap<>();int nodes;
        Reader(byte[] bytes,int offset,JsonObject tables,Map<String,Integer> supplied)throws IOException {
            input=ByteBuffer.wrap(bytes).order(ByteOrder.BIG_ENDIAN);input.position(offset);this.tables=tables;
            for(var entry:tables.getAsJsonObject("registry_sizes").entrySet())registries.put(entry.getKey(),entry.getValue().getAsInt());
            if(supplied==null)throw failure("component_registry_context");
            for(var entry:supplied.entrySet()){
                Integer count=entry.getValue(),old=registries.get(entry.getKey());
                if(entry.getKey()==null||count==null||count<0||count>1048576||old!=null&&!old.equals(count))throw failure("component_registry_context");
                registries.put(entry.getKey(),count);
            }
        }
        JsonObject component(int id)throws IOException {
            if(id<0||id>=122)throw failure("component_type_id");return tables.getAsJsonArray("components").get(id).getAsJsonObject().getAsJsonObject("schema");
        }
        void budget(int depth)throws IOException{if(depth>MAX_DEPTH)throw failure("component_structure_budget");}
        void reserve(int count)throws IOException{if(count<0||count>MAX_NODES-nodes)throw failure("component_structure_budget");nodes+=count;}
        JsonObject object()throws IOException{reserve(1);return new JsonObject();}
        JsonArray array()throws IOException{reserve(1);return new JsonArray();}
        JsonObject node(String kind)throws IOException{JsonObject out=object();out.addProperty("kind",kind);return out;}
        void need(int count)throws IOException{if(count<0||count>input.remaining())throw failure("component_truncated");}
        int u8()throws IOException{need(1);return input.get()&255;}
        int vi()throws IOException {
            int value=0;for(int i=0;i<5;i++){int b=u8();if(i==4&&(b&240)!=0)throw failure("component_varint");value|=(b&127)<<(i*7);if((b&128)==0)return value;}throw failure("component_varint");
        }
        int count(int maximum)throws IOException{int n=vi();if(n<0||n>Math.min(maximum,MAX_COLLECTION)||n>MAX_NODES-nodes)throw failure("component_collection_budget");return n;}
        boolean bool()throws IOException{int value=u8();if(value>1)throw failure("component_boolean");return value!=0;}
        String utf8(int maxChars)throws IOException {
            int length=vi();if(length<0||length>maxChars*3)throw failure("component_string_budget");need(length);byte[] bytes=new byte[length];input.get(bytes);
            try{String value=StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT).onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString();if(value.length()>maxChars)throw failure("component_string_budget");return value;}
            catch(CharacterCodingException invalid){throw failure("component_utf8");}
        }
        String identifier()throws IOException{String value=utf8(32767);if(!value.matches("[a-z0-9_.-]+:[a-z0-9/._-]*"))throw failure("component_identifier");return value;}
        String modifiedUtf()throws IOException {
            need(2);int length=input.getShort()&65535;need(length);byte[] encoded=new byte[length+2];encoded[0]=(byte)(length>>>8);encoded[1]=(byte)length;input.get(encoded,2,length);
            try{return new DataInputStream(new ByteArrayInputStream(encoded)).readUTF();}catch(UTFDataFormatException invalid){throw failure("component_nbt_utf");}
        }
        JsonObject registry(String key,int id)throws IOException {
            Integer size=registries.get(key);if(size==null)throw failure("component_registry_unavailable");if(id<0||id>=size)throw failure("component_registry_id");
            JsonObject out=node("registry");out.addProperty("registry",key);out.addProperty("id",id);return out;
        }
        JsonObject number(String type,Number value)throws IOException{JsonObject out=node("number");out.addProperty("type",type);out.addProperty("value",value);return out;}
        JsonObject read(JsonObject schema,int depth,JsonObject recursive)throws IOException {
            budget(depth);String kind=str(schema,"kind");
            switch(kind){
                case "primitive" -> {return primitive(schema);}
                case "NBT" -> {return nbt(u8(),depth+1);}
                case "mapped" -> {
                    JsonObject value=read(schema.getAsJsonObject("wire"),depth+1,recursive);String owner=str(schema,"semantic_owner");
                    if(owner.equals("net.minecraft.resources.Identifier")){
                        String id=value.get("value").getAsString();if(!id.matches("[a-z0-9_.-]+:[a-z0-9/._-]*"))throw failure("component_identifier");
                    }
                    JsonObject out=node("mapped");out.addProperty("type",owner);out.add("value",value);return out;
                }
                case "optional" -> {
                    JsonObject out=node("optional");boolean present=bool();out.addProperty("present",present);out.add("value",present?read(schema.getAsJsonObject("value"),depth+1,recursive):JsonNull.INSTANCE);return out;
                }
                case "list" -> {
                    int size=str(schema,"count").equals("fixed")?schema.get("size").getAsInt():count(schema.get("official_max").getAsInt());
                    if(size<0||size>MAX_COLLECTION)throw failure("component_collection_budget");JsonArray values=array();for(int i=0;i<size;i++)values.add(read(schema.getAsJsonObject("element"),depth+1,recursive));
                    JsonObject out=node("list");out.add("values",values);return out;
                }
                case "registry-holder" -> {return registry(str(schema,"registry"),vi());}
                case "union" -> {return union(schema,depth,recursive);}
                case "recursive" -> {JsonObject definition=schema.getAsJsonObject("definition");return read(definition,depth+1,definition);}
                case "recursive_reference" -> {if(recursive==null)throw failure("component_recursive_schema");return read(recursive,depth+1,recursive);}
                case "nested-stack" -> {if(str(schema,"form").equals("component_patch"))return patch(depth,recursive);return record(schema,depth,recursive);}
                case "compound" -> {
                    String form=str(schema,"form");if(form.equals("ordered_fields"))return record(schema,depth,recursive);
                    if(form.equals("typed_component")){int id=vi();JsonObject out=node("typed_component");out.addProperty("id",id);out.add("value",read(component(id),depth+1,recursive));return out;}
                    if(form.equals("profile_properties"))return properties();
                    if(!form.equals("map"))throw failure("component_schema_form");
                    int size=count(schema.get("official_max").getAsInt());JsonArray entries=array();Set<String> keys=new HashSet<>();
                    for(int i=0;i<size;i++){JsonObject key=read(schema.getAsJsonObject("key"),depth+1,recursive);if(!keys.add(key.toString()))throw failure("component_duplicate_key");JsonObject pair=object();pair.add("key",key);pair.add("value",read(schema.getAsJsonObject("value"),depth+1,recursive));entries.add(pair);}
                    JsonObject out=node("map");out.add("entries",entries);return out;
                }
                default -> throw failure("component_schema_kind");
            }
        }
        JsonObject primitive(JsonObject schema)throws IOException {
            String type=str(schema,"type");switch(type){
                case "unit" -> {return node("unit");}
                case "bool" -> {JsonObject out=node("bool");out.addProperty("value",bool());return out;}
                case "varint" -> {return number("int32",vi());}
                case "int32_be" -> {need(4);return number("int32",input.getInt());}
                case "float32_be" -> {need(4);int bits=input.getInt();float value=Float.intBitsToFloat(bits);if(!Float.isFinite(value))throw failure("component_nonfinite");JsonObject out=number("float32",value);out.addProperty("bits",String.format(Locale.ROOT,"%08x",bits));return out;}
                case "float64_be" -> {need(8);long bits=input.getLong();double value=Double.longBitsToDouble(bits);if(!Double.isFinite(value))throw failure("component_nonfinite");JsonObject out=number("float64",value);out.addProperty("bits",String.format(Locale.ROOT,"%016x",bits));return out;}
                case "utf8_string" -> {JsonObject out=node("string");out.addProperty("value",utf8(schema.get("max_chars").getAsInt()));return out;}
                case "enum_id_varint" -> {
                    int id=vi();String owner=str(schema,"semantic_owner");JsonArray entries=tables.getAsJsonObject("enum_tables").getAsJsonArray(owner);
                    if(entries!=null)for(JsonElement entry:entries){JsonObject row=entry.getAsJsonObject();if(row.get("id").getAsInt()==id){JsonObject out=node("enum");out.addProperty("type",owner);out.addProperty("id",id);out.addProperty("name",str(row,"name"));return out;}}
                    throw failure("component_enum_id");
                }
                case "packed_block_pos_int64_be" -> {need(8);return number("packed_block_pos",input.getLong());}
                case "uuid_128_bits" -> {need(16);JsonObject out=node("uuid");out.addProperty("value",new UUID(input.getLong(),input.getLong()).toString());return out;}
                default -> throw failure("component_primitive_schema");
            }
        }
        JsonObject record(JsonObject schema,int depth,JsonObject recursive)throws IOException {
            JsonObject out=node("record");out.addProperty("type",str(schema,"semantic_owner"));JsonArray fields=array(),types=schema.getAsJsonArray("fields"),names=schema.getAsJsonArray("field_names");
            for(int i=0;i<types.size();i++){JsonObject field=object();field.addProperty("name",names==null?Integer.toString(i):names.get(i).getAsString());field.add("value",read(types.get(i).getAsJsonObject(),depth+1,recursive));fields.add(field);}
            out.add("fields",fields);return out;
        }
        JsonObject union(JsonObject schema,int depth,JsonObject recursive)throws IOException {
            String form=str(schema,"form");JsonObject out=node("union");out.addProperty("form",form);
            if(form.equals("either")){boolean left=bool();out.addProperty("left",left);out.add("value",read(schema.getAsJsonObject(left?"true":"false"),depth+1,recursive));return out;}
            if(form.equals("registry_or_inline")){
                int selector=vi();if(selector<0)throw failure("component_holder_id");out.addProperty("inline",selector==0);
                out.add("value",selector==0?read(schema.getAsJsonObject("zero_inline"),depth+1,recursive):registry(str(schema,"registry"),selector-1));return out;
            }
            if(form.equals("holder_set")){
                int selector=vi();if(selector<0||selector>MAX_COLLECTION+1)throw failure("component_holder_set_budget");out.addProperty("registry",str(schema,"registry"));
                if(selector==0){out.addProperty("tag",identifier());return out;}
                JsonArray values=array();Set<Integer> ids=new HashSet<>();for(int i=0;i<selector-1;i++){int id=vi();if(!ids.add(id))throw failure("component_duplicate_key");values.add(registry(str(schema,"registry"),id));}out.add("values",values);return out;
            }
            if(!form.equals("dispatch"))throw failure("component_dispatch_schema");
            JsonObject selector=read(schema.getAsJsonObject("selector"),depth+1,recursive);out.add("selector",selector);JsonObject unwrapped=unwrap(selector);
            String owner=str(schema,"semantic_owner"),group;
            if(owner.equals("net.minecraft.world.item.consume_effects.ConsumeEffect"))group="consume_effects";
            else if(owner.equals("net.minecraft.world.item.component.ItemAttributeModifiers$Display"))group="attribute_displays";
            else if(owner.equals("net.minecraft.core.component.predicates.DataComponentPredicate")){
                if(!unwrapped.get("left").getAsBoolean()){
                    int id=unwrap(unwrapped.getAsJsonObject("value")).get("id").getAsInt();component(id);int tag=u8();if(tag!=10)throw failure("component_presence_predicate_nbt");
                    out.addProperty("component_presence_type",id);out.add("value",nbt(tag,depth+1));return out;
                }
                group="component_predicates";unwrapped=unwrap(unwrapped.getAsJsonObject("value"));
            }else throw failure("component_dispatch_schema");
            int id=unwrapped.get("id").getAsInt();for(JsonElement entry:tables.getAsJsonObject("branches").getAsJsonArray(group)){
                JsonObject branch=entry.getAsJsonObject();if(branch.get("id").getAsInt()==id){out.addProperty("branch",str(branch,"name"));out.add("value",read(branch.getAsJsonObject("wire_schema"),depth+1,recursive));return out;}
            }
            throw failure("component_dispatch_id");
        }
        JsonObject patch(int depth,JsonObject recursive)throws IOException {
            int added=count(122),removed=count(122);reserve(removed);Set<Integer> seen=new HashSet<>();JsonArray additions=array(),removals=array();
            for(int i=0;i<added;i++){int id=vi();JsonObject type=component(id);if(!seen.add(id))throw failure("component_duplicate_key");JsonObject entry=object();entry.addProperty("id",id);entry.add("value",read(type,depth+1,recursive));additions.add(entry);}
            for(int i=0;i<removed;i++){int id=vi();component(id);if(!seen.add(id))throw failure("component_duplicate_key");removals.add(id);}
            JsonObject out=node("patch");out.add("added",additions);out.add("removed",removals);return out;
        }
        JsonObject properties()throws IOException {
            int size=count(16);JsonArray values=array();Set<List<String>> seen=new HashSet<>();
            for(int i=0;i<size;i++){JsonObject value=object();String name=utf8(64),text=utf8(32767);boolean signed=bool();String signature=signed?utf8(1024):null;
                if(!seen.add(Arrays.asList(name,text,signature)))throw failure("component_duplicate_key");value.addProperty("name",name);value.addProperty("value",text);value.add("signature",signature==null?JsonNull.INSTANCE:new JsonPrimitive(signature));values.add(value);}
            JsonObject out=node("properties");out.add("values",values);return out;
        }
        JsonObject nbt(int tag,int depth)throws IOException {
            budget(depth);JsonObject out=node("nbt");out.addProperty("tag",tag);
            switch(tag){
                case 0 -> out.add("value",JsonNull.INSTANCE);
                case 1 -> {out.addProperty("type","int8");out.addProperty("value",(byte)u8());}
                case 2 -> {need(2);out.addProperty("type","int16");out.addProperty("value",input.getShort());}
                case 3 -> {need(4);out.addProperty("type","int32");out.addProperty("value",input.getInt());}
                case 4 -> {need(8);out.addProperty("type","int64");out.addProperty("value",input.getLong());}
                case 5,6 -> {JsonObject numeric=object();numeric.addProperty("type",tag==5?"float32_be":"float64_be");JsonObject value=primitive(numeric);out.addProperty("type",str(value,"type"));out.add("value",value.get("value"));out.add("bits",value.get("bits"));}
                case 8 -> {out.addProperty("type","string");out.addProperty("value",modifiedUtf());}
                case 7,11,12 -> {
                    need(4);int count=input.getInt(),width=tag==7?1:tag==11?4:8;if(count<0||count>MAX_COLLECTION||count>input.remaining()/width)throw failure("component_nbt_array_budget");reserve(count);
                    JsonArray values=array();for(int i=0;i<count;i++){if(tag==7)values.add(input.get());else if(tag==11)values.add(input.getInt());else values.add(input.getLong());}out.add("value",values);
                }
                case 9 -> {
                    int element=u8();need(4);int size=input.getInt();if(element>12||element==0&&size!=0||size<0||size>MAX_COLLECTION)throw failure("component_nbt_list_budget");
                    JsonArray values=array();for(int i=0;i<size;i++)values.add(nbt(element,depth+1));out.addProperty("element_tag",element);out.add("value",values);
                }
                case 10 -> {
                    JsonArray values=array();Set<String> names=new HashSet<>();while(true){int child=u8();if(child==0)break;String name=modifiedUtf();if(!names.add(name))throw failure("component_duplicate_key");JsonObject field=object();field.addProperty("name",name);field.add("value",nbt(child,depth+1));values.add(field);}out.add("value",values);
                }
                default -> throw failure("component_nbt_tag");
            }
            return out;
        }
    }
    private static JsonObject unwrap(JsonObject value){while(str(value,"kind").equals("mapped"))value=value.getAsJsonObject("value");return value;}
    private static String str(JsonObject object,String key){return object.get(key).getAsString();}
    private static IOException failure(String code){return new IOException(code);}
}
