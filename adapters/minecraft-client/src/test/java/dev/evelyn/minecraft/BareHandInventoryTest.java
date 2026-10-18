package dev.evelyn.minecraft;

import java.io.IOException;

public final class BareHandInventoryTest {
    private static int checks;
    public static void main(String[] args)throws Exception {
        BareHandInventory inventory=new BareHandInventory();
        check(inventory.hand().equals("unknown"),"selection and baseline start unknown");
        packet(inventory,0x12,full(-1,false));check(inventory.hand().equals("unknown"),"full snapshot cannot assume slot zero");
        packet(inventory,0x6b,new byte[]{8});check(inventory.hand().equals("empty"),"observed selected8 maps to menu44");
        packet(inventory,0x14,ControlFixture.body(d->{d.writeByte(0);d.writeByte(1);d.writeShort(44);d.write(new byte[]{1,1,0,0});}));
        check(inventory.hand().equals("occupied"),"slot44 revokes empty main hand");
        packet(inventory,0x6e,new byte[]{8,0});check(inventory.hand().equals("empty"),"direct slot8 has no menu offset");
        packet(inventory,0x12,full(-1,true));check(inventory.hand().equals("unknown"),"unknown carried component invalidates all earlier empty slots");
        packet(inventory,0x6e,new byte[]{8,0});check(inventory.hand().equals("unknown"),"one delta cannot restore invalidated full baseline");
        packet(inventory,0x12,full(-1,false));check(inventory.hand().equals("empty"),"complete supported baseline restores empty hand");
        packet(inventory,0x12,full(44,false));check(inventory.hand().equals("occupied"),"nonempty default stack recognized");
        packet(inventory,0x14,ControlFixture.body(d->{d.writeByte(1);d.writeByte(0);d.writeShort(44);d.writeByte(0);}));
        check(inventory.hand().equals("occupied"),"foreign container cannot clear player hand");
        reject(()->packet(inventory,0x6b,new byte[]{9}));
        reject(()->packet(inventory,0x12,new byte[]{0,0,46,0}));
        reject(()->packet(inventory,0x6e,new byte[]{8,(byte)255,(byte)255,(byte)255,(byte)255,15}));
        inventory.clear();check(inventory.hand().equals("unknown"),"new binding clears selection and inventory");
        System.out.println("BARE_HAND_INVENTORY_PASS "+checks);
    }
    static byte[] full(int occupied,boolean unknown)throws IOException {
        return ControlFixture.body(d->{d.write(new byte[]{0,0,46});for(int i=0;i<46;i++)d.write(i==occupied?new byte[]{1,1,0,0}:new byte[]{0});
            d.write(unknown?new byte[]{1,1,1,0,99}:new byte[]{0});});
    }
    private static void packet(BareHandInventory i,int id,byte[] body)throws IOException{i.accept(id,new VanillaConnection.Cursor(body));}
    private static void check(boolean value,String why){if(!value)throw new AssertionError(why);checks++;}
    private static void reject(Checked action)throws Exception{try{action.run();}catch(IOException e){checks++;return;}throw new AssertionError("invalid accepted");}
    interface Checked{void run()throws Exception;}
}
