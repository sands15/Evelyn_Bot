package dev.evelyn.minecraft;

import java.io.*;
import java.lang.reflect.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

/** Literal configuration registry layouts; memory streams only, never a connection or game entrypoint. */
public final class ComponentRegistryKeysTest {
    private static int checks;
    public static void main(String[] args)throws Exception {
        VanillaConnection connection=fixture();check(connection.componentRegistryKeys().isEmpty(),"no registry names before observation");
        byte[] packet=registry("minecraft:enchantment","test:alpha","test:beta");handle(connection,packet);
        Map<String,List<String>> first=connection.componentRegistryKeys();check(first.equals(Map.of("minecraft:enchantment",List.of("test:alpha","test:beta"))),"IDs follow server list order");
        var observed=ComponentWireCodec.decode(13,HexFormat.of().parseHex("010001"),0,connection.componentRegistries()).value();
        check(ComponentHash.component(13,observed,connection.componentRegistryKeys()).orElseThrow()==-1587611718,"official hash uses names actually retained by producer");
        check(connection.componentRegistries().get("minecraft:enchantment")==2,"existing count consumer preserved");
        immutable(()->first.put("test:x",List.of()));immutable(()->first.get("minecraft:enchantment").set(0,"test:forged"));immutable(()->first.get("minecraft:enchantment").clear());
        Arrays.fill(packet,(byte)0);check(connection.componentRegistryKeys().equals(first),"input bytes cannot mutate retained keys");
        reject(()->handle(connection,registry("minecraft:enchantment","test:other")),"duplicate registry replacement rejected");
        check(connection.componentRegistryKeys().equals(first),"failed same-configuration replacement cannot install new names");
        handle(connection,registry("test:second","test:one"));check(first.size()==1&&connection.componentRegistryKeys().size()==2,"previous snapshot has no mutable aliases");
        set(connection,"stage",VanillaConnection.Stage.PLAY);handle(connection,new byte[]{0x78});
        check(connection.componentRegistryKeys().isEmpty()&&connection.componentRegistries().isEmpty(),"reconfiguration drops prior generation keys and counts");
        check(ComponentHash.component(13,observed,connection.componentRegistryKeys()).isEmpty(),"cleared current context cannot hash a prior observed holder");
        check(first.get("minecraft:enchantment").get(0).equals("test:alpha"),"immutable old snapshot is stable, never reused by getter");
        handle(connection,registry("minecraft:enchantment","test:gamma","test:beta"));check(connection.componentRegistryKeys().get("minecraft:enchantment").get(0).equals("test:gamma"),"same ID binds current name after reconfiguration");
        check(ComponentHash.component(13,observed,connection.componentRegistryKeys()).orElseThrow()==-1315823333,"current getter resolves the replacement generation without cache");
        connection.close();check(connection.componentRegistryKeys().isEmpty(),"close clears keys");connection.close();check(connection.componentRegistryKeys().isEmpty(),"repeated close remains empty");
        VanillaConnection entry=fixture();handle(entry,registry("minecraft:enchantment","test:old"));invoke(entry,"transition",new Class<?>[]{VanillaConnection.Stage.class},VanillaConnection.Stage.CONFIGURATION);
        check(entry.componentRegistryKeys().isEmpty(),"every configuration entry clears prior keys");entry.close();
        invalidRegistryInputs();System.out.println("COMPONENT_REGISTRY_KEYS_PASS "+checks);
    }
    private static void invalidRegistryInputs()throws Exception {
        for(byte[] packet:new byte[][]{registry("minecraft:enchantment","test:dup","test:dup"),registry("BAD:registry","test:x"),registry("minecraft:enchantment","Bad:key"),concat(registry("minecraft:enchantment","test:x"),new byte[]{0})}){
            VanillaConnection c=fixture();reject(()->handle(c,packet),"malformed names or trailing bytes reject");check(c.componentRegistryKeys().isEmpty(),"malformed packet installs no keys");c.close();
        }
        byte[] valid=registry("minecraft:enchantment","test:x");for(int n=1;n<valid.length;n++){
            VanillaConnection c=fixture();byte[] truncated=Arrays.copyOf(valid,n);reject(()->handle(c,truncated),"truncated registry rejected");check(c.componentRegistryKeys().isEmpty(),"truncated packet installs no keys");c.close();
        }
        VanillaConnection missing=fixture();byte[] noData=valid.clone();noData[noData.length-3]=0;reject(()->handle(missing,noData),"known-pack omitted data remains unsupported");check(missing.componentRegistryKeys().isEmpty(),"missing data installs no keys");missing.close();
        VanillaConnection bytes=fixture();set(bytes,"registryKeyBytes",64L*1024*1024-1);reject(()->handle(bytes,valid),"retained UTF bytes use existing64MiB configuration ceiling");check(bytes.componentRegistryKeys().isEmpty(),"retained-byte budget checks precede installation");bytes.close();
        VanillaConnection entries=fixture();set(entries,"registryEntries",100000);reject(()->handle(entries,valid),"existing aggregate entry bound preserved");check(entries.componentRegistryKeys().isEmpty(),"entry bound installs no keys");entries.close();
    }
    private static VanillaConnection fixture()throws Exception {VanillaConnection c=new VanillaConnection(new UUID(0,1),"Probe",hash->{throw new AssertionError("auth callback forbidden");});set(c,"stage",VanillaConnection.Stage.CONFIGURATION);set(c,"out",new ByteArrayOutputStream());return c;}
    private static void handle(VanillaConnection c,byte[] bytes)throws Exception {invoke(c,"handlePacket",new Class<?>[]{byte[].class},bytes);}
    private static Object invoke(Object target,String name,Class<?>[] types,Object... args)throws Exception {Method m=VanillaConnection.class.getDeclaredMethod(name,types);m.setAccessible(true);try{return m.invoke(target,args);}catch(InvocationTargetException e){if(e.getCause() instanceof Exception x)throw x;throw (Error)e.getCause();}}
    private static void set(Object target,String name,Object value)throws Exception {Field f=VanillaConnection.class.getDeclaredField(name);f.setAccessible(true);f.set(target,value);}
    private static byte[] registry(String name,String... keys)throws Exception {ByteArrayOutputStream bytes=new ByteArrayOutputStream();DataOutputStream out=new DataOutputStream(bytes);out.writeByte(7);string(out,name);vi(out,keys.length);for(String key:keys){string(out,key);out.write(new byte[]{1,10,0});}return bytes.toByteArray();}
    private static void string(DataOutputStream out,String value)throws Exception {byte[] bytes=value.getBytes(StandardCharsets.UTF_8);vi(out,bytes.length);out.write(bytes);}
    private static void vi(DataOutputStream out,int n)throws Exception {do{int b=n&127;n>>>=7;out.writeByte(n==0?b:b|128);}while(n!=0);}
    private static byte[] concat(byte[] a,byte[] b){byte[] out=Arrays.copyOf(a,a.length+b.length);System.arraycopy(b,0,out,a.length,b.length);return out;}
    private interface Checked {void run()throws Exception;}
    private static void reject(Checked action,String message)throws Exception {try{action.run();throw new AssertionError(message);}catch(IOException expected){checks++;}}
    private static void immutable(Checked action)throws Exception {try{action.run();throw new AssertionError("snapshot must be immutable");}catch(UnsupportedOperationException expected){checks++;}}
    private static void check(boolean value,String message){if(!value)throw new AssertionError(message);checks++;}
}
