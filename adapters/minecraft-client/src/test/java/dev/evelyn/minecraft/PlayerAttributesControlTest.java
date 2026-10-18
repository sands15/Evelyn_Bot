package dev.evelyn.minecraft;

import java.io.IOException;
import java.util.List;

/** Exercises the actual PLAY receiver using owned bytes; never opens a Minecraft socket. */
public final class PlayerAttributesControlTest {
    static int checks;
    static void check(boolean value,String message){checks++;if(!value)throw new AssertionError(message);}
    record Mod(String key,double amount,int operation){}
    record Attribute(int id,double base,List<Mod> modifiers){Attribute(int id,double base){this(id,base,List.of());}}
    static byte[] packet(int entity,Attribute... values)throws IOException{
        return ControlFixture.packet(0x86,d->{
            ControlFixture.vi(d,entity);ControlFixture.vi(d,values.length);
            for(Attribute value:values){
                ControlFixture.vi(d,value.id);d.writeDouble(value.base);ControlFixture.vi(d,value.modifiers.size());
                for(Mod modifier:value.modifiers){ControlFixture.string(d,modifier.key);d.writeDouble(modifier.amount);ControlFixture.vi(d,modifier.operation);}
            }
        });
    }
    public static void main(String[] args)throws Exception{
        try(ControlFixture f=new ControlFixture(true,true)){
            var initial=f.control.attributeSnapshot();
            check(initial.entityId()==42&&!initial.received()&&initial.entries().isEmpty(),"login readiness creates no received attributes/defaults");
            f.handle(packet(42,new Attribute(1,1,List.of(new Mod("test:armor",2,0))),new Attribute(2,3)));
            var first=f.control.attributeSnapshot();
            check(first.received()&&first.entries().size()==2&&first.entry(26).isEmpty(),"only received attribute IDs become known");
            check(first.entry(1).orElseThrow().base()==1&&first.entry(1).orElseThrow().modifiers().size()==1,"raw base and complete modifiers retained");
            check(first.entry(1).orElseThrow().origin().equals("SERVER_OBSERVED"),"raw server origin distinct from reference/effective");
            f.handle(packet(42,new Attribute(1,4)));
            var next=f.control.attributeSnapshot();
            check(next.revision()>first.revision()&&next.entry(2).orElseThrow().base()==3,"partial updates preserve omitted attributes");
            check(next.entry(1).orElseThrow().modifiers().isEmpty()&&first.entry(1).orElseThrow().modifiers().size()==1,"complete modifier replacement and immutable prior view");
            f.handle(packet(99,new Attribute(1,9,List.of(new Mod("test:foreign",1,2)))));
            check(f.control.attributeSnapshot()==next,"fully parsed foreign attributes do not change self state");
            f.handle(packet(42,new Attribute(26,(double).1f,List.of(new Mod("test:zero",0,2)))));
            check(f.control.attributeSnapshot().entry(26).orElseThrow().modifiers().size()==1,"legacy zero modifier preservation gate remains valid");
            f.connection.close();
            check(f.control.attributeSnapshot().entityId()==null&&!f.control.attributeSnapshot().received(),"owner close invalidates raw observations");
        }
        for(int failure=0;failure<4;failure++)try(ControlFixture f=new ControlFixture(true,true)){
            f.handle(packet(42,new Attribute(1,1)));
            byte[] malformed=switch(failure){
                case 0->packet(42,new Attribute(1,2),new Attribute(1,3));
                case 1->packet(99,new Attribute(1,Double.NaN));
                case 2->packet(42,new Attribute(26,.2));
                default->packet(42,new Attribute(1,2,List.of(new Mod("test:bad",1,3))));
            };
            try{f.handle(malformed);throw new AssertionError("invalid attributes accepted");}catch(IOException expected){checks++;}
            check(f.connection.snapshot().stage()==VanillaConnection.Stage.CLOSED&&!f.control.attributeSnapshot().received()&&f.control.attributeSnapshot().entries().isEmpty(),"parse/physics failure closes owner and clears raw state");
        }
        try(ControlFixture f=new ControlFixture(true,false)){
            check(f.control.attributeSnapshot()==null,"v4 does not acquire new raw observation model");
            f.handle(packet(42,new Attribute(26,(double).1f)));
            check(f.connection.snapshot().stage()==VanillaConnection.Stage.PLAY,"legacy normal physics preserved");
            try{f.handle(packet(42,new Attribute(26,.2)));throw new AssertionError("legacy physics widened");}catch(IOException expected){checks++;}
        }
        System.out.println("PlayerAttributesControlTest PASS "+checks);
    }
}
