package dev.evelyn.minecraft;

import java.io.IOException;
import java.util.HexFormat;

public final class RecipeControlTest {
    static int checks;
    static void check(boolean value,String label){checks++;if(!value)throw new AssertionError(label);}
    static byte[] packet(int id,String hex)throws IOException{return ControlFixture.packet(id,d->d.write(HexFormat.of().parseHex(hex)));}
    static void menu(ControlFixture f,int type,int slots)throws Exception{
        f.handle(ControlFixture.packet(0x3c,d->{ControlFixture.vi(d,1);ControlFixture.vi(d,type);d.writeByte(8);d.writeUTF("");}));
        f.handle(ControlFixture.packet(0x12,d->{ControlFixture.vi(d,1);ControlFixture.vi(d,3);ControlFixture.vi(d,slots);for(int i=0;i<=slots;i++)ControlFixture.vi(d,0);}));
    }
    static void pureGhostLifetime()throws Exception{
        var state=new RecipeState(1,java.util.Map.of());state.accept(1,0x4b,HexFormat.of().parseHex("0107000000000000000301"));
        state.accept(1,0x40,HexFormat.of().parseHex("0103000000"));var before=state.snapshot();
        state.clearGhost(1);var after=state.snapshot();
        check(after.ghost()==null&&after.bookComplete()&&after.bindings().equals(before.bindings())&&after.revision()>before.revision(),"menu change clears only ghost authority");
        check(before.ghost()!=null,"old consumer snapshot immutable");
        state.clearGhost(1);check(state.snapshot().revision()==after.revision(),"no phantom change with no ghost");
        try{state.clearGhost(2);throw new AssertionError("stale ghost callback accepted");}catch(IOException expected){checks++;}
        check(state.snapshot().equals(after),"stale menu callback preserves newer data");
    }
    public static void main(String[] args)throws Exception{
        pureGhostLifetime();
        try(ControlFixture f=new ControlFixture(true,true)){
            var initial=f.control.recipeSnapshot();check(initial!=null&&!initial.bookComplete()&&initial.bindings().isEmpty()&&initial.settings()==null,"login starts unobserved recipe state");
            f.handle(packet(0x4b,"0107000000000000000301"));
            var book=f.control.recipeSnapshot();check(book.bookComplete()&&book.bindings().containsKey(new RecipeState.DisplayId(7))&&!book.craftabilityKnown(),"actual Add binds display ID without craftability");
            f.handle(packet(0x40,"0103000000"));check(f.control.recipeSnapshot().ghost()==null,"unobserved menu cannot own a ghost");
            menu(f,0,45);f.handle(packet(0x40,"0103000000"));check(f.control.recipeSnapshot().ghost()!=null,"current known menu owns ghost hint");
            f.handle(packet(0x40,"0203000000"));check(f.control.recipeSnapshot().ghost()==null,"foreign container hint cannot retain current authority");
            f.handle(packet(0x40,"0103000000"));menu(f,14,39);
            check(f.control.recipeSnapshot().ghost()==null&&f.control.recipeSnapshot().bookComplete()&&f.control.recipeSnapshot().bindings().containsKey(new RecipeState.DisplayId(7)),"same container ID reopened clears ghost only");
            f.handle(packet(0x40,"0103000000"));f.handle(MenuPropertiesTest.property(1,0,5));
            check(f.control.recipeSnapshot().ghost()!=null,"progress properties do not fabricate changed item contents");
            f.handle(ControlFixture.packet(0x14,d->{ControlFixture.vi(d,1);ControlFixture.vi(d,4);d.writeShort(0);ControlFixture.vi(d,0);}));
            check(f.control.recipeSnapshot().ghost()==null,"received item changes invalidate old ghost hint");
            f.handle(packet(0x4d,"0100010001000100"));check(f.control.recipeSnapshot().settings().types().size()==4,"four observed book settings");
            f.handle(packet(0x88,"0000"));check(f.control.recipeSnapshot().properties()!=null&&!f.control.recipeSnapshot().bookComplete()&&f.control.recipeSnapshot().bindings().isEmpty(),"property refresh conservatively drops old book ID bindings");
            f.handle(packet(0x4b,"0107000000000000000301"));f.handle(packet(0x4c,"0107"));check(f.control.recipeSnapshot().bindings().isEmpty()&&f.control.recipeSnapshot().bookComplete(),"actual Remove keeps received replacement baseline");
            f.connection.close();check(f.control.recipeSnapshot()==null,"owner close discards recipe context");
        }
        try(ControlFixture f=new ControlFixture(true,true)){
            f.handle(packet(0x4b,"0107000000000000000301"));
            try{f.handle(packet(0x4b,"010700000000000000030100"));throw new AssertionError("bad recipe accepted");}catch(IOException expected){checks++;}
            check(f.connection.snapshot().stage()==VanillaConnection.Stage.CLOSED&&f.control.recipeSnapshot()==null,"malformed recipe closes owner and all recipe authority");
        }
        try(ControlFixture f=new ControlFixture(true,false)){
            check(f.control.recipeSnapshot()==null,"v4 has no new recipe state");f.handle(packet(0x4b,"ff"));
            check(f.connection.snapshot().stage()==VanillaConnection.Stage.PLAY,"v4 previous ignored packet behavior preserved");
        }
        System.out.println("RECIPE_CONTROL_PASS "+checks);
    }
}
