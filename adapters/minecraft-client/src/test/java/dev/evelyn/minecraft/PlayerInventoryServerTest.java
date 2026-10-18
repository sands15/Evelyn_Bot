package dev.evelyn.minecraft;

import java.io.ByteArrayInputStream;
import java.io.DataInputStream;
import java.io.IOException;
import java.util.Arrays;
import java.util.HexFormat;
import java.util.List;

/** Independent fixed click vectors and a small pickup model; no product encoder or cursor. */
public final class PlayerInventoryServerTest {
    private static int checks;
    private static void check(boolean value,String label){if(!value)throw new AssertionError(label);checks++;}
    private static byte[] hex(String value){return HexFormat.of().parseHex(value);}
    private static byte[] framed(String payload){byte[] body=hex(payload),frame=new byte[body.length+1];frame[0]=(byte)body.length;System.arraycopy(body,0,frame,1,body.length);return frame;}
    private static void snapshot(PlayerInventoryServer server,int state,int source,int destination,int carried,int clicks){
        var actual=server.snapshot();
        check(actual.stateId()==state&&actual.source()==source&&actual.destination()==destination
                &&actual.carried()==carried&&actual.clicks()==clicks,"independent pickup state");
    }
    private static void response(byte[] bytes,int state,int source,int destination,int carried)throws IOException {
        DataInputStream in=new DataInputStream(new ByteArrayInputStream(bytes));
        check(in.readUnsignedByte()==0x12&&in.readUnsignedByte()==0&&in.readUnsignedByte()==state&&in.readUnsignedByte()==46,"content header");
        for(int slot=0;slot<46;slot++){
            int count=in.readUnsignedByte();check(count==(slot==36?source:slot==37?destination:0),"actual slot count");
            if(count!=0)check(in.readUnsignedByte()==1&&in.readUnsignedByte()==0&&in.readUnsignedByte()==0,"stone without overrides");
        }
        check(in.readUnsignedByte()==carried,"actual carried count");
        if(carried!=0)check(in.readUnsignedByte()==1&&in.readUnsignedByte()==0&&in.readUnsignedByte()==0,"carried stack");
        check(in.available()==0,"content remaining zero");
    }
    private static void rejects(byte[] frame,String label)throws IOException {
        PlayerInventoryServer server=new PlayerInventoryServer();
        try{server.receive(frame);throw new AssertionError(label);}catch(IOException expected){checks++;}
        snapshot(server,3,8,0,0,0);
    }
    private static void rejectsAfterPickup(byte[] frame,String label)throws IOException {
        PlayerInventoryServer server=new PlayerInventoryServer();server.receive(hex("09120003002400000000"));
        try{server.receive(frame);throw new AssertionError(label);}catch(IOException expected){checks++;}
        snapshot(server,4,0,0,8,1);
    }
    private static void delta(List<byte[]> actual,String... expected){
        check(actual.size()==expected.length,"only required deltas");
        for(int n=0;n<expected.length;n++)check(HexFormat.of().formatHex(actual.get(n)).equals(expected[n]),"literal slot/cursor delta");
        for(byte[] packet:actual)check(packet[0]!=0x12,"never full-content fallback");
    }
    private static final class Client {
        int source=8,destination,carried,state=3;
        void accept(List<byte[]> packets)throws IOException {
            for(byte[] packet:packets){
                DataInputStream in=new DataInputStream(new ByteArrayInputStream(packet));int id=in.readUnsignedByte();
                if(id==0x14){check(in.readUnsignedByte()==0,"delta player container");state=in.readUnsignedByte();int slot=in.readShort(),count=stack(in);
                    if(slot==36)source=count;else if(slot==37)destination=count;else throw new AssertionError("unexpected slot");
                }else if(id==0x62)carried=stack(in);else throw new AssertionError("unexpected delta");
                check(in.available()==0,"delta remaining zero");
            }
        }
        int stack(DataInputStream in)throws IOException {int count=in.readUnsignedByte();if(count!=0)check(in.readUnsignedByte()==1&&in.readUnsignedByte()==0&&in.readUnsignedByte()==0,"delta stone patch");return count;}
    }
    public static void main(String[] args)throws Exception {
        PlayerInventoryServer full=new PlayerInventoryServer();response(full.initialContent(),3,8,0,0);Client client=new Client();
        List<byte[]> pickup=full.receive(hex("09120003002400000000"));delta(pickup,"140004002400","6208010000");client.accept(pickup);snapshot(full,4,0,0,8,1);
        List<byte[]> finish=full.receive(hex("0d12000400250000000101080000"));delta(finish,"140005002508010000","6200");client.accept(finish);snapshot(full,5,0,8,0,2);
        check(client.source==0&&client.destination==8&&client.carried==0&&client.state==5,"full transfer client observes exact postcondition");
        try{finish.add(new byte[0]);throw new AssertionError("mutable response list");}catch(UnsupportedOperationException expected){checks++;}
        try{full.receive(hex("09120005002500000000"));throw new AssertionError("click after terminal");}catch(IOException expected){checks++;}
        PlayerInventoryServer partial=new PlayerInventoryServer();Client portion=new Client();
        portion.accept(partial.receive(hex("09120003002400000000")));
        List<byte[]> one=partial.receive(hex("0d12000400250100000101080000"));delta(one,"140005002501010000","6207010000");portion.accept(one);
        List<byte[]> two=partial.receive(hex("0d12000500250100000101070000"));delta(two,"140006002502010000","6206010000");portion.accept(two);
        List<byte[]> three=partial.receive(hex("0d12000600250100000101060000"));delta(three,"140007002503010000","6205010000");portion.accept(three);
        List<byte[]> returned=partial.receive(hex("0d12000700240000000101050000"));delta(returned,"140008002405010000","6200");portion.accept(returned);snapshot(partial,8,5,3,0,5);
        check(portion.source==5&&portion.destination==3&&portion.carried==0&&portion.state==8,"partial transfer client observes remainder");
        byte[] base=hex("09120003002400000000");
        for(int[] bad:new int[][]{{0,8},{1,0x11},{2,1},{3,2},{4,0x7f},{5,0x25},{6,1},{7,1},{8,1},{9,1}}){
            byte[] value=base.clone();value[bad[0]]=(byte)bad[1];rejects(value,"invalid field "+bad[0]);
        }
        rejects(Arrays.copyOf(base,base.length-1),"truncated frame");
        byte[] trailing=Arrays.copyOf(base,base.length+1);trailing[0]++;rejects(trailing,"trailing click bytes");
        rejects(hex("0d1280808080800003002400000000"),"overlong VarInt");
        for(String bad:new String[]{
                "120004002500000000", // Wrong EMPTY when PRE-click carried is8.
                "120004002500000002", // Noncanonical optional flag.
                "12000400250000000100080000", // Air cannot be a nonempty prediction.
                "12000400250000000101000000", // Zero count cannot be nonempty.
                "12000400250000000101070000", // Wrong current carried count.
                "120004002500000001010801001234567800", // Valid nonempty map, wrong stone patch.
                "1200040025000000010108000100", // Valid removal, wrong stone patch.
                "1200040025000000010108017a1234567800", // Unknown component122.
                "1200040025000000010108020012345678001234567800", // Duplicate addition.
                "120004002500000001010800020000", // Duplicate removal.
                "12000400250000000101080100123456780100", // Addition/removal overlap.
                "120004002500000001010801001234", // Truncated int32 hash.
                "1200040025000000010108000000" // Trailing bytes.
        })rejectsAfterPickup(framed(bad),"invalid prior carried hash");
        PlayerInventoryServer paused=new PlayerInventoryServer();paused.pauseResponses();
        check(paused.receive(base).isEmpty(),"paused effect not published");snapshot(paused,4,0,0,8,1);
        try{paused.receive(hex("0d12000400250000000101080000"));throw new AssertionError("unobserved followup");}catch(IOException expected){checks++;}
        snapshot(paused,4,0,0,8,1);
        check(paused.report().equals("PLAYER_SERVER 4 0 0 8 1 true"),"bounded pause report");
        PlayerInventoryServer maintenance=new PlayerInventoryServer();
        check(maintenance.receive(hex("012c")).isEmpty(),"existing maintenance is ignored");snapshot(maintenance,3,8,0,0,0);
        // Actual vanilla accepts a wrong hash and compares it against POST-click actual state. EMPTY then matches0.
        PlayerInventoryServer oldEmpty=new PlayerInventoryServer(false);Client stale=new Client();stale.accept(oldEmpty.receive(base));
        List<byte[]> oldFinal=oldEmpty.receive(hex("09120004002500000000"));delta(oldFinal,"140005002508010000");stale.accept(oldFinal);
        snapshot(oldEmpty,5,0,8,0,2);check(stale.carried==8&&stale.destination==8,"old EMPTY final leaves client cursor stale8; never force full resync");
        PlayerInventoryServer actualHash=new PlayerInventoryServer(false);
        // Valid map/set wire schema is decoded completely, even when its hash differs from this fixed empty-patch stone.
        delta(actualHash.receive(framed("12000300240000000101080100123456780101")),"140004002400","6208010000");
        System.out.println("PlayerInventoryServerTest PASS "+checks);
    }
}
