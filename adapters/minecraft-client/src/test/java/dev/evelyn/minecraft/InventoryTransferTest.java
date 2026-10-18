package dev.evelyn.minecraft;

import java.io.ByteArrayOutputStream;
import java.io.IOException;

/** Server observations drive every next click; no prediction, game, or socket. */
public final class InventoryTransferTest {
    private static int checks;
    private static void check(boolean value,String label){if(!value)throw new AssertionError(label);checks++;}
    private static void reject(Runnable action){checks++;try{action.run();}catch(IllegalArgumentException expected){return;}throw new AssertionError("invalid transfer admitted");}
    public static void main(String[] args)throws Exception {
        InventoryState state=state(8,0,0);long revision=state.view().revision();
        InventoryTransfer transfer=new InventoryTransfer(state.view(),36,37,8,revision);
        InventoryTransfer.Click source=transfer.prepare(state.view());
        check(source.slot()==36&&!source.right()&&source.containerId()==0&&source.stateId()==1,"first source pickup");
        check(source.carried().count()==0,"first click reports actually observed empty cursor");
        check(transfer.valid(source,state.view()),"exact prepared owner");transfer.commit(source,state.view());
        check(transfer.prepare(state.view())==null&&transfer.status().equals("running"),"no repeat while server outcome pending");
        update(state,0,0,8);InventoryTransfer.Click target=transfer.prepare(state.view());
        check(target.slot()==37&&!target.right(),"observed cursor unlocks destination");transfer.commit(target,state.view());
        check(target.carried().itemId()==1&&target.carried().count()==8&&target.carried().added().isEmpty(),"final placement reports actually observed nonempty cursor");
        check(transfer.prepare(state.view())==null,"no speculative completion");update(state,0,8,0);
        check(transfer.prepare(state.view())==null&&transfer.status().equals("succeeded"),"full transfer observed");
        check(!transfer.valid(target,state.view()),"completed click cannot be reused");

        state=state(8,0,0);transfer=new InventoryTransfer(state.view(),36,37,3,state.view().revision());
        source=transfer.prepare(state.view());transfer.commit(source,state.view());update(state,0,0,8);
        for(int placed=1;placed<=3;placed++){
            target=transfer.prepare(state.view());check(target.slot()==37&&target.right(),"partial transfer uses single item intent");
            transfer.commit(target,state.view());check(transfer.prepare(state.view())==null,"no next click before observation");
            update(state,0,placed,8-placed);
        }
        target=transfer.prepare(state.view());check(target.slot()==36&&!target.right(),"return exact remainder");
        transfer.commit(target,state.view());update(state,5,3,0);transfer.prepare(state.view());
        check(transfer.status().equals("succeeded"),"partial transfer observes source remainder, destination and empty cursor");

        InventoryState normal=state(8,0,0);InventoryState.View view=normal.view();
        for(int[] argsCase:new int[][]{{36,36,1},{5,37,1},{36,45,1},{36,37,0},{36,37,9},{37,36,1}})
            reject(()->new InventoryTransfer(view,argsCase[0],argsCase[1],argsCase[2],view.revision()));
        reject(()->new InventoryTransfer(view,36,37,1,view.revision()-1));
        InventoryState occupied=state(8,1,0);reject(()->new InventoryTransfer(occupied.view(),36,37,1,occupied.view().revision()));
        InventoryState cursor=state(8,0,1);reject(()->new InventoryTransfer(cursor.view(),36,37,1,cursor.view().revision()));
        InventoryState unknown=new InventoryState();reject(()->new InventoryTransfer(unknown.view(),36,37,1,unknown.view().revision()));

        InventoryTransfer stale=new InventoryTransfer(view,36,37,8,view.revision());source=stale.prepare(view);
        update(normal,7,0,0);check(!stale.valid(source,normal.view()),"changed revision before write fenced");
        stale.prepare(normal.view());check(stale.status().equals("failed"),"uncommitted changed intent fails");
        InventoryState pending=state(8,0,0);InventoryTransfer waiting=new InventoryTransfer(pending.view(),36,37,8,pending.view().revision());
        source=waiting.prepare(pending.view());waiting.commit(source,pending.view());update(pending,0,0,7);
        check(waiting.prepare(pending.view())==null&&waiting.status().equals("running"),"wrong cursor cannot unlock next click");
        pending.clear();waiting.prepare(pending.view());check(waiting.status().equals("failed"),"unknown inventory invalidates pending action");
        check(!waiting.valid(source,pending.view()),"no followup after invalidation");
        for(boolean reusedMenu:new boolean[]{false,true}){
            InventoryState changed=new InventoryState();if(reusedMenu)changed.accept(0x3c,new byte[]{1,0,8,0,0});
            updateMenu(changed,reusedMenu?1:0,reusedMenu?45:46,8,0,0);
            InventoryTransfer old=new InventoryTransfer(changed.view(),36,37,8,changed.view().revision());
            InventoryTransfer.Click first=old.prepare(changed.view());old.commit(first,changed.view());
            if(reusedMenu)changed.accept(0x3c,new byte[]{1,0,8,0,0});else changed.clear();
            updateMenu(changed,reusedMenu?1:0,reusedMenu?45:46,0,0,8);
            check(old.prepare(changed.view())==null&&old.status().equals("failed"),"new baseline lifetime cannot inherit an old pending click even with the same menu ID");
            check(!old.valid(first,changed.view()),"old lifetime never regains commit authority");
        }
        InventoryState exhausted=state(8,0,0);var epoch=InventoryState.class.getDeclaredField("epoch");epoch.setAccessible(true);epoch.setLong(exhausted,Integer.MAX_VALUE);
        exhausted.clear();try{update(exhausted,8,0,0);throw new AssertionError("exhausted lifetime restored");}catch(IOException expected){checks++;}
        check(!exhausted.view().known(),"lifetime saturation never restores an old known baseline");
        InventoryState removed=patched("000103");
        InventoryTransfer removalTransfer=new InventoryTransfer(removed.view(),36,37,8,removed.view().revision());
        check(removed.view().slots().get(36).hash().removed().equals(java.util.Set.of(3))&&removalTransfer.prepare(removed.view())!=null,"removed-only patches retain truthful hash without component-value guesses");
        InventoryState scalar=patched("01000301");
        check(scalar.view().slots().get(36).hash()!=null&&scalar.view().slots().get(36).hash().added().containsKey(3),"confirmed scalar patch receives canonical hash");
        for(String patch:new String[]{"0100183fcccccd01bf050100","01002c00","01000780000000","01003680000000","010005000180000000","01001d0180000000"}){
            InventoryState unsupportedHash=patched(patch);
            check(unsupportedHash.view().known()&&unsupportedHash.view().slots().get(36).hash()==null,"unavailable or invalid persistent hash leaves parsed inventory known");
            try{new InventoryTransfer(unsupportedHash.view(),36,37,8,unsupportedHash.view().revision());throw new AssertionError("unknown hash transfer admitted");}
            catch(IllegalArgumentException expected){check("inventory_transfer_hash_unavailable".equals(expected.getMessage()),"unavailable hash rejected before first pickup");}
        }
        System.out.println("InventoryTransferTest PASS "+checks);
    }
    private static InventoryState patched(String patch)throws IOException {
        ByteArrayOutputStream out=new ByteArrayOutputStream();vi(out,0);vi(out,1);vi(out,46);
        for(int slot=0;slot<46;slot++){vi(out,slot==36?8:0);if(slot==36){vi(out,1);out.writeBytes(java.util.HexFormat.of().parseHex(patch));}}
        vi(out,0);InventoryState state=new InventoryState();state.accept(0x12,out.toByteArray());return state;
    }
    private static InventoryState state(int source,int destination,int carried)throws IOException {
        InventoryState state=new InventoryState();update(state,source,destination,carried);return state;
    }
    private static void update(InventoryState state,int source,int destination,int carried)throws IOException {
        updateMenu(state,0,46,source,destination,carried);
    }
    private static void updateMenu(InventoryState state,int container,int count,int source,int destination,int carried)throws IOException {
        ByteArrayOutputStream out=new ByteArrayOutputStream();vi(out,container);vi(out,(int)(state.view().revision()+1)&32767);vi(out,count);
        for(int slot=0;slot<count;slot++)stack(out,slot==36?source:slot==37?destination:0);stack(out,carried);state.accept(0x12,out.toByteArray());
    }
    private static void stack(ByteArrayOutputStream out,int count){vi(out,count);if(count>0){vi(out,1);vi(out,0);vi(out,0);}}
    private static void vi(ByteArrayOutputStream out,int value){do{int b=value&127;value>>>=7;out.write(b|(value==0?0:128));}while(value!=0);}
}
