package dev.evelyn.minecraft;

import java.io.IOException;

/** Only a complete supported inventory baseline and an observed selection establish an empty hand. */
final class BareHandInventory {
    private boolean[] occupied;
    private int selected=-1;
    String hand(){return selected<0||occupied==null?"unknown":occupied[selected]?"occupied":"empty";}
    void clear(){occupied=null;selected=-1;}
    void accept(int id,VanillaConnection.Cursor c)throws IOException {
        try {
            if(id==0x6b){int slot=c.count(8);c.end();selected=slot;return;}
            boolean[] next=occupied==null?null:occupied.clone();
            if(id==0x12){
                int container=c.count(Integer.MAX_VALUE);c.count(Integer.MAX_VALUE);int size=c.count(4096);
                if(container!=0){c.discard(1048576);return;}
                if(size!=46)throw new VanillaConnection.Failure("unsupported_inventory_layout");
                next=new boolean[9];for(int i=0;i<size;i++){boolean item=stack(c);if(i>=36&&i<=44)next[i-36]=item;}
                stack(c);
            }else if(id==0x14){
                int container=c.count(Integer.MAX_VALUE);c.count(Integer.MAX_VALUE);int slot=c.data.readShort();
                if(container!=0){c.discard(1048576);return;}
                if(slot<0||slot>=46)throw new VanillaConnection.Failure("invalid_inventory_slot");
                boolean item=stack(c);if(next!=null&&slot>=36&&slot<=44)next[slot-36]=item;
            }else if(id==0x6e){
                int slot=c.count(40);boolean item=stack(c);if(next!=null&&slot<9)next[slot]=item;
            }else throw new IllegalArgumentException("inventory_packet");
            c.end();occupied=next;
        }catch(UnknownComponent unsupported){occupied=null;c.discard(1048576);}
        catch(IOException|RuntimeException malformed){occupied=null;throw malformed;}
    }
    private static boolean stack(VanillaConnection.Cursor c)throws IOException {
        int count=c.count(99);if(count==0)return false;
        c.count(65535);int added=c.count(256),removed=c.count(256);
        // shortcut: component values have type-dependent lengths; add reviewed codecs before accepting patched stacks.
        if(added!=0||removed!=0)throw new UnknownComponent();return true;
    }
    private static final class UnknownComponent extends IOException {}
}
