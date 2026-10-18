package dev.evelyn.minecraft;

import java.io.IOException;
import java.util.List;

public final class BlockObservationControlTest {
    static int checks;
    static void check(boolean value,String name){checks++;if(!value)throw new AssertionError(name);}
    static int block(ControlFixture f){return f.connection.world.sample(List.of(new ChunkWorld.BlockPos(10,1,8))).blocks().get(0).stateId();}
    static void vl(java.io.DataOutputStream out,long value)throws IOException{do{int b=(int)(value&127);value>>>=7;out.writeByte(b|(value==0?0:128));}while(value!=0);}
    public static void main(String[] args)throws Exception{
        try(ControlFixture f=new ControlFixture(true,true)){
            check(f.control.acknowledgedBlocksThrough()==-1,"new connection receipt unknown");
            f.handle(ControlFixture.packet(4,d->ControlFixture.vi(d,300)));f.handle(ControlFixture.packet(4,d->ControlFixture.vi(d,1)));
            check(f.control.acknowledgedBlocksThrough()==300&&f.control.resultCode().equals("off"),"cumulative receipt is not command completion");
            f.handle(ControlFixture.packet(5,d->{ControlFixture.vi(d,42);d.writeLong(0);d.writeByte(255);}));
            check(f.control.acknowledgedBlocksThrough()==300&&f.control.resultCode().equals("off"),"crack clear not own mining completion");
            f.handle(ControlFixture.block(10,1,8,1));check(block(f)==1,"block decoder uses existing terrain owner");
            f.handle(ControlFixture.packet(0x56,d->{d.writeLong(0);ControlFixture.vi(d,2);vl(d,(2L<<12)|0xa81);vl(d,(3L<<12)|0xa81);}));
            check(block(f)==3,"section duplicate order applies last state in single world");
            f.handle(new byte[]{0});f.handle(ControlFixture.block(10,1,8,4));check(block(f)==3,"world bundle hides intermediate state");
            f.handle(new byte[]{0});check(block(f)==4,"world bundle publishes final state");
            f.connection.close();check(f.control.acknowledgedBlocksThrough()==-1,"close discards old receipt watermark");
        }
        for(int malformed:new int[]{0,1,2})try(ControlFixture f=new ControlFixture(true,true)){
            f.handle(ControlFixture.packet(4,d->ControlFixture.vi(d,5)));
            byte[] packet=malformed==0?new byte[]{4,6,0}:malformed==1?ControlFixture.block(10,1,8,35723):
                ControlFixture.packet(0x56,d->{d.writeLong(0);ControlFixture.vi(d,2);vl(d,(2L<<12)|0xa81);vl(d,(35723L<<12)|0xa82);});
            try{f.handle(packet);throw new AssertionError("malformed player block packet accepted");}catch(IOException expected){checks++;}
            check(f.connection.snapshot().stage()==VanillaConnection.Stage.CLOSED&&f.control.acknowledgedBlocksThrough()==-1,"malformed packet closes current owner and clears receipt");
        }
        try(ControlFixture f=new ControlFixture(true,false)){
            f.handle(ControlFixture.block(10,1,8,35723));
            check(f.connection.snapshot().stage()==VanillaConnection.Stage.PLAY&&block(f)==35723,"v4 block parser availability preserved; catalog still treats state unknown");
        }
        System.out.println("BlockObservationControlTest PASS "+checks);
    }
}
