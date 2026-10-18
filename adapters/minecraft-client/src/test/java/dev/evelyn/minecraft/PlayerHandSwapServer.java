package dev.evelyn.minecraft;

import java.io.IOException;
import java.util.Arrays;
import java.util.HexFormat;
import java.util.List;

/** Independent fixed-literal swap oracle; actual arithmetic precedes slot-only responses. */
final class PlayerHandSwapServer {
    // Official PlayerAction.SWAP: action7, BlockPos.ZERO, direction0, prediction sequence0.
    private static final byte[] SWAP=HexFormat.of().parseHex("0c290700000000000000000000");
    private int state=3,mainItem=1,mainCount=8,offItem=2,offCount=3,swaps;
    private boolean paused;
    void pause(){if(swaps!=0)throw new IllegalStateException("pause_before_swap_only");paused=true;}
    String report(){return "PLAYER_SWAP_SERVER "+state+" "+mainItem+" "+mainCount+" "+offItem+" "+offCount+" "+swaps+" "+paused;}
    byte[] initialContent()throws IOException{
        if(swaps!=0)throw new IOException("initial_only");
        return ControlFixture.packet(0x12,d->{ControlFixture.vi(d,0);ControlFixture.vi(d,state);ControlFixture.vi(d,46);
            for(int slot=0;slot<46;slot++)stack(d,slot==36?mainItem:offItem,slot==36?mainCount:slot==45?offCount:0);stack(d,0,0);});
    }
    List<byte[]> receive(byte[] frame)throws IOException{
        if(Arrays.equals(frame,new byte[]{1,0x2c})||Arrays.equals(frame,new byte[]{2,0x2b,0})||Arrays.equals(frame,new byte[]{1,0x0d}))return List.of();
        if(!Arrays.equals(frame,SWAP)||swaps!=0)throw new IOException("synthetic_swap_packet_invalid");
        int item=mainItem,count=mainCount;mainItem=offItem;mainCount=offCount;offItem=item;offCount=count;swaps++;
        byte[] main=slot(36,mainItem,mainCount),off=slot(45,offItem,offCount);
        return paused?List.of():List.of(main,off);
    }
    private byte[] slot(int index,int item,int count)throws IOException{
        state=(state+1)&32767;
        return ControlFixture.packet(0x14,d->{ControlFixture.vi(d,0);ControlFixture.vi(d,state);d.writeShort(index);stack(d,item,count);});
    }
    private static void stack(java.io.DataOutputStream out,int item,int count)throws IOException{
        ControlFixture.vi(out,count);if(count>0){ControlFixture.vi(out,item);ControlFixture.vi(out,0);ControlFixture.vi(out,0);}
    }
}
