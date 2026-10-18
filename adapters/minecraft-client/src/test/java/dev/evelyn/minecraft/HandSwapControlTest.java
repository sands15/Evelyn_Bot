package dev.evelyn.minecraft;

import com.google.gson.JsonObject;
import java.io.IOException;
import java.io.OutputStream;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

/** Real single writer/receiver; only deltas acknowledge actual changed hands. */
public final class HandSwapControlTest {
    static int checks;
    static void check(boolean value,String name){checks++;if(!value)throw new AssertionError(name);}
    static void stack(java.io.DataOutputStream out,int item,int count)throws IOException{ControlFixture.vi(out,count);if(count>0){ControlFixture.vi(out,item);ControlFixture.vi(out,0);ControlFixture.vi(out,0);}}
    static byte[] slot(int state,int slot,int item,int count)throws IOException{return ControlFixture.packet(0x14,d->{ControlFixture.vi(d,0);ControlFixture.vi(d,state);d.writeShort(slot);stack(d,item,count);});}
    static void initial(ControlFixture f,boolean identical)throws Exception{
        f.handle(ControlFixture.packet(0x12,d->{ControlFixture.vi(d,0);ControlFixture.vi(d,1);ControlFixture.vi(d,46);
            for(int slot=0;slot<46;slot++)stack(d,slot==36?1:2,slot==36?8:slot==45?(identical?8:3):0);stack(d,0,0);}));
        if(identical)f.handle(slot(2,45,1,8));
        f.handle(ControlFixture.packet(0x6b,d->ControlFixture.vi(d,0)));
    }
    static JsonObject command(ControlFixture f){JsonObject c=new JsonObject();c.addProperty("seq",1);c.addProperty("action","swap_hands");c.addProperty("run_ms",10000);
        c.addProperty("inventory_revision",f.control.observation().getAsJsonObject("inventory").get("revision").getAsLong());return c;}
    static AtomicInteger outgoing(ControlFixture f,int interruption)throws Exception{
        AtomicInteger swaps=new AtomicInteger();
        ControlFixture.set(f.connection,"out",new OutputStream(){
            public void write(int value){throw new AssertionError("fragmented synthetic frame");}
            public void write(byte[] bytes,int offset,int length)throws IOException{
                f.output.write(bytes,offset,length);VanillaConnection.Cursor c=new VanillaConnection.Cursor(java.util.Arrays.copyOfRange(bytes,offset,offset+length));c.vi();int id=c.vi();
                if(id==0x29){
                    check(c.vi()==7&&c.data.readLong()==0&&c.data.readUnsignedByte()==0&&c.vi()==0,"exact official SWAP action");c.end();swaps.incrementAndGet();
                    try{
                        if(interruption==1){f.control.fence(1);return;}
                        if(interruption==2){f.handle(ControlFixture.packet(0x3c,d->{ControlFixture.vi(d,1);ControlFixture.vi(d,0);d.writeByte(8);d.writeUTF("");}));return;}
                        if(interruption==3){f.handle(ControlFixture.packet(0x6b,d->ControlFixture.vi(d,0)));return;}
                        if(interruption==4){f.handle(ControlFixture.block(8,1,8,89));return;}
                        if(interruption==0||interruption==6){if(interruption==6)f.handle(new byte[]{0});f.handle(slot(2,36,2,3));f.handle(slot(3,45,1,8));}
                    }catch(Exception failure){throw new IOException(failure);}
                }else if(id!=0x2c&&id!=0x2b&&id!=0x0d)throw new AssertionError("unexpected gameplay packet "+id);
            }
        });return swaps;
    }
    public static void main(String[] args)throws Exception{
        try(ControlFixture f=new ControlFixture(true,true)){
            initial(f,false);AtomicInteger swaps=outgoing(f,0);f.control.start(command(f),()->true);
            check(f.control.poll()==null,"write is not completion");
            check("succeeded".equals(f.control.poll())&&f.control.resultCode().equals("hand_swap_observed"),"both immediate received deltas complete");
            check(swaps.get()==1,"exactly one swap");int bytes=f.output.size();f.control.poll();check(f.output.size()==bytes,"terminal no replay");
        }
        try(ControlFixture f=new ControlFixture(true,true)){
            initial(f,false);AtomicInteger swaps=outgoing(f,5);f.control.start(command(f),()->true);f.control.poll();int bytes=f.output.size();
            for(int n=0;n<3;n++)check(f.control.poll()==null&&f.output.size()==bytes,"no acknowledgement no replay");
            f.handle(slot(2,36,2,3));check(f.control.poll()==null&&f.output.size()==bytes,"one hand is insufficient");
            f.handle(slot(3,45,1,8));check("succeeded".equals(f.control.poll())&&swaps.get()==1,"delayed both-hands effect");
        }
        for(int interruption:new int[]{1,2,3,4})try(ControlFixture f=new ControlFixture(true,true)){
            initial(f,false);AtomicInteger swaps=outgoing(f,interruption);f.control.start(command(f),()->true);
            try{f.control.poll();throw new AssertionError("invalid midwrite authority accepted");}catch(IOException expected){checks++;}
            check(swaps.get()==1&&f.connection.snapshot().stage()==VanillaConnection.Stage.CLOSED,"midwrite invalidation closes exact owner "+interruption);
            int bytes=f.output.size();f.control.poll();check(f.output.size()==bytes,"midwrite unknown never replays");
        }
        try(ControlFixture f=new ControlFixture(true,true)){
            initial(f,false);AtomicBoolean allowed=new AtomicBoolean(true);f.control.start(command(f),allowed::get);allowed.set(false);
            check("failed".equals(f.control.poll())&&f.output.size()==0,"revoked authority no send");
        }
        try(ControlFixture f=new ControlFixture(true,true)){
            initial(f,true);f.control.start(command(f),()->true);
            check("failed".equals(f.control.poll())&&f.output.size()==0&&f.control.resultCode().equals("hand_swap_unobservable"),"identical hands preflight no mutation");
        }
        for(int change:new int[]{0,1})try(ControlFixture f=new ControlFixture(true,true)){
            initial(f,false);f.control.start(command(f),()->true);
            if(change==0)f.handle(slot(2,10,1,1));else f.handle(ControlFixture.packet(0x6b,d->ControlFixture.vi(d,0)));
            check("failed".equals(f.control.poll())&&f.output.size()==0,"revision or selection generation fence beforewrite");
        }
        try(ControlFixture f=new ControlFixture(true,true)){
            initial(f,false);f.handle(ControlFixture.block(8,1,8,89));f.control.start(command(f),()->true);
            check("failed".equals(f.control.poll())&&f.output.size()==0&&f.connection.snapshot().stage()==VanillaConnection.Stage.CLOSED,"no new physics lease in water");
        }
        for(int trigger:new int[]{2,3})try(ControlFixture f=new ControlFixture(true,true)){
            initial(f,false);AtomicInteger calls=new AtomicInteger(),swaps=outgoing(f,5);
            f.control.start(command(f),()->{
                if(calls.incrementAndGet()==trigger)try{f.handle(new byte[]{0});}catch(Exception e){throw new AssertionError(e);}
                return true;
            });
            check("failed".equals(f.control.poll())&&f.output.size()==0&&swaps.get()==0,"prewrite bundle after guard callback fences swap "+trigger);
        }
        try(ControlFixture f=new ControlFixture(true,true)){
            initial(f,false);AtomicInteger swaps=outgoing(f,6);f.control.start(command(f),()->true);
            check(f.control.poll()==null&&f.connection.controlSuspended()&&swaps.get()==1,"postcommit bundled replies remain allowed");
            check(f.control.poll()==null,"no further input while response bundle is open");
            f.handle(new byte[]{0});check("succeeded".equals(f.control.poll()),"completed response bundle retains actual swap result");
        }
        System.out.println("HandSwapControlTest PASS "+checks);
    }
}
