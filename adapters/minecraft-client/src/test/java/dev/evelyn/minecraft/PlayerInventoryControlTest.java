package dev.evelyn.minecraft;

import com.google.gson.JsonObject;
import java.io.IOException;
import java.io.OutputStream;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

/** Real receiver and send/authority boundary against owned immediate or delayed server replies. */
public final class PlayerInventoryControlTest {
    private static int checks;
    private static void check(boolean value,String label){if(!value)throw new AssertionError(label);checks++;}
    private static JsonObject command(ControlFixture f,int amount){
        JsonObject c=new JsonObject();c.addProperty("seq",1);c.addProperty("action","inventory_transfer");c.addProperty("run_ms",10000);
        c.addProperty("source_slot",36);c.addProperty("destination_slot",37);c.addProperty("amount",amount);
        c.addProperty("inventory_revision",f.control.observation().getAsJsonObject("inventory").get("revision").getAsLong());return c;
    }
    static byte[] content(int state,int source,int destination,int cursor)throws IOException {
        return ControlFixture.packet(0x12,d->{ControlFixture.vi(d,0);ControlFixture.vi(d,state);ControlFixture.vi(d,46);
            for(int slot=0;slot<46;slot++)stack(d,slot==36?source:slot==37?destination:0);stack(d,cursor);});
    }
    private static void stack(java.io.DataOutputStream d,int count)throws IOException {
        ControlFixture.vi(d,count);if(count>0){ControlFixture.vi(d,1);ControlFixture.vi(d,0);ControlFixture.vi(d,0);}
    }
    private static AtomicInteger outgoing(ControlFixture f,int amount,int interruption)throws Exception {
        AtomicInteger clicks=new AtomicInteger();
        ControlFixture.set(f.connection,"out",new OutputStream(){
            @Override public void write(int value){f.output.write(value);}
            @Override public void write(byte[] bytes,int offset,int length)throws IOException {
                f.output.write(bytes,offset,length);VanillaConnection.Cursor frame=new VanillaConnection.Cursor(java.util.Arrays.copyOfRange(bytes,offset,offset+length));
                int size=frame.vi(),id=frame.vi();if(size!=frame.bytes.available()+1)throw new AssertionError("synthetic frame length");
                if(id==0x12){
                    int click=clicks.incrementAndGet();if(interruption==1){f.control.fence(1);return;}
                    try{
                        if(interruption==2){f.handle(ControlFixture.packet(0x3c,d->{ControlFixture.vi(d,1);ControlFixture.vi(d,0);d.writeByte(8);d.writeUTF("");}));return;}
                        if(interruption==3){f.handle(ControlFixture.packet(0x2a,d->{ControlFixture.vi(d,1);ControlFixture.vi(d,0);d.writeInt(42);}));return;}
                        if(interruption==4&&click==1)f.handle(new byte[]{0});
                        if(click==1)f.handle(content(click+1,0,0,8));
                        else if(amount==8)f.handle(content(click+1,0,8,0));
                        else if(click<=amount+1)f.handle(content(click+1,0,click-1,9-click));
                        else f.handle(content(click+1,8-amount,amount,0));
                    }catch(Exception failure){throw new IOException(failure);}
                }else if(id!=0x2c&&id!=0x2b&&id!=0x0d)throw new AssertionError("unexpected gameplay packet "+id);
            }
        });return clicks;
    }
    public static void main(String[] args)throws Exception {
        for(int amount:new int[]{8,3})try(ControlFixture f=new ControlFixture(true,true)){
            f.handle(content(1,8,0,0));AtomicInteger clicks=outgoing(f,amount,0);f.control.start(command(f,amount),()->true);
            String result=null;for(int tick=0;tick<12&&result==null;tick++)result=f.control.poll();
            check("succeeded".equals(result)&&"inventory_transfer_observed".equals(f.control.resultCode()),"server source/destination/cursor completion");
            check(clicks.get()==(amount==8?2:amount+2),"one committed intent for every observed step");
            int bytes=f.output.size();f.control.poll();check(f.output.size()==bytes,"terminal poll does not replay");
        }
        try(ControlFixture f=new ControlFixture(true,true)){
            f.handle(content(1,8,0,0));f.control.start(command(f,8),()->true);check(f.control.poll()==null,"pending server outcome");
            int bytes=f.output.size();for(int i=0;i<4;i++)check(f.control.poll()==null&&f.output.size()==bytes,"no retry without observed effect");
            f.handle(content(2,8,0,0));check(f.control.poll()==null&&f.output.size()==bytes,"server no-op is not success or replay");
            f.control.stop();check(f.connection.snapshot().stage()==VanillaConnection.Stage.CLOSED&&f.output.size()==bytes,"stop closes with no rollback clicks");
        }
        try(ControlFixture f=new ControlFixture(true,true)){
            f.handle(content(1,8,0,0));AtomicBoolean permit=new AtomicBoolean(true);f.control.start(command(f,8),permit::get);permit.set(false);
            check("failed".equals(f.control.poll())&&f.output.size()==0,"revoked owner sends nothing");
        }
        try(ControlFixture f=new ControlFixture(true,true)){
            f.handle(content(1,8,0,0));f.control.start(command(f,8),()->true);f.handle(content(2,7,0,0));
            check("failed".equals(f.control.poll())&&f.output.size()==0,"changed inventory before first write fenced");
        }
        for(int interruption:new int[]{1,2,3})try(ControlFixture f=new ControlFixture(true,true)){
            f.handle(content(1,8,0,0));AtomicInteger clicks=outgoing(f,8,interruption);f.control.start(command(f,8),()->true);
            try{f.control.poll();throw new AssertionError("cancel or menu change during write accepted");}catch(IOException expected){checks++;}
            check(clicks.get()==1&&f.connection.snapshot().stage()==VanillaConnection.Stage.CLOSED,"mid-write cancellation or menu change closes exact owner");
            int bytes=f.output.size();f.control.poll();check(f.output.size()==bytes,"no followup after partial write outcome");
        }
        for(boolean water:new boolean[]{false,true})try(ControlFixture f=new ControlFixture(true,true)){
            f.handle(content(1,8,0,0));
            if(water)f.handle(ControlFixture.block(8,1,8,89));
            else {JsonObject look=new JsonObject();look.addProperty("seq",1);look.addProperty("action","look");look.addProperty("run_ms",1000);look.addProperty("yaw",0);look.addProperty("pitch",0);f.control.start(look,()->true);}
            f.control.start(command(f,8),()->true);
            check("failed".equals(f.control.poll())&&f.output.size()==0,"water or unsettled movement cannot start transfer");
            check(f.connection.snapshot().stage()==VanillaConnection.Stage.CLOSED,"transfer without physics authority closes owner");
        }
        try(ControlFixture f=new ControlFixture(true,true)){
            f.handle(content(1,8,0,0));f.control.start(command(f,8),()->true);f.control.poll();int bytes=f.output.size();
            f.handle(ControlFixture.block(8,1,8,89));
            check("failed".equals(f.control.poll())&&f.output.size()==bytes,"pending transfer cannot continue after fluid change");
            check(f.connection.snapshot().stage()==VanillaConnection.Stage.CLOSED,"loss of stable ground closes pending owner");
        }
        for(String patch:new String[]{"0100183fcccccd01bf050100","01002c00","01000780000000","01003680000000","010005000180000000","01001d0180000000"})try(ControlFixture f=new ControlFixture(true,true)){
            f.handle(ControlFixture.packet(0x12,d->{ControlFixture.vi(d,0);ControlFixture.vi(d,1);ControlFixture.vi(d,46);
                for(int slot=0;slot<46;slot++){ControlFixture.vi(d,slot==36?8:0);if(slot==36){ControlFixture.vi(d,1);d.write(java.util.HexFormat.of().parseHex(patch));}}
                ControlFixture.vi(d,0);}));
            f.control.start(command(f,8),()->true);
            check("failed".equals(f.control.poll())&&"inventory_transfer_hash_unavailable".equals(f.control.resultCode())&&f.output.size()==0,"unavailable hash cannot pick up any items");
            check(f.connection.controlReady(),"safe preflight rejection preserves dry connection and untouched inventory");
        }
        for(int trigger:new int[]{2,3})try(ControlFixture f=new ControlFixture(true,true)){
            f.handle(content(1,8,0,0));AtomicInteger calls=new AtomicInteger(),clicks=outgoing(f,8,0);
            f.control.start(command(f,8),()->{
                if(calls.incrementAndGet()==trigger)try{f.handle(new byte[]{0});}catch(Exception e){throw new AssertionError(e);}
                return true;
            });
            check("failed".equals(f.control.poll())&&f.output.size()==0&&clicks.get()==0,"prewrite bundle after guard callback fences pickup "+trigger);
        }
        try(ControlFixture f=new ControlFixture(true,true)){
            f.handle(content(1,8,0,0));AtomicInteger clicks=outgoing(f,8,4);f.control.start(command(f,8),()->true);
            check(f.control.poll()==null&&f.connection.controlSuspended()&&clicks.get()==1,"postcommit bundle keeps observed cursor pending");
            check(f.control.poll()==null&&clicks.get()==1,"open response bundle grants no next click");
            f.handle(new byte[]{0});String result=null;for(int n=0;n<4&&result==null;n++)result=f.control.poll();
            check("succeeded".equals(result)&&clicks.get()==2,"normal response bundle completes exact transfer");
        }
        System.out.println("PlayerInventoryControlTest PASS "+checks);
    }
}
