package dev.evelyn.minecraft;

/** One non-predicting swap; both server-observed hands are required, never a dispatch-only success. */
final class HandSwap {
    record Step(long revision) {}
    private final long inventoryEpoch,selectionGeneration,initialRevision;
    private final int selected,mainSlot;
    private final InventoryState.StackView main,off;
    private Step prepared;
    private boolean awaiting;
    private long committedRevision;
    private String status="running",code="hand_swap_running";

    HandSwap(InventoryState.View initial,int selected,long selectionGeneration,long expectedRevision){
        if(selected<0||selected>8||selectionGeneration<0||selectionGeneration>Protocol.MAX_SEQUENCE
                ||initial==null||!initial.known()||initial.containerId()!=0||initial.menuTypeId()!=null
                ||initial.stateId()<0||initial.stateId()>32767||initial.slots().size()!=46
                ||initial.carried()==null||initial.carried().count()!=0||initial.revision()!=expectedRevision)
            throw new IllegalArgumentException("hand_swap_invalid");
        this.selected=selected;this.selectionGeneration=selectionGeneration;mainSlot=36+selected;
        main=initial.slots().get(mainSlot);off=initial.slots().get(45);
        if(same(main,off))throw new IllegalArgumentException("hand_swap_unobservable");
        inventoryEpoch=initial.epoch();initialRevision=initial.revision();committedRevision=initialRevision;
    }
    private static boolean same(InventoryState.StackView a,InventoryState.StackView b){
        return a!=null&&b!=null&&a.itemId()==b.itemId()&&a.count()==b.count()&&a.wirePatchIdentity().equals(b.wirePatchIdentity());
    }
    private boolean context(InventoryState.View current,int selected,long generation){
        return current!=null&&current.known()&&current.epoch()==inventoryEpoch&&current.containerId()==0&&current.menuTypeId()==null
                &&current.slots().size()==46&&current.carried()!=null&&current.carried().count()==0
                &&this.selected==selected&&selectionGeneration==generation;
    }
    Step prepare(InventoryState.View current,int selected,long generation){
        if(!status.equals("running"))return null;
        if(!context(current,selected,generation)||current.revision()<committedRevision){invalidate("inventory_changed");return null;}
        InventoryState.StackView actualMain=current.slots().get(mainSlot),actualOff=current.slots().get(45);
        if(awaiting){
            if(current.revision()>committedRevision&&same(actualMain,off)&&same(actualOff,main)){
                status="succeeded";code="hand_swap_observed";
            }else if(!(same(actualMain,main)||same(actualMain,off))||!(same(actualOff,off)||same(actualOff,main)))invalidate("hand_swap_conflict");
            return null;
        }
        if(current.revision()!=initialRevision||!same(actualMain,main)||!same(actualOff,off)){invalidate("inventory_changed");return null;}
        if(prepared==null)prepared=new Step(current.revision());
        return prepared;
    }
    boolean valid(Step step,InventoryState.View current,int selected,long generation){
        return step!=null&&prepared==step&&!awaiting&&status.equals("running")&&context(current,selected,generation)
                &&current.revision()==step.revision()&&same(current.slots().get(mainSlot),main)&&same(current.slots().get(45),off);
    }
    void commit(Step step,InventoryState.View current,int selected,long generation){
        if(!valid(step,current,selected,generation)){invalidate("hand_swap_commit_invalidated");throw new IllegalStateException(code);}
        committedRevision=step.revision();awaiting=true;prepared=null;
    }
    boolean sameLifetime(InventoryState.View current,int selected,long generation){return status.equals("running")&&context(current,selected,generation);}
    void invalidate(String reason){if(status.equals("running")){status="failed";code=reason;prepared=null;}}
    String status(){return status;}
    String code(){return code;}
}
