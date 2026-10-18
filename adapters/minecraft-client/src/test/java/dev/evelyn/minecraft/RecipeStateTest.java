package dev.evelyn.minecraft;

import com.google.gson.*;
import java.io.*;
import java.lang.reflect.Field;
import java.util.*;

public final class RecipeStateTest {
    static int checks;
    static final HexFormat HEX=HexFormat.of();
    static void eq(Object actual,Object expected,String label){checks++;if(!Objects.equals(actual,expected))throw new AssertionError(label+": "+actual+" != "+expected);}
    @FunctionalInterface interface Action{void run()throws Exception;}
    static void reject(Action action,String label)throws Exception{try{action.run();throw new AssertionError("accepted "+label);}catch(IOException expected){checks++;}}
    static void immutable(Action action,String label)throws Exception{try{action.run();throw new AssertionError("mutable "+label);}catch(UnsupportedOperationException expected){checks++;}}
    static byte[] bytes(String hex){return HEX.parseHex(hex);}
    static void vi(ByteArrayOutputStream out,int n){while((n&~127)!=0){out.write((n&127)|128);n>>>=7;}out.write(n);}
    static byte[] concat(byte[]... values){var out=new ByteArrayOutputStream();for(var b:values)out.writeBytes(b);return out.toByteArray();}
    static byte[] entry(int id,byte[] display){var out=new ByteArrayOutputStream();vi(out,id);out.writeBytes(display);out.writeBytes(new byte[]{0,0,0,0});return out.toByteArray();}
    static byte[] add(boolean replace,byte[]... entries){var out=new ByteArrayOutputStream();vi(out,entries.length);for(var e:entries)out.writeBytes(e);out.write(replace?1:0);return out.toByteArray();}
    static byte[] shapeless(int count,byte[] ingredient){var out=new ByteArrayOutputStream();out.write(0);vi(out,count);for(int i=0;i<count;i++)out.writeBytes(ingredient);out.writeBytes(new byte[]{0,0});return out.toByteArray();}
    static RecipeState state(){return new RecipeState(1,Map.of());}
    static void initial(RecipeState state)throws Exception{state.accept(1,0x4b,bytes("0107000000000000000301"));}
    static int templateField(RecipeDisplayGrammar.Stack stack,int index,String key){return stack.template().copy().getAsJsonArray("fields").get(index).getAsJsonObject().getAsJsonObject("value").get(key).getAsInt();}
    static void literal(String label,byte[] wire)throws Exception {
        if(label.startsWith("slot_display_")){
            String[] types={"Empty","AnyFuel","WithPotion","WithComponent","Item","Stack","Tag","Dyed","Trim","Remainder","Composite"};int id=Integer.parseInt(label.substring(13));var value=RecipeDisplayGrammar.decodeSlot(wire,Map.of());eq(value.getClass().getSimpleName(),types[id],label+" typed structure");
            if(value instanceof RecipeDisplayGrammar.Item it)eq(it.itemId(),1,"literal item1");
            if(value instanceof RecipeDisplayGrammar.Stack st){eq(templateField(st,0,"id"),7,"template item before count");eq(templateField(st,1,"value"),1,"template count");eq(st.template().effectiveKnown(),false,"syntax not effective stack");st.template().copy().remove("fields");eq(templateField(st,0,"id"),7,"consumer copy is defensive");}
            if(value instanceof RecipeDisplayGrammar.WithComponent c)eq(c.componentTypeId(),4,"component type domain");
            if(value instanceof RecipeDisplayGrammar.Composite c){eq(c.contents().size(),2,"composite two entries");eq(c.contents().get(1).getClass(),RecipeDisplayGrammar.AnyFuel.class,"composite child");immutable(()->c.contents().clear(),"composite list");}
        }else if(label.startsWith("recipe_display_")){
            String[] types={"Shapeless","Shaped","Furnace","Stonecutter","Smithing"};int id=Integer.parseInt(label.substring(15));var value=RecipeDisplayGrammar.decodeDisplay(wire,Map.of());eq(value.getClass().getSimpleName(),types[id],label+" typed structure");
            if(value instanceof RecipeDisplayGrammar.Shaped s){eq(s.width(),1,"width");eq(s.height(),1,"height");eq(s.ingredients().size(),1,"shape size");}
            if(value instanceof RecipeDisplayGrammar.Furnace f){eq(f.duration(),1,"furnace duration");eq(f.experience(),0f,"furnace XP");}
        }else if(label.startsWith("display_entry_")){
            var value=RecipeState.decodeEntry(wire,Map.of());eq(value.id().value(),7,"server display7 distinct from dispatch0");eq(value.group(),label.contains("absent")?null:0,"optional group absent vs zero");eq(value.requirements()==null,label.contains("absent"),"absent vs empty requirements");
        }else{
            int id=switch(label){case "book_add"->0x4b;case "book_remove"->0x4c;case "book_settings"->0x4d;case "update_recipes"->0x88;case "ghost_recipe"->0x40;default->throw new AssertionError(label);};
            var state=state();state.accept(1,id,wire);var snapshot=state.snapshot();eq(snapshot.revision(),1L,"observation revision");eq(snapshot.craftabilityKnown(),false,"no craftability promotion");
            if(id==0x4b){eq(snapshot.bookComplete(),true,"replace baseline");var value=snapshot.bindings().get(new RecipeState.DisplayId(7));eq(value.flags(),3,"literal flags");eq(value.notification()&&value.highlight(),true,"known flag bits");}
            if(id==0x4d){eq(snapshot.settings().types().size(),4,"settings four types");for(var setting:snapshot.settings().types()){eq(setting.open(),true,"open");eq(setting.filtering(),false,"filter");}}
            if(id==0x88){eq(snapshot.properties().itemSets().get("minecraft:furnace_input"),List.of(1),"property item binding");eq(snapshot.properties().stonecutter().size(),1,"stonecutter entry without ID");eq(((RecipeDisplayGrammar.Item)snapshot.properties().stonecutter().getFirst().display()).itemId(),7,"display item7 is not recipe ID");eq(snapshot.bindings().size(),0,"no invented book ID");}
            if(id==0x40){eq(snapshot.ghost().containerId(),1,"ghost container");eq(snapshot.ghost().display().getClass(),RecipeDisplayGrammar.Stonecutter.class,"ghost display");eq(snapshot.bindings().size(),0,"ghost invents no binding");}
        }
    }
    static void raw(String label,byte[] wire)throws Exception {
        if(label.startsWith("slot_"))RecipeDisplayGrammar.decodeSlot(wire,Map.of());else if(label.startsWith("recipe_"))RecipeDisplayGrammar.decodeDisplay(wire,Map.of());else if(label.startsWith("display_entry_"))RecipeState.decodeEntry(wire,Map.of());
        else state().accept(1,switch(label){case "book_add"->0x4b;case "book_remove"->0x4c;case "book_settings"->0x4d;case "update_recipes"->0x88;case "ghost_recipe"->0x40;default->throw new AssertionError(label);},wire);
    }
    // Independent exact official25 codec literals; source SHA256 e2874582c320992c445cb07a5b3b39d5c1d424bd4ebff316933b05fb118ec605.
    static final String OFFICIAL="[{\"label\":\"slot_display_0\",\"wire_hex\":\"00\"},{\"label\":\"slot_display_1\",\"wire_hex\":\"01\"},{\"label\":\"slot_display_2\",\"wire_hex\":\"0200\"},{\"label\":\"slot_display_3\",\"wire_hex\":\"030004\"},{\"label\":\"slot_display_4\",\"wire_hex\":\"0401\"},{\"label\":\"slot_display_5\",\"wire_hex\":\"0507010000\"},{\"label\":\"slot_display_6\",\"wire_hex\":\"0601\"},{\"label\":\"slot_display_7\",\"wire_hex\":\"070000\"},{\"label\":\"slot_display_8\",\"wire_hex\":\"080000000a6d696e6563726166743a08000000\"},{\"label\":\"slot_display_9\",\"wire_hex\":\"090000\"},{\"label\":\"slot_display_10\",\"wire_hex\":\"0a020001\"},{\"label\":\"recipe_display_0\",\"wire_hex\":\"00000000\"},{\"label\":\"recipe_display_1\",\"wire_hex\":\"01010101000000\"},{\"label\":\"recipe_display_2\",\"wire_hex\":\"02000100000100000000\"},{\"label\":\"recipe_display_3\",\"wire_hex\":\"03000000\"},{\"label\":\"recipe_display_4\",\"wire_hex\":\"040000000000\"},{\"label\":\"display_entry_group_absent\",\"wire_hex\":\"0700000000000000\"},{\"label\":\"display_entry_group_zero_requirements_empty\",\"wire_hex\":\"070000000001000100\"},{\"label\":\"book_add\",\"wire_hex\":\"0107000000000000000301\"},{\"label\":\"book_remove\",\"wire_hex\":\"0107\"},{\"label\":\"book_settings\",\"wire_hex\":\"0100010001000100\"},{\"label\":\"update_recipes\",\"wire_hex\":\"01176d696e6563726166743a6675726e6163655f696e70757401010102010407\"},{\"label\":\"ghost_recipe\",\"wire_hex\":\"0103000000\"},{\"label\":\"place_recipe\",\"wire_hex\":\"010701\"},{\"label\":\"mount_open\",\"wire_hex\":\"010500000007\"}]";
    static void official()throws Exception {
        var vectors=JsonParser.parseString(OFFICIAL).getAsJsonArray();eq(vectors.size(),25,"official25 accounted");int decoded=0,excluded=0;
        for(var item:vectors){var row=item.getAsJsonObject();String label=row.get("label").getAsString();byte[] wire=bytes(row.get("wire_hex").getAsString());
            if(label.equals("place_recipe")||label.equals("mount_open")){eq(RecipeState.supports(label.equals("place_recipe")?0x27:0x2a),false,"explicit outside-unit literal");excluded++;continue;}
            literal(label,wire);for(int size=0;size<wire.length;size++){byte[] part=Arrays.copyOf(wire,size);reject(()->raw(label,part),"official truncation "+label+"/"+size);}reject(()->raw(label,concat(wire,new byte[]{0})),"official trailing "+label);decoded++;
        }eq(decoded,23,"applicable literals decoded");eq(excluded,2,"sender/mount excluded");
    }
    static void transitions()throws Exception {
        var state=state();eq(state.snapshot().settings(),null,"settings unknown");eq(state.snapshot().properties(),null,"properties unknown");initial(state);var old=state.snapshot();
        state.accept(1,0x4b,add(false,entry(9000,bytes("03000000"))));eq(state.snapshot().bindings().size(),2,"merge IDs beyond collection limit");eq(state.snapshot().bindings().get(new RecipeState.DisplayId(9000)).display().getClass(),RecipeDisplayGrammar.Stonecutter.class,"dispatch3 distinct from display9000");
        state.accept(1,0x4b,add(false,entry(7,bytes("040000000000"))));eq(state.snapshot().bindings().size(),2,"replace single ID");eq(state.snapshot().bindings().get(new RecipeState.DisplayId(7)).observedRevision(),3L,"binding observed revision");
        state.accept(1,0x4b,add(false,entry(9,bytes("00000000")),entry(9,bytes("03000000"))));eq(state.snapshot().bindings().get(new RecipeState.DisplayId(9)).display().getClass(),RecipeDisplayGrammar.Stonecutter.class,"official duplicate ID last-wins");
        state.accept(1,0x4c,bytes("03070709"));eq(state.snapshot().bindings().keySet(),Set.of(new RecipeState.DisplayId(9000)),"remove repeated IDs");
        state.accept(1,0x4b,add(true,entry(4,bytes("00000000"))));eq(state.snapshot().bindings().keySet(),Set.of(new RecipeState.DisplayId(4)),"replace clears old IDs");eq(old.bindings().keySet(),Set.of(new RecipeState.DisplayId(7)),"old snapshot preserved");immutable(()->state.snapshot().bindings().clear(),"binding map");
        var newer=new RecipeState(2,Map.of());newer.accept(2,0x4b,bytes("0107000000000000000301"));var saved=newer.snapshot();reject(()->newer.accept(1,0x4b,null),"stale invalid packet");eq(newer.snapshot(),saved,"stale generation preserves new binding");eq(newer.accept(2,0x27,new byte[0]),false,"ignore non-observation packet");eq(newer.snapshot(),saved,"non-observation preserves state");
        reject(()->newer.accept(2,0x4b,bytes("000100")),"malformed trailing Add");eq(newer.snapshot().bookComplete(),false,"invalid clears completeness");eq(newer.snapshot().bindings().size(),0,"malformed atomic clear");eq(newer.snapshot().revision(),2L,"invalidated revision");
        initial(state);state.accept(1,0x40,bytes("0103000000"));state.accept(1,0x4d,bytes("0100010001000100"));state.accept(1,0x88,bytes("0000"));eq(state.snapshot().bindings().size(),0,"context refresh invalidates ID bindings");eq(state.snapshot().ghost(),null,"context refresh invalidates ghost");eq(state.snapshot().settings()!=null,true,"settings still observed");
        state.clear(1);eq(state.snapshot().settings(),null,"clear settings unknown");eq(state.snapshot().properties(),null,"clear context unknown");initial(state);Field revision=RecipeState.class.getDeclaredField("revision");revision.setAccessible(true);revision.setLong(state,Long.MAX_VALUE);reject(()->state.accept(1,0x4d,new byte[8]),"revision exhaustion");eq(state.snapshot().exhausted(),true,"exhausted fails closed");eq(state.snapshot().bindings().size(),0,"exhausted clears IDs");eq(state.snapshot().revision(),Long.MAX_VALUE,"no revision wrap");reject(()->state.accept(1,0x4b,bytes("0001")),"no reopen after exhaustion");
    }
    static void limits()throws Exception {
        var dyed=(RecipeDisplayGrammar.Dyed)RecipeDisplayGrammar.decodeSlot(bytes("07040101"),Map.of());eq(((RecipeDisplayGrammar.Item)dyed.dye()).itemId(),1,"dyed source order");eq(dyed.target().getClass(),RecipeDisplayGrammar.AnyFuel.class,"dyed target order");
        var remainder=(RecipeDisplayGrammar.Remainder)RecipeDisplayGrammar.decodeSlot(bytes("09040101"),Map.of());eq(((RecipeDisplayGrammar.Item)remainder.input()).itemId(),1,"remainder input order");eq(remainder.remainder().getClass(),RecipeDisplayGrammar.AnyFuel.class,"remainder output order");
        var stone=(RecipeDisplayGrammar.Stonecutter)RecipeDisplayGrammar.decodeDisplay(bytes("03040104020403"),Map.of());eq(((RecipeDisplayGrammar.Item)stone.input()).itemId(),1,"stonecutter input order");eq(((RecipeDisplayGrammar.Item)stone.result()).itemId(),2,"stonecutter result order");eq(((RecipeDisplayGrammar.Item)stone.craftingStation()).itemId(),3,"stonecutter station order");
        for(String wire:List.of("0b","04fa0c","03007a","0500010000","0507000000","06030202"))reject(()->RecipeDisplayGrammar.decodeSlot(bytes(wire),Map.of()),"invalid slot field "+wire);
        reject(()->RecipeDisplayGrammar.decodeDisplay(bytes("01020101000000"),Map.of()),"shaped width*height mismatch");var odd=(RecipeDisplayGrammar.Shaped)RecipeDisplayGrammar.decodeDisplay(bytes("010000000000"),Map.of());eq(odd.width(),0,"zero grid structure");eq(odd.ingredients().size(),0,"zero grid is not craftability");
        var negative=(RecipeDisplayGrammar.Stack)RecipeDisplayGrammar.decodeSlot(bytes("0507ffffffff0f0000"),Map.of());eq(templateField(negative,1,"value"),-1,"official constructor forbids zero count, not all negative counts");
        eq(RecipeDisplayGrammar.decodeSlot(bytes("08000001"),Map.of("minecraft:trim_pattern",1)).getClass(),RecipeDisplayGrammar.Trim.class,"supplied dynamic registry");reject(()->RecipeDisplayGrammar.decodeSlot(bytes("08000001"),Map.of()),"missing trim registry");
        for(int count:new int[]{32,33}){var out=new ByteArrayOutputStream();for(int i=0;i<count;i++)out.write(2);out.write(0);if(count==32)eq(RecipeDisplayGrammar.decodeSlot(out.toByteArray(),Map.of()).getClass(),RecipeDisplayGrammar.WithPotion.class,"depth32 boundary");else reject(()->RecipeDisplayGrammar.decodeSlot(out.toByteArray(),Map.of()),"depth33");}
        var mixed=new ByteArrayOutputStream();for(int i=0;i<31;i++)mixed.write(2);mixed.writeBytes(bytes("0507010000"));reject(()->RecipeDisplayGrammar.decodeSlot(mixed.toByteArray(),Map.of()),"outer depth shared with template");
        var attack=add(true,entry(7,shapeless(4096,bytes("050701010004"))));var state=state();initial(state);reject(()->state.accept(1,0x4b,attack),"valid-looking shared component allocation attack");eq(state.snapshot().bindings().size(),0,"shared budget clears book");
        var many=new ByteArrayOutputStream();vi(many,4097);reject(()->state().accept(1,0x4b,many.toByteArray()),"list4097 before allocation");reject(()->state().accept(1,0x4b,new byte[1048577]),"packet one MiB");
        var retained=state();for(int i=0;i<5;i++)retained.accept(1,0x4b,add(i==0,entry(i,shapeless(4000,new byte[]{0}))));eq(retained.snapshot().bindings().size(),5,"retained normal near node limit");reject(()->retained.accept(1,0x4b,add(false,entry(5,shapeless(4000,new byte[]{0})))),"retained nodes across packets");eq(retained.snapshot().bindings().size(),0,"retained nodes clear baseline");
        var combined=state();for(int i=0;i<5;i++)combined.accept(1,0x4b,add(i==0,entry(i,shapeless(4000,new byte[]{0}))));reject(()->combined.accept(1,0x40,concat(new byte[]{1},shapeless(4000,new byte[]{0}))),"book and ghost combined retained budget");
        String name="minecraft:"+"a".repeat(32757);byte[] text=name.getBytes(java.nio.charset.StandardCharsets.UTF_8);var tag=new ByteArrayOutputStream();tag.write(6);tag.write(0);vi(tag,text.length);tag.writeBytes(text);
        var wire=state();for(int i=0;i<31;i++)wire.accept(1,0x4b,add(i==0,entry(i,shapeless(1,tag.toByteArray()))));eq(wire.snapshot().bindings().size(),31,"retained wire spans below one MiB");reject(()->wire.accept(1,0x4b,add(false,entry(31,shapeless(1,tag.toByteArray())))),"retained wire spans above one MiB");
        var mutable=new ArrayList<>(List.of(1));var props=new RecipeState.Properties(Map.of("minecraft:test",mutable),List.of());mutable.clear();eq(props.itemSets().get("minecraft:test"),List.of(1),"deep property copy");immutable(()->props.itemSets().get("minecraft:test").clear(),"nested property list");
        var atomic=state();atomic.accept(1,0x88,bytes("0000"));atomic.accept(1,0x40,bytes("0103000000"));atomic.accept(1,0x4d,bytes("0100010001000100"));initial(atomic);atomic.accept(1,0x40,bytes("0103000000"));
        var prior=atomic.snapshot();reject(()->atomic.accept(1,0x4b,bytes("010700000000000000030100")),"late malformed packet clears all channels");eq(atomic.snapshot().bindings().size(),0,"atomic book clear");eq(atomic.snapshot().settings(),null,"atomic settings clear");eq(atomic.snapshot().properties(),null,"atomic properties clear");eq(atomic.snapshot().ghost(),null,"atomic ghost clear");eq(prior.bindings().size(),1,"consumer prior snapshot survives invalidation");
        byte[] original=bytes("0107000000000000000301");var owned=state();owned.accept(1,0x4b,original);Arrays.fill(original,(byte)0);eq(owned.snapshot().bindings().size(),1,"packet bytes never retained by reference");
    }
    public static void main(String[] args)throws Exception{official();transitions();limits();System.out.println("RECIPE_STATE_PASS "+checks);}
}
