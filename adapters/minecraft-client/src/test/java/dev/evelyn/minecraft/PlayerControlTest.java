package dev.evelyn.minecraft;

import com.google.gson.JsonObject;

/** Read-only inventory projection follows the real receiver and never sends a game action. */
public final class PlayerControlTest {
    private static int checks;
    private static void yes(boolean condition,String reason){if(!condition)throw new AssertionError(reason);checks++;}
    private static JsonObject page(int start,long revision){JsonObject value=new JsonObject();value.addProperty("seq",1);value.addProperty("action","inventory_page");value.addProperty("run_ms",1000);value.addProperty("page_start",start);value.addProperty("inventory_revision",revision);return value;}
    private static JsonObject inventory(ControlFixture fixture){return fixture.control.observation().getAsJsonObject("inventory");}
    public static void main(String[] args)throws Exception {
        try(ControlFixture f=new ControlFixture(true,true)){
            yes(!inventory(f).get("known").getAsBoolean(),"no fabricated login inventory");
            f.handle(ControlFixture.packet(0x12,d->{ControlFixture.vi(d,0);ControlFixture.vi(d,3);ControlFixture.vi(d,46);for(int i=0;i<47;i++)ControlFixture.vi(d,0);}));
            JsonObject initial=inventory(f);yes(initial.get("known").getAsBoolean(),"receiver full inventory");
            yes(initial.getAsJsonArray("slots").size()==16,"bounded first page");
            long revision=initial.get("revision").getAsLong();
            f.control.start(page(32,revision),()->true);yes("succeeded".equals(f.control.poll()),"page selected");
            yes("inventory_page_selected".equals(f.control.resultCode()),"read-only result code");
            yes(inventory(f).get("page_start").getAsInt()==32,"chosen page");
            yes(inventory(f).getAsJsonArray("slots").size()==14,"bounded last page");
            yes(f.output.size()==0,"page sends no game bytes");
            f.control.start(page(0,revision-1),()->true);yes("failed".equals(f.control.poll()),"stale revision fenced");
            yes("inventory_changed".equals(f.control.resultCode()),"exact revision failure");
            f.control.start(page(0,revision),()->false);yes("failed".equals(f.control.poll()),"authority fenced");
            yes(f.output.size()==0,"revoked page sends no game bytes");
            f.handle(ControlFixture.packet(0x14,d->{ControlFixture.vi(d,0);ControlFixture.vi(d,4);d.writeShort(36);ControlFixture.vi(d,1);ControlFixture.vi(d,1);ControlFixture.vi(d,1);ControlFixture.vi(d,0);ControlFixture.vi(d,13);ControlFixture.vi(d,1);ControlFixture.vi(d,0);ControlFixture.vi(d,1);}));
            yes(!inventory(f).get("known").getAsBoolean(),"unsupported patch revokes full baseline");
            yes(inventory(f).get("page_start").getAsInt()==0,"unknown resets page safely");
            f.handle(ControlFixture.packet(0x12,d->{ControlFixture.vi(d,0);ControlFixture.vi(d,5);ControlFixture.vi(d,46);for(int i=0;i<47;i++)ControlFixture.vi(d,0);}));
            f.handle(ControlFixture.packet(0x6b,d->ControlFixture.vi(d,0)));
            yes("empty".equals(f.control.observation().getAsJsonObject("combat").get("hand").getAsString()),"empty hand has complete player baseline");
            f.handle(ControlFixture.packet(0x3c,d->{ControlFixture.vi(d,1);ControlFixture.vi(d,5);d.writeByte(8);d.writeUTF("");}));
            f.handle(ControlFixture.packet(0x12,d->{ControlFixture.vi(d,1);ControlFixture.vi(d,0);ControlFixture.vi(d,90);for(int i=0;i<90;i++){if(i==81){ControlFixture.vi(d,1);ControlFixture.vi(d,1);ControlFixture.vi(d,0);ControlFixture.vi(d,0);}else ControlFixture.vi(d,0);}ControlFixture.vi(d,0);}));
            yes(!"empty".equals(f.control.observation().getAsJsonObject("combat").get("hand").getAsString()),"foreign menu cannot retain stale empty hand authority");
        }
        try(ControlFixture f=new ControlFixture(true)){
            yes(!f.control.observation().has("inventory"),"v4 exact projection preserved");
        }
        try(ControlFixture f=new ControlFixture(true,true)){
            try{
                f.handle(ControlFixture.packet(0x12,d->{ControlFixture.vi(d,0);ControlFixture.vi(d,0);ControlFixture.vi(d,46);for(int i=0;i<46;i++){if(i==0){ControlFixture.vi(d,1);ControlFixture.vi(d,1658);ControlFixture.vi(d,0);ControlFixture.vi(d,0);}else ControlFixture.vi(d,0);}ControlFixture.vi(d,0);}));
                throw new AssertionError("unknown official item id accepted as known stack");
            }catch(java.io.IOException expected){checks++;}
            yes(f.connection.snapshot().stage()==VanillaConnection.Stage.CLOSED,"malformed item closes exact owner");
        }
        System.out.println("PLAYER_CONTROL_PASS "+checks);
    }
}
