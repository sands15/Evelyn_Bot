package dev.evelyn.minecraft;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Test-only stone pickup server: independent decoding, actual arithmetic and official remote-delta rules. */
final class PlayerInventoryServer {
    record Snapshot(int stateId,int source,int destination,int carried,int clicks,boolean paused) { }
    private record Prediction(int itemId,int count,Map<Integer,Integer> additions,Set<Integer> removals) {
        boolean matches(int actual){return count==actual&&(actual==0||itemId==1&&additions.isEmpty()&&removals.isEmpty());}
    }
    private int stateId=3,source=8,destination,carried,clicks;
    private int remoteSource=8,remoteDestination;
    private final boolean enforcePriorCarried;
    private boolean paused,complete;

    PlayerInventoryServer(){this(true);}
    /** Relaxed mode exists only to reproduce the old wrong prediction accepted by the real server. */
    PlayerInventoryServer(boolean enforcePriorCarried){this.enforcePriorCarried=enforcePriorCarried;}

    Snapshot snapshot(){return new Snapshot(stateId,source,destination,carried,clicks,paused);}
    void pauseResponses(){if(clicks!=0)throw new IllegalStateException("pause_before_click_only");paused=true;}
    String report(){return "PLAYER_SERVER "+stateId+" "+source+" "+destination+" "+carried+" "+clicks+" "+paused;}
    byte[] initialContent()throws IOException {if(clicks!=0)throw invalid();return content();}

    List<byte[]> receive(byte[] frame)throws IOException {
        DataInputStream in=new DataInputStream(new ByteArrayInputStream(frame));
        int size=varInt(in);if(size!=in.available())throw invalid();
        int id=varInt(in);
        // Existing movement maintenance has its own focused tests. This oracle only models menu pickup.
        if(id==0x2c||id==0x2b||id==0x0d)return List.of();
        if(id!=0x12||complete||(paused&&clicks!=0))throw invalid();
        int container=varInt(in),state=varInt(in),slot=in.readShort(),button=in.readUnsignedByte(),input=varInt(in);
        int changedSlots=varInt(in);if(changedSlots!=0)throw invalid();
        Prediction prediction=prediction(in);
        if(in.available()!=0||container!=0||state!=stateId||input!=0
                ||button>1||(slot!=36&&slot!=37))throw invalid();
        // This is an oracle assertion about our sender, not a claim that vanilla rejects wrong predictions.
        if(enforcePriorCarried&&!prediction.matches(carried))throw invalid();
        if(clicks==0){
            if(slot!=36||button!=0)throw invalid();
            carried=source;source=0;
        }else if(slot==37){
            if(carried==0)throw invalid();
            int placed=button==0?Math.min(carried,64-destination):Math.min(1,64-destination);
            destination+=placed;carried-=placed;complete=carried==0;
        }else{
            if(button!=0||destination==0||carried==0)throw invalid();
            source+=carried;carried=0;complete=true;
        }
        if(source+destination+carried!=8||source<0||destination<0||carried<0)throw new AssertionError("synthetic conservation");
        clicks++;
        // Empty changed-slot predictions leave the prior remote slot cache intact. Each sent slot increments state.
        List<byte[]> responses=new ArrayList<>();
        if(source!=remoteSource){responses.add(slot(36,source));remoteSource=source;}
        if(destination!=remoteDestination){responses.add(slot(37,destination));remoteDestination=destination;}
        // setRemoteCarried(prediction) occurs after clicked; compare that hash to the POST-click actual cursor.
        if(!prediction.matches(carried))responses.add(cursor());
        return paused?List.of():List.copyOf(responses);
    }

    private static Prediction prediction(DataInputStream in)throws IOException {
        int present=in.readUnsignedByte();if(present==0)return new Prediction(0,0,Map.of(),Set.of());
        if(present!=1)throw invalid();
        int item=varInt(in),count=varInt(in);if(item<1||item>1657||count<1||count>99)throw invalid();
        int added=varInt(in);if(added>122)throw invalid();Map<Integer,Integer> additions=new HashMap<>();
        for(int n=0;n<added;n++){
            int type=varInt(in);if(type>121||additions.putIfAbsent(type,in.readInt())!=null)throw invalid();
        }
        int removed=varInt(in);if(removed>122||added+removed>122)throw invalid();Set<Integer> removals=new HashSet<>();
        for(int n=0;n<removed;n++){
            int type=varInt(in);if(type>121||!removals.add(type)||additions.containsKey(type))throw invalid();
        }
        return new Prediction(item,count,Map.copyOf(additions),Set.copyOf(removals));
    }

    private byte[] slot(int index,int count)throws IOException {
        stateId=(stateId+1)&32767;
        ByteArrayOutputStream bytes=new ByteArrayOutputStream();DataOutputStream out=new DataOutputStream(bytes);
        out.writeByte(0x14);varInt(out,0);varInt(out,stateId);out.writeShort(index);stack(out,count);return bytes.toByteArray();
    }
    private byte[] cursor()throws IOException {
        ByteArrayOutputStream bytes=new ByteArrayOutputStream();DataOutputStream out=new DataOutputStream(bytes);
        out.writeByte(0x62);stack(out,carried);return bytes.toByteArray();
    }

    private byte[] content()throws IOException {
        ByteArrayOutputStream bytes=new ByteArrayOutputStream();DataOutputStream out=new DataOutputStream(bytes);
        out.writeByte(0x12);varInt(out,0);varInt(out,stateId);varInt(out,46);
        for(int slot=0;slot<46;slot++)stack(out,slot==36?source:slot==37?destination:0);
        stack(out,carried);return bytes.toByteArray();
    }
    private static void stack(DataOutputStream out,int count)throws IOException {
        varInt(out,count);if(count!=0){varInt(out,1);varInt(out,0);varInt(out,0);}
    }
    private static int varInt(DataInputStream in)throws IOException {
        int value=0;
        for(int shift=0;shift<35;shift+=7){
            int part=in.readUnsignedByte();if(shift==28&&(part&0xf0)!=0)throw invalid();
            value|=(part&0x7f)<<shift;
            if((part&0x80)==0){if(value<0||(shift!=0&&part==0))throw invalid();return value;}
        }
        throw invalid();
    }
    private static void varInt(DataOutputStream out,int value)throws IOException {
        while((value&~127)!=0){out.writeByte((value&127)|128);value>>>=7;}out.writeByte(value);
    }
    private static IOException invalid(){return new IOException("synthetic_inventory_packet_invalid");}
}
