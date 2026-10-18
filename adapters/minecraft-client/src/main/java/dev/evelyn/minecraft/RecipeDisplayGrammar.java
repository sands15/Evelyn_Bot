package dev.evelyn.minecraft;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.io.IOException;
import java.util.*;

final class RecipeDisplayGrammar {
    sealed interface Slot permits Empty,AnyFuel,WithPotion,WithComponent,Item,Stack,Tag,Dyed,Trim,Remainder,Composite {}
    record Empty() implements Slot {}
    record AnyFuel() implements Slot {}
    record WithPotion(Slot display) implements Slot {}
    record WithComponent(Slot source,int componentTypeId) implements Slot {}
    record Item(int itemId) implements Slot {}
    record Stack(Value template) implements Slot {}
    record Tag(Value holders) implements Slot {}
    record Dyed(Slot dye,Slot target) implements Slot {}
    record Trim(Slot base,Slot material,Value pattern) implements Slot {}
    record Remainder(Slot input,Slot remainder) implements Slot {}
    record Composite(List<Slot> contents) implements Slot { Composite{contents=List.copyOf(contents);} }
    sealed interface Display permits Shapeless,Shaped,Furnace,Stonecutter,Smithing {}
    record Shapeless(List<Slot> ingredients,Slot result,Slot craftingStation) implements Display {Shapeless{ingredients=List.copyOf(ingredients);}}
    record Shaped(int width,int height,List<Slot> ingredients,Slot result,Slot craftingStation) implements Display {Shaped{ingredients=List.copyOf(ingredients);}}
    record Furnace(Slot ingredient,Slot fuel,Slot result,Slot craftingStation,int duration,float experience) implements Display {}
    record Stonecutter(Slot input,Slot result,Slot craftingStation) implements Display {}
    record Smithing(Slot template,Slot base,Slot addition,Slot result,Slot craftingStation) implements Display {}
    /** Sole ownership of a newly decoded tree; copies returned to consumers never alias retained syntax. */
    static final class Value {
        private final JsonObject syntax;
        private Value(JsonObject syntax){this.syntax=syntax;}
        JsonObject copy(){return syntax.deepCopy();}
        boolean effectiveKnown(){return false;}
    }
    private RecipeDisplayGrammar(){}
    private static JsonObject schema(String text){return JsonParser.parseString(text).getAsJsonObject();}
    private static final JsonObject HOLDERS=schema("{\"kind\":\"union\",\"form\":\"holder_set\",\"registry\":\"minecraft:item\"}");
    private static final JsonObject TEMPLATE=schema("{\"kind\":\"nested-stack\",\"form\":\"item_template\",\"semantic_owner\":\"net.minecraft.world.item.ItemStackTemplate\",\"fields\":[{\"kind\":\"registry-holder\",\"registry\":\"minecraft:item\"},{\"kind\":\"primitive\",\"type\":\"varint\"},{\"kind\":\"nested-stack\",\"form\":\"component_patch\"}],\"field_names\":[\"item_id\",\"count\",\"component_patch\"]}");
    private static final JsonObject PATTERN=schema("{\"kind\":\"union\",\"form\":\"registry_or_inline\",\"registry\":\"minecraft:trim_pattern\",\"zero_inline\":{\"kind\":\"compound\",\"form\":\"ordered_fields\",\"semantic_owner\":\"net.minecraft.world.item.equipment.trim.TrimPattern\",\"fields\":[{\"kind\":\"mapped\",\"semantic_owner\":\"net.minecraft.resources.Identifier\",\"wire\":{\"kind\":\"primitive\",\"type\":\"utf8_string\",\"max_chars\":32767}},{\"kind\":\"NBT\"},{\"kind\":\"primitive\",\"type\":\"bool\"}]}}");
    private static final JsonObject FLOAT=schema("{\"kind\":\"primitive\",\"type\":\"float32_be\"}");
    static Slot decodeSlot(byte[] bytes,Map<String,Integer> context)throws IOException {var in=ComponentWireCodec.open(bytes,0,context);Slot out=slot(in,0);end(in);return out;}
    static Display decodeDisplay(byte[] bytes,Map<String,Integer> context)throws IOException {var in=ComponentWireCodec.open(bytes,0,context);Display out=display(in,0);end(in);return out;}
    static void end(ComponentWireCodec.DecodeSession in)throws IOException {if(in.remaining()!=0)throw new IOException("recipe_trailing_data");}
    static int nonnegative(ComponentWireCodec.DecodeSession in)throws IOException {int id=in.vi();if(id<0)throw new IOException("recipe_negative_id");return id;}
    static int registry(ComponentWireCodec.DecodeSession in,int size)throws IOException {int id=nonnegative(in);if(id>=size)throw new IOException("recipe_registry_id");return id;}
    static Value holders(ComponentWireCodec.DecodeSession in,int depth)throws IOException {return value(in,HOLDERS,depth);}
    private static Value value(ComponentWireCodec.DecodeSession in,JsonObject schema,int depth)throws IOException {
        var decoded=in.readTrustedSchema(schema,depth);in.reserve(1);return new Value(decoded.value());
    }
    private static List<Slot> slots(ComponentWireCodec.DecodeSession in,int depth)throws IOException {
        int count=in.count(4096);in.reserve(1+count);var out=new ArrayList<Slot>(count);
        for(int i=0;i<count;i++)out.add(slot(in,depth));in.reserve(1+count);return List.copyOf(out);
    }
    static Slot slot(ComponentWireCodec.DecodeSession in,int depth)throws IOException {
        in.checkDepth(depth);int type=registry(in,11);in.reserve(1);
        return switch(type) {
            case 0->new Empty();case 1->new AnyFuel();
            case 2->new WithPotion(slot(in,depth+1));
            case 3->new WithComponent(slot(in,depth+1),registry(in,122));
            case 4->new Item(registry(in,1658));
            case 5->{
                var decoded=in.readTrustedSchema(TEMPLATE,depth+1);var fields=decoded.value().getAsJsonArray("fields");
                int item=fields.get(0).getAsJsonObject().getAsJsonObject("value").get("id").getAsInt();
                int count=fields.get(1).getAsJsonObject().getAsJsonObject("value").get("value").getAsInt();
                if(item==0||count==0)throw new IOException("recipe_empty_item_template");
                in.reserve(1);yield new Stack(new Value(decoded.value()));
            }
            case 6->new Tag(holders(in,depth+1));
            case 7->new Dyed(slot(in,depth+1),slot(in,depth+1));
            case 8->new Trim(slot(in,depth+1),slot(in,depth+1),value(in,PATTERN,depth+1));
            case 9->new Remainder(slot(in,depth+1),slot(in,depth+1));
            case 10->new Composite(slots(in,depth+1));
            default->throw new IOException("recipe_slot_type");
        };
    }
    static Display display(ComponentWireCodec.DecodeSession in,int depth)throws IOException {
        in.checkDepth(depth);int type=registry(in,5);in.reserve(1);
        return switch(type) {
            case 0->new Shapeless(slots(in,depth+1),slot(in,depth+1),slot(in,depth+1));
            case 1->{int width=in.vi(),height=in.vi();var contents=slots(in,depth+1);if(contents.size()!=width*height)throw new IOException("recipe_shaped_size");yield new Shaped(width,height,contents,slot(in,depth+1),slot(in,depth+1));}
            case 2->{var ingredient=slot(in,depth+1);var fuel=slot(in,depth+1);var result=slot(in,depth+1);var station=slot(in,depth+1);int duration=in.vi();float experience=in.readTrustedSchema(FLOAT,depth+1).value().get("value").getAsFloat();yield new Furnace(ingredient,fuel,result,station,duration,experience);}
            case 3->new Stonecutter(slot(in,depth+1),slot(in,depth+1),slot(in,depth+1));
            case 4->new Smithing(slot(in,depth+1),slot(in,depth+1),slot(in,depth+1),slot(in,depth+1),slot(in,depth+1));
            default->throw new IOException("recipe_display_type");
        };
    }
}
