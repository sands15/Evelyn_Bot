package dev.evelyn.minecraft;

import java.io.ByteArrayOutputStream;
import java.io.IOException;

/** Exact official 26.3 constructor layouts, including Crafter preview and no player slots in Lectern. */
public final class MenuLayoutObservationTest {
    private static int checks;
    private static void check(boolean value,String why){if(!value)throw new AssertionError(why);checks++;}
    private static byte[] full(int container,int count,int item,int cursor)throws IOException {
        ByteArrayOutputStream out=new ByteArrayOutputStream();out.write(container);out.write(9);out.write(count);
        for(int slot=0;slot<count;slot++)stack(out,slot==0?item:0);stack(out,cursor);return out.toByteArray();
    }
    private static void stack(ByteArrayOutputStream out,int item){out.write(item==0?0:1);if(item!=0)out.writeBytes(new byte[]{(byte)item,0,0});}
    private static byte[] playerFull(){
        ByteArrayOutputStream out=new ByteArrayOutputStream();out.writeBytes(new byte[]{0,10,46});
        for(int slot=0;slot<46;slot++)stack(out,slot==9?16:slot==44?17:0);stack(out,18);return out.toByteArray();
    }
    private static int item(InventoryState state,int slot){return state.view().slots().get(slot).itemId();}
    public static void main(String[] args)throws Exception {
        // Derived independently from the pinned official constructor traces, not totalSlots-36.
        int[] totals={45,54,63,72,81,90,45,46,39,37,39,41,46,38,39,39,41,1,40,39,63,40,39,39,38};
        int[] starts={9,18,27,36,45,54,9,9,3,1,3,5,10,2,3,3,5,-1,4,3,27,4,3,3,2};
        for(int type=0;type<totals.length;type++){
            InventoryState state=new InventoryState();state.accept(0x3c,new byte[]{5,(byte)type,8,0,0});
            check(!state.view().known(),"opening type "+type+" requires its own full baseline");
            state.accept(0x12,full(5,totals[type],1,21));
            check(state.view().known()&&state.view().slots().size()==totals[type]&&state.view().menuTypeId()==type,"official full slot count for type "+type);
            var page=state.snapshot((totals[type]-1)/16*16);
            check(page.getAsJsonArray("slots").size()==totals[type]-page.get("page_start").getAsInt(),"last page exact for type "+type);
            if(type==7)state.accept(0x14,new byte[]{5,9,0,45,1,11,0,0});
            long before=state.view().revision();
            state.accept(0x6e,new byte[]{9,1,13,0,0});
            state.accept(0x6e,new byte[]{8,1,14,0,0});
            if(starts[type]>=0){
                check(item(state,starts[type])==13,"direct main9 at exact embedded start for type "+type);
                check(item(state,starts[type]+35)==14,"direct hotbar8 at exact embedded end for type "+type);
            }else check(state.view().revision()==before&&item(state,0)==1,"no invented player slots in Lectern");
            state.accept(0x14,new byte[]{0,10,0,36,1,15,0,0});
            if(starts[type]>=0)check(item(state,starts[type]+27)==15,"shared player menu36 maps hotbar0 for type "+type);
            state.accept(0x12,playerFull());
            check(item(state,0)==1&&state.view().carried().itemId()==21&&state.view().stateId()==9,"local slots/cursor/state preserved across player full for type "+type);
            if(starts[type]>=0)check(item(state,starts[type])==16&&item(state,starts[type]+35)==17,"shared full maps both player ends for type "+type);
            if(type==7)check(item(state,45)==11,"Crafter preview45 is not overwritten by player hotbar");
            if(type==17)check(state.view().revision()==before&&state.view().slots().size()==1,"player full/delta ignored by a menu with no embedded player");
            before=state.view().revision();state.accept(0x6e,new byte[]{36,1,22,0,0});
            check(state.view().revision()==before,"player armor namespace never aliases specialized local slots");
            try{state.accept(0x12,full(5,totals[type]+1,0,0));throw new AssertionError("wrong total accepted for "+type);}
            catch(IOException expected){checks++;}
            check(!state.view().known(),"wrong layout revokes the complete baseline");
            if(type>=7&&totals[type]>1){
                state.accept(0x3c,new byte[]{5,(byte)type,8,0,0});state.accept(0x12,full(5,totals[type],1,0));
                try{new InventoryTransfer(state.view(),0,1,1,state.view().revision());throw new AssertionError("observing a menu silently enabled transfer for "+type);}
                catch(IllegalArgumentException expected){checks++;}
            }
        }
        InventoryState unknown=new InventoryState();unknown.accept(0x3c,new byte[]{5,25,8,0,0});unknown.accept(0x12,full(5,38,0,0));
        check(!unknown.view().known(),"unregistered menu25 remains unknown");
        InventoryState mount=new InventoryState();mount.accept(0x12,full(0,46,1,0));long epoch=mount.view().epoch();
        mount.accept(0x2a,new byte[]{5,0,0,0,0,42});
        check(!mount.view().known()&&mount.view().epoch()>epoch,"separate mount-screen packet invalidates the player baseline");
        mount.accept(0x12,full(5,38,1,0));mount.accept(0x12,playerFull());
        check(!mount.view().known(),"unsourced mount layout cannot be restored by either mount or player full");
        mount.accept(0x11,new byte[]{5});mount.accept(0x12,full(0,46,1,0));
        check(mount.view().known()&&mount.view().containerId()==0,"closing mount requires and accepts a fresh player baseline");
        for(byte[] invalid:new byte[][]{{0,0,0,0,0,42},{5,0,-1,-1,-1,-1},{5,0,0,0},{5,0,0,0,0,42,0}}){
            mount.accept(0x12,full(0,46,1,0));
            try{mount.accept(0x2a,invalid);throw new AssertionError("malformed mount packet accepted");}catch(IOException expected){checks++;}
            check(!mount.view().known(),"malformed mount packet revokes the old baseline atomically");
        }
        System.out.println("MENU_LAYOUT_OBSERVATION_PASS "+checks);
    }
}
