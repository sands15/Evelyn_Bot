package dev.evelyn.minecraft;

import java.lang.reflect.InvocationTargetException;
import java.util.Arrays;
import java.util.HexFormat;

/** Literal official 26.3 packets; encoding is not evidence that a menu action succeeded. */
public final class MenuPacketsTest {
    private static Class<?> type,input,mouse;
    private static int checks;
    public static void main(String[] args)throws Exception {
        try{type=Class.forName("dev.evelyn.minecraft.MenuPackets");}
        catch(ClassNotFoundException missing){throw new AssertionError("typed 26.3 menu packets are not implemented",missing);}
        input=Class.forName("dev.evelyn.minecraft.MenuPackets$Input");mouse=Class.forName("dev.evelyn.minecraft.MenuPackets$MouseButton");
        packet(pickup(0,7,36,"LEFT"),0x12,"0007002400000000");
        packet(pickup(0,7,36,"RIGHT"),0x12,"0007002401000000");
        packet(pickup(1,300,-999,"RIGHT"),0x12,"01ac02fc1901000000");
        packet(pickup(Integer.MAX_VALUE,32767,32767,"LEFT"),0x12,"ffffffff07ffff017fff00000000");
        String[] names={"PICKUP","QUICK_MOVE","SWAP","CLONE","THROW","QUICK_CRAFT","PICKUP_ALL"};
        int[] buttons={0,0,40,2,1,1,0};String[] hex={"0007002400000000","0007002400010000","0007002428020000","0007002402030000","0007002401040000","0007002401050000","0007002400060000"};
        for(int i=0;i<names.length;i++)packet(click(0,7,36,buttons[i],names[i]),0x12,hex[i]);
        for(int[] values:new int[][]{{0,-999},{1,36},{2,-999},{4,-999},{5,36},{6,-999},{8,-999},{9,36},{10,-999}}) {
            String prefix=values[1]==-999?"0007fc19":"00070024";
            packet(click(0,7,values[1],values[0],"QUICK_CRAFT"),0x12,prefix+String.format("%02x",values[0])+"050000");
        }
        packet(call("buttonClick",new Class[]{int.class,int.class},300,32768),0x11,"ac02808002");
        packet(call("placeRecipe",new Class[]{int.class,int.class,boolean.class},1,300,false),0x27,"01ac0200");
        packet(call("placeRecipe",new Class[]{int.class,int.class,boolean.class},0,0,true),0x27,"000001");
        reject(()->pickup(-1,0,0,"LEFT"));reject(()->pickup(0,-1,0,"LEFT"));reject(()->pickup(0,32768,0,"LEFT"));
        reject(()->pickup(0,0,-1,"LEFT"));reject(()->pickup(0,0,-1000,"LEFT"));reject(()->pickup(0,0,32768,"LEFT"));reject(()->pickup(0,0,0,null));
        reject(()->click(0,0,0,0,null));
        for(String name:new String[]{"PICKUP","QUICK_MOVE","THROW","PICKUP_ALL"}){reject(()->click(0,0,0,-1,name));reject(()->click(0,0,0,2,name));}
        for(String name:new String[]{"QUICK_MOVE","SWAP","CLONE","THROW","PICKUP_ALL"})reject(()->click(0,0,-999,name.equals("CLONE")?2:0,name));
        for(int button:new int[]{-1,9,39,41,127,128})reject(()->click(0,0,0,button,"SWAP"));
        for(int button:new int[]{0,1,3})reject(()->click(0,0,0,button,"CLONE"));
        for(int button:new int[]{-1,3,7,11,12,127})reject(()->click(0,0,0,button,"QUICK_CRAFT"));
        for(int button:new int[]{0,2,4,6,8,10})reject(()->click(0,0,36,button,"QUICK_CRAFT"));
        for(int button:new int[]{1,5,9})reject(()->click(0,0,-999,button,"QUICK_CRAFT"));
        reject(()->call("buttonClick",new Class[]{int.class,int.class},-1,0));reject(()->call("buttonClick",new Class[]{int.class,int.class},0,-1));
        reject(()->call("placeRecipe",new Class[]{int.class,int.class,boolean.class},-1,0,false));reject(()->call("placeRecipe",new Class[]{int.class,int.class,boolean.class},0,-1,false));
        Object value=pickup(0,7,36,"LEFT");byte[] exposed=payload(value);exposed[0]=99;
        check(payload(value)[0]==0,"immutable encoded payload ownership");
        var carried=new MenuPackets.Carried(1,8,java.util.Map.of(),java.util.Set.of());
        packet(MenuPackets.pickup(0,7,37,MenuPackets.MouseButton.LEFT,carried),0x12,"000700250000000101080000");
        packet(MenuPackets.pickup(0,7,37,MenuPackets.MouseButton.LEFT,new MenuPackets.Carried(1,8,java.util.Map.of(),java.util.Set.of(3))),0x12,"00070025000000010108000103");
        packet(MenuPackets.pickup(0,7,37,MenuPackets.MouseButton.LEFT,new MenuPackets.Carried(1,8,java.util.Map.of(3,0x01020304),java.util.Set.of())),0x12,"0007002500000001010801030102030400");
        reject(()->new MenuPackets.Carried(1,0,java.util.Map.of(),java.util.Set.of()));
        reject(()->new MenuPackets.Carried(-1,1,java.util.Map.of(),java.util.Set.of()));
        reject(()->new MenuPackets.Carried(0,1,java.util.Map.of(),java.util.Set.of()));
        reject(()->new MenuPackets.Carried(1658,1,java.util.Map.of(),java.util.Set.of()));
        reject(()->new MenuPackets.Carried(1,100,java.util.Map.of(),java.util.Set.of()));
        reject(()->new MenuPackets.Carried(1,1,java.util.Map.of(122,1),java.util.Set.of()));
        reject(()->new MenuPackets.Carried(1,1,java.util.Map.of(),java.util.Set.of(-1)));
        reject(()->new MenuPackets.Carried(1,1,java.util.Map.of(3,1),java.util.Set.of(3)));
        var hashes=new java.util.HashMap<Integer,Integer>();hashes.put(3,9);var removed=new java.util.HashSet<Integer>();removed.add(4);
        var owned=new MenuPackets.Carried(1,1,hashes,removed);hashes.clear();removed.clear();
        check(owned.added().get(3)==9&&owned.removed().contains(4),"immutable carried patch ownership");
        reject(()->MenuPackets.pickup(0,0,0,MenuPackets.MouseButton.LEFT,null));
        System.out.println("MENU_PACKETS_PASS "+checks);
    }
    @SuppressWarnings({"rawtypes","unchecked"})
    private static Object enumValue(Class<?> type,String name){return name==null?null:Enum.valueOf((Class)type,name);}
    private static Object pickup(int container,int state,int slot,String button)throws Exception{return call("pickup",new Class[]{int.class,int.class,int.class,mouse},container,state,slot,enumValue(mouse,button));}
    private static Object click(int container,int state,int slot,int button,String kind)throws Exception{return call("click",new Class[]{int.class,int.class,int.class,int.class,input},container,state,slot,button,enumValue(input,kind));}
    private static Object call(String name,Class<?>[] signature,Object...args)throws Exception {
        try{return type.getDeclaredMethod(name,signature).invoke(null,args);}
        catch(InvocationTargetException failure){if(failure.getCause() instanceof Exception e)throw e;throw failure;}
    }
    private static byte[] payload(Object packet)throws Exception{return(byte[])packet.getClass().getDeclaredMethod("payload").invoke(packet);}
    private static void packet(Object packet,int id,String hex)throws Exception {
        check((int)packet.getClass().getDeclaredMethod("id").invoke(packet)==id,"known official registered packet id");
        check(Arrays.equals(payload(packet),HexFormat.of().parseHex(hex)),"fixed menu vector "+hex);
    }
    private static void check(boolean value,String why){if(!value)throw new AssertionError(why);checks++;}
    private static void reject(Checked action)throws Exception{try{action.run();}catch(IllegalArgumentException expected){checks++;return;}throw new AssertionError("invalid menu intent accepted");}
    private interface Checked{void run()throws Exception;}
}
