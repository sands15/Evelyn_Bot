package dev.evelyn.minecraft;

import java.lang.reflect.InvocationTargetException;
import java.util.Arrays;
import java.util.HexFormat;

/** Fixed payloads from the exact official 26.3 codecs; no socket or world is used. */
public final class PlayerActionPacketsTest {
    private static Class<?> type;
    private static Class<?> actionType;
    private static int checks;
    private static final Class<?>[] USE = {int.class,int.class,float.class,float.class};
    private static final Class<?>[] BLOCK = {int.class,int.class,int.class,int.class,int.class,float.class,float.class,float.class,boolean.class,boolean.class,int.class};

    public static void main(String[] args) throws Exception {
        try { type = Class.forName("dev.evelyn.minecraft.PlayerActionPackets"); }
        catch (ClassNotFoundException missing) { throw new AssertionError("typed 26.3 action packets are not implemented", missing); }
        actionType = Class.forName("dev.evelyn.minecraft.PlayerActionPackets$DigAction");
        selectSlot(); useItem(); blockAndDig(); inputAndCopies();
        System.out.println("PLAYER_ACTION_PACKETS_PASS " + checks);
    }

    private static void selectSlot() throws Exception {
        packet(invoke("selectSlot", new Class[]{int.class}, 0), 0x36, "0000");
        packet(invoke("selectSlot", new Class[]{int.class}, 8), 0x36, "0008");
        reject(() -> invoke("selectSlot", new Class[]{int.class}, -1));
        reject(() -> invoke("selectSlot", new Class[]{int.class}, 9));
    }

    private static void useItem() throws Exception {
        packet(use(0,300,90,-45), 0x43, "00ac0242b40000c2340000");
        packet(use(1,0,-360,90), 0x43, "0100c3b4000042b40000");
        packet(use(0,0,-0f,0f), 0x43, "00008000000000000000");
        reject(() -> use(-1,0,0,0)); reject(() -> use(2,0,0,0)); reject(() -> use(0,-1,0,0));
        reject(() -> use(0,0,Float.NaN,0)); reject(() -> use(0,0,Float.POSITIVE_INFINITY,0));
        reject(() -> use(0,0,0,Float.NaN)); reject(() -> use(0,0,0,Float.NEGATIVE_INFINITY));
        reject(() -> use(0,0,360.01f,0)); reject(() -> use(0,0,-360.01f,0));
        reject(() -> use(0,0,0,90.01f)); reject(() -> use(0,0,0,-90.01f));
    }

    private static void blockAndDig() throws Exception {
        packet(block(0,0,0,1,0,.5f,1,.25f,false,false,7), 0x42,
            "000000000000000000013f0000003f8000003e800000000007");
        packet(block(1,64,-2,5,1,0,.5f,1,true,true,128), 0x42,
            "010000007fffffe04005000000003f0000003f80000001018001");
        packet(block(-1,-64,2,0,0,0,0,0,false,false,0), 0x42,
            "00ffffffc000002fc000000000000000000000000000000000");
        String[] names = {"START_DESTROY_BLOCK","CHANGE_DESTROY_DIRECTION","ABORT_DESTROY_BLOCK","STOP_DESTROY_BLOCK",
            "DROP_ALL_ITEMS","DROP_ITEM","RELEASE_USE_ITEM","SWAP_ITEM_WITH_OFFHAND","STAB"};
        String[] codes = {"00","01","02","03","04","05","06","07","08"};
        for (int i=0;i<names.length;i++) packet(dig(names[i],1,64,-2,5,300),0x29,codes[i]+"0000007fffffe04005ac02");
        packet(invoke("releaseUse", new Class[]{int.class},0),0x29,"0600000000000000000000");
        packet(invoke("swapHands", new Class[]{int.class},0),0x29,"0700000000000000000000");
        packet(invoke("drop", new Class[]{boolean.class,int.class},true,0),0x29,"0400000000000000000000");
        packet(invoke("drop", new Class[]{boolean.class,int.class},false,0),0x29,"0500000000000000000000");
        reject(() -> dig(null,0,0,0,0,0));
        reject(() -> dig(names[0],0,0,0,-1,0)); reject(() -> dig(names[0],0,0,0,6,0));
        reject(() -> dig(names[0],0,0,0,0,-1));
        for (int[] xyz : new int[][]{{30000001,0,0},{-30000001,0,0},{0,0,30000001},{0,0,-30000001},{0,2048,0},{0,-2049,0}}) {
            reject(() -> dig(names[0],xyz[0],xyz[1],xyz[2],0,0));
            reject(() -> block(xyz[0],xyz[1],xyz[2],0,0,0,0,0,false,false,0));
        }
        dig(names[0],30000000,2047,-30000000,0,0); checks++;
        dig(names[0],-30000000,-2048,30000000,5,Integer.MAX_VALUE); checks++;
        reject(() -> block(0,0,0,6,0,0,0,0,false,false,0));
        reject(() -> block(0,0,0,0,2,0,0,0,false,false,0));
        reject(() -> block(0,0,0,0,0,0,0,0,false,false,-1));
        for (float bad : new float[]{-.001f,1.001f,Float.NaN,Float.POSITIVE_INFINITY}) {
            reject(() -> block(0,0,0,0,0,bad,0,0,false,false,0));
            reject(() -> block(0,0,0,0,0,0,bad,0,false,false,0));
            reject(() -> block(0,0,0,0,0,0,0,bad,false,false,0));
        }
    }

    private static void inputAndCopies() throws Exception {
        packet(invoke("closeContainer",new Class[]{int.class},300),0x13,"ac02");
        reject(() -> invoke("closeContainer",new Class[]{int.class},-1));
        Class<?>[] flags = {boolean.class,boolean.class,boolean.class,boolean.class,boolean.class,boolean.class,boolean.class};
        packet(invoke("playerInput",flags,false,false,false,false,false,false,false),0x2b,"00");
        packet(invoke("playerInput",flags,true,false,false,false,true,false,true),0x2b,"51");
        packet(invoke("playerInput",flags,true,true,true,true,true,true,true),0x2b,"7f");
        for (int i=0;i<7;i++) {
            Object[] values={false,false,false,false,false,false,false}; values[i]=true;
            packet(invoke("playerInput",flags,values),0x2b,new String[]{"01","02","04","08","10","20","40"}[i]);
        }
        Object p=invoke("selectSlot",new Class[]{int.class},8);
        byte[] first=payload(p); first[1]=0;
        check(payload(p)[1]==8,"packet accessor must not expose mutable encoded bytes");
        var ctor=p.getClass().getDeclaredConstructor(int.class,byte[].class);
        byte[] source={0,8}; Object copied=ctor.newInstance(0x36,source); source[1]=0;
        check(payload(copied)[1]==8,"packet constructor must copy owned encoded bytes");
    }

    private static Object use(int hand,int sequence,float yaw,float pitch)throws Exception{return invoke("useItem",USE,hand,sequence,yaw,pitch);}
    private static Object block(int x,int y,int z,int face,int hand,float hx,float hy,float hz,boolean inside,boolean border,int sequence)throws Exception {
        return invoke("useBlock",BLOCK,x,y,z,face,hand,hx,hy,hz,inside,border,sequence);
    }
    @SuppressWarnings({"unchecked","rawtypes"})
    private static Object dig(String action,int x,int y,int z,int face,int sequence)throws Exception {
        Object value=action==null?null:Enum.valueOf((Class)actionType,action);
        return invoke("dig",new Class[]{actionType,int.class,int.class,int.class,int.class,int.class},value,x,y,z,face,sequence);
    }
    private static Object invoke(String name,Class<?>[] signature,Object... args)throws Exception {
        try{return type.getDeclaredMethod(name,signature).invoke(null,args);}
        catch(InvocationTargetException failure){if(failure.getCause() instanceof Exception e)throw e;throw failure;}
    }
    private static byte[] payload(Object packet)throws Exception{return(byte[])packet.getClass().getDeclaredMethod("payload").invoke(packet);}
    private static void packet(Object packet,int id,String hex)throws Exception {
        check((int)packet.getClass().getDeclaredMethod("id").invoke(packet)==id,"registered packet id");
        check(Arrays.equals(payload(packet),HexFormat.of().parseHex(hex)),"exact payload: "+hex);
    }
    private static void check(boolean value,String why){if(!value)throw new AssertionError(why);checks++;}
    private static void reject(Checked action)throws Exception {
        try{action.run();}catch(IllegalArgumentException expected){checks++;return;}
        throw new AssertionError("invalid action accepted");
    }
    interface Checked{void run()throws Exception;}
}
