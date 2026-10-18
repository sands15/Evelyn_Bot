package dev.evelyn.minecraft;

import java.io.*;
import java.lang.reflect.*;
import java.nio.ByteBuffer;
import java.net.Socket;
import java.math.BigInteger;
import java.security.*;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import java.util.zip.Deflater;
import javax.crypto.*;
import javax.crypto.spec.*;

/** Fixed vectors derived from the hash-pinned Mojang 26.3 classes, not encoder round trips. */
public final class VanillaConnectionTest {
    private static Class<?> type;
    private static int checks;
    private static final UUID PROFILE = new UUID(0, 1);

    public static void main(String[] args) throws Exception {
        try { type = Class.forName("dev.evelyn.minecraft.VanillaConnection"); }
        catch (ClassNotFoundException missing) { throw new AssertionError("26.3 admission transport is not implemented", missing); }
        equal(777, invoke(null, "readVarInt", new Class[]{InputStream.class, int.class}, input("8906"), 5));
        equal(-1, invoke(null, "readVarInt", new Class[]{InputStream.class, int.class}, input("ffffffff0f"), 5));
        reject(() -> invoke(null, "readVarInt", new Class[]{InputStream.class, int.class}, input("ffffffff10"), 5));
        reject(() -> decode(hex("00"), -1));
        reject(() -> decode(hex("80808000"), -1));
        reject(() -> decode(hex("0201"), -1));
        bytes(hex("0100"), encode(hex("00"), -1));
        bytes(hex("020000"), encode(hex("00"), 256));
        bytes(hex("04010203"), decode(hex("050004010203"), 256));
        byte[] compressed = zlib(hex("04010203"));
        bytes(hex("04010203"), decode(concat(new byte[]{(byte)(compressed.length + 1), 4}, compressed), 1));
        reject(() -> decode(concat(new byte[]{(byte)(compressed.length + 1), 5}, compressed), 1));
        reject(() -> decode(concat(new byte[]{(byte)(compressed.length + 2), 4}, compressed, new byte[]{0}), 1));
        reject(() -> decode(concat(new byte[]{(byte)(compressed.length + 1), 4}, compressed), 5));
        reject(() -> decode(hex("0481808004"), 1));

        Object connection = fixture("CONFIGURATION");
        handle(connection, hex("04000000000000007b"));
        bytes(hex("0904000000000000007b"), output(connection));
        handle(connection, hex("0501020304"));
        bytes(hex("050501020304"), output(connection));
        // KnownPack list: one (minecraft, core, 26.3); explicitly select no local packs.
        handle(connection, concat(hex("0f01"), string("minecraft"), string("core"), string("26.3")));
        bytes(hex("020700"), output(connection));
        reject(() -> handle(fixture("CONFIGURATION"), hex("1400")));
        reject(() -> handle(fixture("CONFIGURATION"), hex("09")));
        reject(() -> handle(fixture("CONFIGURATION"), hex("ff01")));
        reject(() -> handle(fixture("CONFIGURATION"), hex("04000000000000000100")));
        reject(() -> handle(fixture("CONFIGURATION"), hex("04000000")));

        // One unnamed compound with root INT min_y=0, height=16.
        byte[] registry = concat(hex("07"), string("minecraft:dimension_type"), hex("01"), string("minecraft:overworld"), hex("010a0300056d696e5f79000000000300066865696768740000001000"));
        handle(connection, registry);
        reject(() -> handle(fixture("CONFIGURATION"), concat(hex("07"), string("minecraft:dimension_type"), hex("01"), string("minecraft:overworld"), hex("00"))));
        reject(() -> handle(fixture("CONFIGURATION"), concat(hex("07"), string("minecraft:dimension_type"), hex("01"), string("minecraft:overworld"), hex("010d"))));
        byte[] deep = new byte[200];
        for (int i = 0; i < 40; i++) { deep[i*4] = 10; deep[i*4+1] = 0; deep[i*4+2] = 1; deep[i*4+3] = 'x'; }
        reject(() -> handle(fixture("CONFIGURATION"), concat(hex("07"), string("minecraft:dimension_type"), hex("01"), string("minecraft:overworld"), hex("010a"), deep)));

        Object login = fixture("LOGIN");
        set(login, "authenticated", true); set(login, "encrypted", true);
        // CB login-finished: GameProfile UUID, name, zero properties, additional session UUID.
        byte[] finished = concat(hex("02"), uuid(PROFILE), string("Probe"), hex("00"), uuid(new UUID(0, 2)));
        handle(login, finished);
        check("CONFIGURATION".equals(field(login, "stage").toString()), "login transitions to configuration");
        byte[] sent = output(login);
        check(sent.length > 2 && sent[0] == 1 && sent[1] == 3, "login acknowledgement precedes client information");
        Object wrong = fixture("LOGIN"); set(wrong, "authenticated", true); set(wrong, "encrypted", true);
        reject(() -> handle(wrong, concat(hex("02"), uuid(new UUID(0, 99)), string("Probe"), hex("00"), uuid(new UUID(0, 2)))));
        reject(() -> handle(fixture("LOGIN"), finished));
        reject(() -> handle(fixture("LOGIN"), hex("ff")));

        Object play = fixture("PLAY"); set(play, "playLogin", true);
        // CB 0x49: id=7, x/y/z=1/64/2, delta=0/0/0, yaw=90, pitch=0, flags=0.
        ByteBuffer position = ByteBuffer.allocate(62).put((byte)0x49).put((byte)7);
        position.putDouble(1).putDouble(64).putDouble(2).putDouble(0).putDouble(0).putDouble(0).putFloat(90).putFloat(0).putInt(0);
        handle(play, position.array());
        ByteBuffer expected = ByteBuffer.allocate(35).put((byte)34).put((byte)0).put((byte)7);
        expected.putDouble(1).putDouble(64).putDouble(2).putFloat(90).putFloat(0);
        bytes(expected.array(), output(play));
        check((boolean)field(play, "positionConfirmed"), "server position confirmed");
        byte[] nonfinite = position.array().clone(); ByteBuffer.wrap(nonfinite).putDouble(2, Double.NaN);
        reject(() -> handle(play, nonfinite));
        reject(() -> handle(play, concat(position.array(), hex("00"))));
        byte[] relativeFirst = position.array().clone(); ByteBuffer.wrap(relativeFirst).putInt(58, 1);
        Object first = fixture("PLAY"); set(first, "playLogin", true);
        reject(() -> handle(first, relativeFirst));
        handle(play, hex("2d0000000000000001"));
        bytes(hex("091c0000000000000001"), output(play));
        reject(() -> handle(play, hex("54")));
        reject(() -> handle(play, hex("9001")));
        Object noLogin = fixture("PLAY"); reject(() -> handle(noLogin, position.array()));
        handle(play, hex("78"));
        bytes(hex("0110"), output(play));
        check("CONFIGURATION".equals(field(play,"stage").toString()), "reconfiguration fences play");
        check(!(boolean)field(play,"positionConfirmed"), "reconfiguration invalidates position");
        check(((Set<?>)invoke(play,"capabilities",new Class[0])).isEmpty(), "no action capabilities");
        invoke(play, "close", new Class[0]); invoke(play, "close", new Class[0]);
        statusAndStateVectors(registry);
        cryptoBoundary();
        encryptedFrameBoundary();
        boundedStreams();
        Object overdue=fixture("CONFIGURATION");((AtomicBoolean)field(overdue,"busy")).set(true);set(overdue,"deadlineNanos",System.nanoTime()-1);
        reject(()->handle(overdue,hex("040000000000000001")));equal(0,output(overdue).length);
        Object pitchClamp = fixture("PLAY"); set(pitchClamp,"playLogin",true);
        byte[] lookDown = position.array().clone(); ByteBuffer.wrap(lookDown).putFloat(54, 120);
        handle(pitchClamp, lookDown);
        // PositionMoveRotation.calculateAbsolute clamps pitch to [-90,90] before confirmation.
        equal(90f, ByteBuffer.wrap(output(pitchClamp)).getFloat(31));
        System.out.println("VANILLA_CONNECTION_PASS " + checks + " checks (synthetic, no server)");
    }
    private static void statusAndStateVectors(byte[] registry) throws Exception {
        invoke(null,"validateStatus",new Class[]{String.class},"{\"version\":{\"name\":\"26.3\",\"protocol\":777},\"players\":{\"online\":0},\"description\":\"unused\"}"); checks++;
        reject(()->invoke(null,"validateStatus",new Class[]{String.class},"{\"version\":{\"name\":\"26.3\",\"protocol\":776}}"));
        reject(()->invoke(null,"validateStatus",new Class[]{String.class},"{\"version\":{\"name\":\"26.3\",\"protocol\":\"777\"}}"));
        reject(()->invoke(null,"validateStatus",new Class[]{String.class},"{\"version\":{\"name\":\"26.3\",\"protocol\":777,\"protocol\":777}}"));
        reject(()->invoke(null,"validateStatus",new Class[]{String.class},"{\"version\":{\"name\":\"26.3\",\"protocol\":777}}{}"));
        Object config = fixture("CONFIGURATION"); reject(()->handle(config,hex("03")));
        handle(config,registry); handle(config,hex("03")); bytes(hex("0103"),output(config));
        set(config,"authenticated",true); set(config,"encrypted",true);
        // Play login: INT entity, BOOL hardcore, dimension set, three VarInts, three BOOLs,
        // dimension holder raw registry id, dimension key, seed, game type VarInt,
        // optional previous game type (0=absent), 3 BOOLs, cooldown, sea level, 2 BOOLs.
        byte[] playLogin = concat(hex("320000002a0001"),string("minecraft:overworld"),hex("14020200010000"),string("minecraft:overworld"),hex("00000000000000000100000000003f0101"));
        handle(config,playLogin); check((boolean)field(config,"playLogin"),"full fixed play login consumed");
        reject(()->handle(config,playLogin));
        Object offline = fixture("CONFIGURATION");handle(offline,registry);handle(offline,hex("03"));
        byte[] offlineLogin = playLogin.clone();offlineLogin[offlineLogin.length-2]=0;
        reject(()->handle(offline,offlineLogin));
        Object batch = fixture("CONFIGURATION"); handle(batch,registry);
        handle(batch,concat(hex("07"),string("minecraft:worldgen/biome"),hex("01"),string("test:biome"),hex("010a00")));
        handle(batch,hex("03")); set(batch,"authenticated",true); set(batch,"encrypted",true); handle(batch,playLogin); output(batch);
        handle(batch,hex("0c"));handle(batch,hex("2e00000000000000000008000000000000000000000000000000"));handle(batch,hex("0b01"));bytes(hex("050b3f800000"),output(batch));
        reject(()->handle(batch,hex("0b01")));
        Object bundle = fixture("PLAY");handle(bundle,hex("00"));set(bundle,"positionConfirmed",true);
        Object state = invoke(bundle,"snapshot",new Class[0]);
        check(!(boolean)state.getClass().getMethod("initialPositionConfirmed").invoke(state),"open bundle does not publish admission");
        handle(bundle,hex("00"));
        state = invoke(bundle,"snapshot",new Class[0]);
        check(!(boolean)state.getClass().getMethod("worldReady").invoke(state),"world readiness is never claimed");
        reject(()->handle(bundle,hex("8f01")));
    }
    private static void cryptoBoundary() throws Exception {
        KeyPairGenerator generator=KeyPairGenerator.getInstance("RSA");generator.initialize(1024);KeyPair pair=generator.generateKeyPair();
        byte[] challenge=hex("01020304"), publicKey=pair.getPublic().getEncoded();
        byte[] hello=concat(hex("0100"),varint(publicKey.length),publicKey,hex("04"),challenge,hex("01"));
        Class<?> join = Class.forName("dev.evelyn.minecraft.VanillaConnection$SessionJoiner");
        ByteArrayOutputStream wire=new ByteArrayOutputStream();AtomicReference<String> digest=new AtomicReference<>();
        Object callback=Proxy.newProxyInstance(join.getClassLoader(),new Class[]{join},(p,m,a)->{
            if(wire.size()!=0)throw new AssertionError("key response before authenticated join");digest.set((String)a[0]);return null;
        });
        Object login=type.getConstructor(UUID.class,String.class,join).newInstance(PROFILE,"Probe",callback);
        set(login,"out",wire);set(login,"in",new ByteArrayInputStream(new byte[0]));set(login,"stage",field(fixture("LOGIN"),"stage"));
        invoke(login,"begin",new Class[]{Duration.class},Duration.ofSeconds(2));
        handle(login,hello);
        byte[] response=decode(wire.toByteArray(),-1); ByteArrayInputStream fields=new ByteArrayInputStream(response);
        equal(1,fields.read()); int secretLength=(int)invoke(null,"readVarInt",new Class[]{InputStream.class,int.class},fields,5);
        Cipher rsa=Cipher.getInstance("RSA/ECB/PKCS1Padding");rsa.init(Cipher.DECRYPT_MODE,pair.getPrivate());byte[] secret=rsa.doFinal(fields.readNBytes(secretLength));
        int challengeLength=(int)invoke(null,"readVarInt",new Class[]{InputStream.class,int.class},fields,5);
        bytes(challenge,rsa.doFinal(fields.readNBytes(challengeLength)));equal(0,fields.available());equal(16,secret.length);
        MessageDigest sha=MessageDigest.getInstance("SHA-1");sha.update(secret);sha.update(publicKey);
        equal(new BigInteger(sha.digest()).toString(16),digest.get());
        wire.reset();handle(login,hex("050f6d696e6563726166743a70726f6265")); // cookie request after AES switch
        Cipher aes=Cipher.getInstance("AES/CFB8/NoPadding");aes.init(Cipher.DECRYPT_MODE,new SecretKeySpec(secret,"AES"),new IvParameterSpec(secret));
        bytes(concat(hex("12040f"),"minecraft:probe".getBytes(java.nio.charset.StandardCharsets.UTF_8),hex("00")),aes.doFinal(wire.toByteArray()));
        invoke(login,"close",new Class[0]);
        AtomicInteger calls=new AtomicInteger();ByteArrayOutputStream rejectedWire=new ByteArrayOutputStream();
        Object rejectedCallback=Proxy.newProxyInstance(join.getClassLoader(),new Class[]{join},(p,m,a)->{calls.incrementAndGet();throw new IOException("private synthetic auth response");});
        Object denied=type.getConstructor(UUID.class,String.class,join).newInstance(PROFILE,"Probe",rejectedCallback);
        set(denied,"out",rejectedWire);set(denied,"stage",field(fixture("LOGIN"),"stage"));invoke(denied,"begin",new Class[]{Duration.class},Duration.ofSeconds(2));
        reject(()->handle(denied,hello));equal(1,calls.get());equal(0,rejectedWire.size());check(!(boolean)field(denied,"authenticated"),"failed join cannot authenticate");invoke(denied,"close",new Class[0]);
    }
    private static void encryptedFrameBoundary() throws Exception {
        byte[] packet=hex("04010203"),key=new byte[16];
        for(int threshold:new int[]{-1,256,1}) {
            for(int frames:new int[]{1,2}) for(int fragment:new int[]{1,512}) {
                byte[] framed=encode(packet,threshold);
                Cipher encrypt=Cipher.getInstance("AES/CFB8/NoPadding");
                encrypt.init(Cipher.ENCRYPT_MODE,new SecretKeySpec(key,"AES"),new IvParameterSpec(key));
                byte[] wire=encrypt.doFinal(frames==1?framed:concat(framed,framed));
                InputStream peer=new ByteArrayInputStream(wire) {
                    @Override public synchronized int read(byte[] b,int off,int len) {
                        if(pos==count&&len>0) throw new AssertionError("frame read waits for peer before acknowledging");
                        return super.read(b,off,Math.min(len,fragment));
                    }
                };
                Cipher decrypt=Cipher.getInstance("AES/CFB8/NoPadding");
                decrypt.init(Cipher.DECRYPT_MODE,new SecretKeySpec(key,"AES"),new IvParameterSpec(key));
                InputStream input=new CipherInputStream(peer,decrypt);
                for(int i=0;i<frames;i++) bytes(packet,(byte[])invoke(null,"decodeFrame",new Class[]{InputStream.class,int.class},input,threshold));
            }
            Cipher encrypt=Cipher.getInstance("AES/CFB8/NoPadding");
            encrypt.init(Cipher.ENCRYPT_MODE,new SecretKeySpec(key,"AES"),new IvParameterSpec(key));
            byte[] wire=encrypt.doFinal(encode(packet,threshold));
            Cipher decrypt=Cipher.getInstance("AES/CFB8/NoPadding");
            decrypt.init(Cipher.DECRYPT_MODE,new SecretKeySpec(key,"AES"),new IvParameterSpec(key));
            InputStream truncated=new CipherInputStream(new ByteArrayInputStream(Arrays.copyOf(wire,wire.length-1)),decrypt);
            reject(()->invoke(null,"decodeFrame",new Class[]{InputStream.class,int.class},truncated,threshold));
        }
    }
    private static void boundedStreams() throws Exception {
        Object blockedWrite=fixture("CONFIGURATION");ClosingSocket sink=new ClosingSocket();set(blockedWrite,"socket",sink);
        set(blockedWrite,"out",new OutputStream(){public void write(int b)throws IOException{sink.block();}public void write(byte[] b,int o,int n)throws IOException{sink.block();}});
        long start=System.nanoTime();invoke(blockedWrite,"begin",new Class[]{Duration.class},Duration.ofMillis(100));
        reject(()->handle(blockedWrite,hex("040000000000000001")));
        check(TimeUnit.NANOSECONDS.toMillis(System.nanoTime()-start)<1500,"watchdog bounds a stalled complete write");equal(1,sink.closes.get());
        Object blockedRead=fixture("PLAY");ClosingSocket source=new ClosingSocket();set(blockedRead,"socket",source);set(blockedRead,"playLogin",true);set(blockedRead,"positionConfirmed",true);
        set(blockedRead,"in",new InputStream(){public int read()throws IOException{source.block();return -1;}});
        start=System.nanoTime();Object result=invoke(blockedRead,"maintain",new Class[]{Duration.class},Duration.ofMillis(100));
        check(TimeUnit.NANOSECONDS.toMillis(System.nanoTime()-start)<1500,"watchdog bounds an idle read");equal(1,source.closes.get());
        equal("CLOSED",result.getClass().getMethod("stage").invoke(result).toString());
        reject(()->invoke(blockedRead,"maintain",new Class[]{Duration.class},Duration.ofMillis(100)));
    }
    private static final class ClosingSocket extends Socket {
        final CountDownLatch closed=new CountDownLatch(1);final AtomicInteger closes=new AtomicInteger();
        @Override public void close(){closes.incrementAndGet();closed.countDown();}
        void block()throws IOException{try{if(!closed.await(2,TimeUnit.SECONDS))throw new AssertionError("owned stream was not closed by deadline");}catch(InterruptedException e){Thread.currentThread().interrupt();}throw new IOException("synthetic closed stream");}
    }
    private static byte[] varint(int value){ByteArrayOutputStream out=new ByteArrayOutputStream();while((value&~127)!=0){out.write(value&127|128);value>>>=7;}out.write(value);return out.toByteArray();}
    private static Object fixture(String stage) throws Exception {
        Class<?> join = Class.forName("dev.evelyn.minecraft.VanillaConnection$SessionJoiner");
        Object callback = Proxy.newProxyInstance(join.getClassLoader(), new Class[]{join}, (p,m,a)->null);
        Object value = type.getConstructor(UUID.class,String.class,join).newInstance(PROFILE,"Probe",callback);
        set(value,"out",new ByteArrayOutputStream());
        Class<?> state = Class.forName("dev.evelyn.minecraft.VanillaConnection$Stage");
        @SuppressWarnings({"unchecked","rawtypes"}) Object enumValue = Enum.valueOf((Class)state,stage);
        set(value,"stage",enumValue);
        return value;
    }
    private static byte[] output(Object value) throws Exception { ByteArrayOutputStream out=(ByteArrayOutputStream)field(value,"out"); byte[] result=out.toByteArray(); out.reset(); return result; }
    private static void handle(Object value, byte[] body) throws Exception { invoke(value,"handlePacket",new Class[]{byte[].class},body); }
    private static byte[] encode(byte[] body,int threshold) throws Exception { return (byte[])invoke(null,"encodeFrame",new Class[]{byte[].class,int.class},body,threshold); }
    private static byte[] decode(byte[] wire,int threshold) throws Exception { return (byte[])invoke(null,"decodeFrame",new Class[]{InputStream.class,int.class},new ByteArrayInputStream(wire),threshold); }
    private static Object invoke(Object target,String name,Class<?>[] parameters,Object... args) throws Exception {
        Method m=type.getDeclaredMethod(name,parameters); m.setAccessible(true);
        try { return m.invoke(target,args); } catch(InvocationTargetException e){ if(e.getCause() instanceof Exception x)throw x; throw (Error)e.getCause(); }
    }
    private static Object field(Object value,String name) throws Exception { Field f=type.getDeclaredField(name); f.setAccessible(true); return f.get(value); }
    private static void set(Object value,String name,Object data) throws Exception {
        if (name.equals("playLogin") && Boolean.TRUE.equals(data)) {
            PlayerObservation model = (PlayerObservation)field(value, "observations");
            if (!model.snapshot().available()) {
                int event = (int)field(value, "observationEvent") + 1;
                model.player(event, System.nanoTime(), "minecraft:overworld");
                set(value, "observationEvent", event);
            }
        }
        Field f=type.getDeclaredField(name);f.setAccessible(true);f.set(value,data);
    }
    private static byte[] uuid(UUID id){return ByteBuffer.allocate(16).putLong(id.getMostSignificantBits()).putLong(id.getLeastSignificantBits()).array();}
    private static byte[] string(String s){byte[] bytes=s.getBytes(java.nio.charset.StandardCharsets.UTF_8);return concat(new byte[]{(byte)bytes.length},bytes);}
    private static byte[] zlib(byte[] body){Deflater d=new Deflater();try{d.setInput(body);d.finish();byte[] data=new byte[200];int n=d.deflate(data);return Arrays.copyOf(data,n);}finally{d.end();}}
    private static ByteArrayInputStream input(String h){return new ByteArrayInputStream(hex(h));}
    private static byte[] hex(String h){return HexFormat.of().parseHex(h);}
    private static byte[] concat(byte[]... chunks){ByteArrayOutputStream b=new ByteArrayOutputStream();for(byte[] c:chunks)b.writeBytes(c);return b.toByteArray();}
    private static void bytes(byte[] expected,byte[] actual){check(Arrays.equals(expected,actual),"independent wire vector mismatch");}
    private static void equal(Object expected,Object actual){check(expected.equals(actual),"primitive vector mismatch");}
    private static void check(boolean condition,String label){if(!condition)throw new AssertionError(label);checks++;}
    private static void reject(Checked action) throws Exception {try{action.run();}catch(IOException|IllegalArgumentException expected){checks++;return;}throw new AssertionError("malformed/unsupported input accepted");}
    @FunctionalInterface private interface Checked {void run() throws Exception;}
}
