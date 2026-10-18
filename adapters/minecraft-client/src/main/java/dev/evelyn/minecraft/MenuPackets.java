package dev.evelyn.minecraft;

import java.nio.ByteBuffer;
import java.util.Arrays;
import java.util.Map;
import java.util.Set;

/** Exact 26.3 menu intents; the connection owner validates the current menu and observes actual effects. */
final class MenuPackets {
    private MenuPackets() { }
    enum MouseButton {
        LEFT(0),RIGHT(1);
        private final int wireId;
        MouseButton(int wireId){this.wireId=wireId;}
    }
    enum Input {
        PICKUP(0),QUICK_MOVE(1),SWAP(2),CLONE(3),THROW(4),QUICK_CRAFT(5),PICKUP_ALL(6);
        private final int wireId;
        Input(int wireId){this.wireId=wireId;}
    }
    record Carried(int itemId,int count,Map<Integer,Integer> added,Set<Integer> removed){
        Carried{
            if(added==null||removed==null||count<0||count>99||count==0&&itemId!=-1||count>0&&(itemId<1||itemId>=1658)
                    ||count==0&&(!added.isEmpty()||!removed.isEmpty())||added.size()+removed.size()>122)throw invalid();
            added=Map.copyOf(added);removed=Set.copyOf(removed);
            for(int type:added.keySet())if(type<0||type>=122||removed.contains(type))throw invalid();
            for(int type:removed)if(type<0||type>=122)throw invalid();
        }
        static Carried empty(){return new Carried(-1,0,Map.of(),Set.of());}
        Carried withCount(int value){return value==0?empty():new Carried(itemId,value,added,removed);}
        void write(ByteBuffer out){
            out.put((byte)(count==0?0:1));if(count==0)return;
            varInt(out,itemId);varInt(out,count);varInt(out,added.size());
            for(int type:new java.util.TreeSet<>(added.keySet())){varInt(out,type);out.putInt(added.get(type));}
            varInt(out,removed.size());for(int type:new java.util.TreeSet<>(removed))varInt(out,type);
        }
    }

    static PlayerActionPackets.Packet pickup(int containerId,int stateId,int slot,MouseButton button) {
        return pickup(containerId,stateId,slot,button,Carried.empty());
    }
    static PlayerActionPackets.Packet pickup(int containerId,int stateId,int slot,MouseButton button,Carried carried) {
        if(button==null)throw invalid();
        return click(containerId,stateId,slot,button.wireId,Input.PICKUP,carried);
    }

    static PlayerActionPackets.Packet click(int containerId,int stateId,int slot,int button,Input input) {
        return click(containerId,stateId,slot,button,input,Carried.empty());
    }
    private static PlayerActionPackets.Packet click(int containerId,int stateId,int slot,int button,Input input,Carried carried) {
        nonnegative(containerId);
        if(stateId<0||stateId>32767||input==null||carried==null||(slot!=-999&&(slot<0||slot>32767)))throw invalid();
        switch(input){
            case PICKUP -> {if(button<0||button>1)throw invalid();}
            case QUICK_MOVE,THROW,PICKUP_ALL -> {if(slot<0||button<0||button>1)throw invalid();}
            case SWAP -> {if(slot<0||!((button>=0&&button<=8)||button==40))throw invalid();}
            case CLONE -> {if(slot<0||button!=2)throw invalid();}
            case QUICK_CRAFT -> {
                int stage=button&3,type=button>>2;
                if(button<0||type>2||stage>2||(stage==1?slot<0:slot!=-999))throw invalid();
            }
        }
        ByteBuffer out=ByteBuffer.allocate(2048);varInt(out,containerId);varInt(out,stateId);
        out.putShort((short)slot).put((byte)button).put((byte)input.wireId);
        // Report the observed cursor, so the server sends every actual change, including a final empty cursor.
        out.put((byte)0);carried.write(out);
        return encoded(0x12,out);
    }

    static PlayerActionPackets.Packet buttonClick(int containerId,int buttonId) {
        nonnegative(containerId);nonnegative(buttonId);
        ByteBuffer out=ByteBuffer.allocate(10);varInt(out,containerId);varInt(out,buttonId);
        return encoded(0x11,out);
    }

    static PlayerActionPackets.Packet placeRecipe(int containerId,int recipeDisplayId,boolean useMaxItems) {
        nonnegative(containerId);nonnegative(recipeDisplayId);
        ByteBuffer out=ByteBuffer.allocate(11);varInt(out,containerId);varInt(out,recipeDisplayId);out.put((byte)(useMaxItems?1:0));
        return encoded(0x27,out);
    }

    private static void nonnegative(int value){if(value<0)throw invalid();}
    private static IllegalArgumentException invalid(){return new IllegalArgumentException("menu_packet_parameters_invalid");}
    private static void varInt(ByteBuffer out,int value){while((value&~0x7f)!=0){out.put((byte)((value&0x7f)|0x80));value>>>=7;}out.put((byte)value);}
    private static PlayerActionPackets.Packet encoded(int id,ByteBuffer out){return new PlayerActionPackets.Packet(id,Arrays.copyOf(out.array(),out.position()));}
}
