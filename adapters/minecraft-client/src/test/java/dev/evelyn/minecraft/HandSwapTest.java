package dev.evelyn.minecraft;

import java.util.ArrayList;
import java.util.List;

public final class HandSwapTest {
    static int checks;
    static void check(boolean value,String name){checks++;if(!value)throw new AssertionError(name);}
    static void reject(Runnable call){try{call.run();throw new AssertionError("accepted invalid swap");}catch(IllegalArgumentException expected){checks++;}}
    static final InventoryState.StackView EMPTY=new InventoryState.StackView(-1,0,"",MenuPackets.Carried.empty());
    static final InventoryState.StackView A=new InventoryState.StackView(1,8,"patch-a",null),B=new InventoryState.StackView(2,3,"patch-b",null);
    static InventoryState.View view(long revision,long epoch,InventoryState.StackView main,InventoryState.StackView off){
        List<InventoryState.StackView> slots=new ArrayList<>();for(int n=0;n<46;n++)slots.add(EMPTY);
        slots.set(38,main);slots.set(45,off);return new InventoryState.View(revision,epoch,true,0,3,null,slots,EMPTY);
    }
    public static void main(String[] args){
        for(InventoryState.StackView off:List.of(EMPTY,B)){
            InventoryState.View initial=view(10,2,A,off);HandSwap swap=new HandSwap(initial,2,7,10);
            HandSwap.Step step=swap.prepare(initial,2,7);check(step!=null,"prepared");
            check(swap.prepare(initial,2,7)==step,"same prepared identity");
            check(swap.valid(step,initial,2,7),"exact beforewrite");swap.commit(step,initial,2,7);
            check(!swap.valid(step,initial,2,7),"committed cannot replay");
            check(swap.prepare(initial,2,7)==null&&swap.status().equals("running"),"no response remains pending");
            InventoryState.View noOp=view(11,2,A,off);
            check(swap.prepare(noOp,2,7)==null&&swap.status().equals("running"),"fresh no-op not success");
            check(swap.prepare(view(12,2,off,off),2,7)==null&&swap.status().equals("running"),"one delta insufficient");
            check(swap.prepare(view(13,2,off,A),2,7)==null&&swap.status().equals("succeeded"),"both actual hands swapped");
            check(swap.code().equals("hand_swap_observed"),"effect receipt");
            check(swap.prepare(view(14,2,A,off),2,7)==null&&swap.status().equals("succeeded"),"terminal never replay");
        }
        InventoryState.View initial=view(10,2,A,B);
        reject(()->new HandSwap(initial,2,7,9));reject(()->new HandSwap(initial,-1,7,10));reject(()->new HandSwap(initial,9,7,10));
        reject(()->new HandSwap(view(10,2,A,A),2,7,10));reject(()->new HandSwap(view(10,2,EMPTY,EMPTY),2,7,10));
        reject(()->new HandSwap(new InventoryState.View(10,2,false,0,3,null,List.of(),null),2,7,10));
        reject(()->new HandSwap(new InventoryState.View(10,2,true,1,3,0,initial.slots(),EMPTY),2,7,10));
        reject(()->new HandSwap(new InventoryState.View(10,2,true,0,3,null,initial.slots(),A),2,7,10));
        reject(()->new HandSwap(new InventoryState.View(10,2,true,0,3,null,initial.slots().subList(0,45),EMPTY),2,7,10));
        for(int fault=0;fault<7;fault++){
            HandSwap swap=new HandSwap(initial,2,7,10);HandSwap.Step step=swap.prepare(initial,2,7);swap.commit(step,initial,2,7);
            InventoryState.View changed=switch(fault){
                case 0->view(11,3,B,A);
                case 1->view(9,2,B,A);
                case 2->view(11,2,new InventoryState.StackView(2,2,"patch-b",null),A);
                case 3->view(11,2,new InventoryState.StackView(2,3,"changed-patch",null),A);
                default->view(11,2,B,A);
            };
            int selected=fault==4?3:2;long generation=fault==5?9:7;
            if(fault==6)changed=new InventoryState.View(11,2,true,0,4,null,changed.slots(),A);
            check(swap.prepare(changed,selected,generation)==null&&swap.status().equals("failed"),"pending fault "+fault);
        }
        HandSwap prewrite=new HandSwap(initial,2,7,10);HandSwap.Step step=prewrite.prepare(initial,2,7);
        check(!prewrite.valid(step,view(11,2,A,B),2,7),"fresh unrelated revision invalid beforewrite");
        check(!prewrite.valid(step,initial,2,8),"same selection new generation invalid beforewrite");
        check(!prewrite.valid(new HandSwap.Step(10),initial,2,7),"forged step invalid");
        prewrite.invalidate("off");check(prewrite.prepare(initial,2,7)==null&&prewrite.code().equals("off"),"off terminal");
        HandSwap reversed=new HandSwap(initial,2,7,10);HandSwap.Step reverse=reversed.prepare(initial,2,7);reversed.commit(reverse,initial,2,7);
        check(reversed.prepare(view(11,2,A,A),2,7)==null&&reversed.status().equals("running"),"offhand delta first insufficient");
        check(reversed.prepare(view(12,2,B,A),2,7)==null&&reversed.status().equals("succeeded"),"offhand then main success");
        System.out.println("HandSwapTest PASS "+checks);
    }
}
