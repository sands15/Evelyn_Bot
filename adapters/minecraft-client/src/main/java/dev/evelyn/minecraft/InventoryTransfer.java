package dev.evelyn.minecraft;

import java.util.ArrayList;
import java.util.List;

/** One explicit transfer; each click waits for matching server source/destination/cursor state. */
final class InventoryTransfer {
    record Click(int containerId,int stateId,int slot,boolean right,long revision,MenuPackets.Carried carried) {}
    private record Instruction(int slot,boolean right,InventoryState.StackView source,InventoryState.StackView destination,InventoryState.StackView carried) {}
    private final int containerId,sourceSlot,destinationSlot,slotCount;
    private final Integer menuTypeId;
    private final long inventoryEpoch;
    private final InventoryState.StackView initialSource,empty=new InventoryState.StackView(-1,0,"",MenuPackets.Carried.empty());
    private final List<Instruction> instructions;
    private long committedRevision;
    private int next;
    private boolean awaiting;
    private Click prepared;
    private String status="running",code="inventory_transfer_running";

    InventoryTransfer(InventoryState.View initial,int source,int destination,int amount,long expectedRevision){
        if(initial==null||!initial.known()||initial.revision()!=expectedRevision||initial.stateId()<0||initial.stateId()>32767
                ||source==destination||!movable(initial,source)||!movable(initial,destination)||initial.carried()==null
                ||initial.carried().count()!=0||initial.slots().get(source).count()<amount||amount<1||amount>99
                ||initial.slots().get(destination).count()!=0)throw new IllegalArgumentException("inventory_transfer_invalid");
        containerId=initial.containerId();menuTypeId=initial.menuTypeId();inventoryEpoch=initial.epoch();sourceSlot=source;destinationSlot=destination;slotCount=initial.slots().size();
        initialSource=initial.slots().get(source);committedRevision=initial.revision();int count=initialSource.count();
        if(initialSource.hash()==null)throw new IllegalArgumentException("inventory_transfer_hash_unavailable");
        List<Instruction> steps=new ArrayList<>();steps.add(new Instruction(source,false,empty,empty,initialSource));
        if(amount==count)steps.add(new Instruction(destination,false,empty,initialSource,empty));
        else {
            for(int n=1;n<=amount;n++)steps.add(new Instruction(destination,true,empty,count(n),count(count-n)));
            steps.add(new Instruction(source,false,count(count-amount),count(amount),empty));
        }
        instructions=List.copyOf(steps);
    }
    private static boolean movable(InventoryState.View state,int slot){
        if(slot<0||slot>=state.slots().size())return false;
        if(state.containerId()==0)return state.menuTypeId()==null&&slot>=9&&slot<45;
        return state.menuTypeId()!=null&&state.menuTypeId()>=0&&state.menuTypeId()<=6;
    }
    private InventoryState.StackView count(int count){return count==0?empty:new InventoryState.StackView(initialSource.itemId(),count,initialSource.wirePatchIdentity(),initialSource.hash().withCount(count));}
    private boolean context(InventoryState.View current){
        return current!=null&&current.known()&&current.epoch()==inventoryEpoch&&current.containerId()==containerId&&java.util.Objects.equals(current.menuTypeId(),menuTypeId)
                &&current.slots().size()==slotCount&&current.carried()!=null;
    }
    private boolean matches(InventoryState.View current,Instruction instruction){
        return current.slots().get(sourceSlot).equals(instruction.source())&&current.slots().get(destinationSlot).equals(instruction.destination())
                &&current.carried().equals(instruction.carried());
    }
    Click prepare(InventoryState.View current){
        if(!status.equals("running"))return null;
        if(!context(current)||current.revision()<committedRevision){invalidate("inventory_changed");return null;}
        if(awaiting){
            if(current.revision()==committedRevision||!matches(current,instructions.get(next)))return null;
            awaiting=false;next++;
            if(next==instructions.size()){status="succeeded";code="inventory_transfer_observed";return null;}
        }
        if(prepared!=null){if(!valid(prepared,current)){invalidate("inventory_changed");return null;}return prepared;}
        boolean expected=next==0?current.revision()==committedRevision&&current.slots().get(sourceSlot).equals(initialSource)
                &&current.slots().get(destinationSlot).equals(empty)&&current.carried().equals(empty):matches(current,instructions.get(next-1));
        if(!expected){invalidate("inventory_changed");return null;}
        Instruction instruction=instructions.get(next);
        if(current.carried().hash()==null){invalidate("inventory_transfer_hash_unavailable");return null;}
        prepared=new Click(containerId,current.stateId(),instruction.slot(),instruction.right(),current.revision(),current.carried().hash());return prepared;
    }
    boolean valid(Click step,InventoryState.View current){
        return step!=null&&status.equals("running")&&!awaiting&&prepared==step&&context(current)
                &&current.revision()==step.revision()&&current.stateId()==step.stateId();
    }
    void commit(Click step,InventoryState.View current){
        if(!valid(step,current)){invalidate("inventory_commit_invalidated");throw new IllegalStateException(code);}
        committedRevision=step.revision();awaiting=true;prepared=null;
    }
    void invalidate(String reason){if(status.equals("running")){status="failed";code=reason;prepared=null;}}
    String status(){return status;}
    String code(){return code;}
    boolean sameLifetime(InventoryState.View current){return status.equals("running")&&context(current);}
}
