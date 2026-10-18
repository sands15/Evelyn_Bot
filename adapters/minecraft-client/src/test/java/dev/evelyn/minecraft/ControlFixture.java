package dev.evelyn.minecraft;

import com.google.gson.JsonObject;
import java.io.*;
import java.lang.reflect.*;
import java.nio.charset.StandardCharsets;
import java.net.URI;
import java.util.UUID;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.BooleanSupplier;

/** Owned in-memory wire fixture. It cannot authenticate or open a game socket. */
public final class ControlFixture implements AutoCloseable {
    final VanillaConnection connection;
    final VanillaControl control;
    final ByteArrayOutputStream output = new ByteArrayOutputStream();
    private PlayerInventoryServer inventoryServer;
    private PlayerHandSwapServer handSwapServer;
    public static void main(String[] args) {
        try { runLoopback(false); marker("CONTROL_PASS"); }
        catch(Exception|AssertionError failure){marker("CONTROL_FAILED");System.exit(2);}
    }
    static void runLoopback(boolean advanced) throws Exception {
        runLoopback(advanced,false);
    }
    static void runLoopback(boolean advanced,boolean player) throws Exception {
        String endpoint=System.getenv("EVELYN_TEST_ENDPOINT"),token=System.getenv("EVELYN_TEST_TOKEN");
        String configured=System.getenv("EVELYN_TEST_DURATION_MS");long duration=configured==null?25000:Long.parseLong(configured);
        if(endpoint==null||token==null||duration<100||duration>25000)throw new IllegalArgumentException("fixture_config");
        ArrayBlockingQueue<String> inputs=new ArrayBlockingQueue<>(8);AtomicBoolean invalid=new AtomicBoolean(),connectionFailure=new AtomicBoolean();
        Thread reader=new Thread(()->{
            try{InputStreamReader in=new InputStreamReader(System.in,StandardCharsets.UTF_8);StringBuilder line=new StringBuilder();int ch;
                while((ch=in.read())!=-1){if(ch=='\n'){String value=line.toString().replace("\r","");line.setLength(0);if(!inputs.offer(value)){invalid.set(true);return;}}
                    else if(line.length()>=32){invalid.set(true);return;}else line.append((char)ch);}
            }catch(IOException e){invalid.set(true);}
        },"owned-control-input");reader.setDaemon(true);
        try(ControlFixture fixture=new ControlFixture(advanced,player)){
            AtomicInteger callbacks=new AtomicInteger();AtomicBoolean callbackOnce=new AtomicBoolean();
            DirectClient.ControlExecutor executor=new DirectClient.ControlExecutor(){
                public void start(JsonObject command,BooleanSupplier permit){fixture.control.start(command,permit);}
                public String poll()throws IOException{return fixture.control.poll();}
                public void stop(){fixture.control.stop();}
                public void fence(int sequence){fixture.control.fence(sequence);}
                public void off(){if(callbackOnce.compareAndSet(false,true))callbacks.incrementAndGet();fixture.control.off();}
                public String resultCode(){return fixture.control.resultCode();}
                public void idle(BooleanSupplier lease)throws Exception{if(advanced)fixture.control.idle(lease);}
            };
            boolean ready=false,quit=false;long start=System.nanoTime(),closedAt=0;int closedBytes=-1;
            try(DirectClient client=player?DirectClient.playerControl(URI.create(endpoint),token,executor):advanced?DirectClient.advancedControl(URI.create(endpoint),token,executor):DirectClient.control(URI.create(endpoint),token,executor)){
                client.observeControl(fixture.control.observation());client.connect().exceptionally(e->{connectionFailure.set(true);return null;});reader.start();
                while(!quit&&System.nanoTime()-start<duration*1_000_000L){
                    if(invalid.get()||connectionFailure.get())throw new AssertionError("fixture_input_or_connect");
                    client.tick();
                    if(!ready&&client.ready()){ready=true;marker("READY");}
                    if(fixture.connection.snapshot().stage()==VanillaConnection.Stage.CLOSED){
                        if(closedAt==0){closedAt=System.nanoTime();closedBytes=fixture.output.size();}
                        if(fixture.output.size()!=closedBytes)throw new AssertionError("write_after_close");
                        if(System.nanoTime()-closedAt>=500_000_000L)break;
                    }else{
                        String input;while((input=inputs.poll())!=null){
                            switch(input){
                                case "player_inventory" -> {
                                    if(!player)throw new IllegalArgumentException("fixture_mode");
                                    fixture.handle(packet(0x12,d->{vi(d,0);vi(d,3);vi(d,46);for(int i=0;i<46;i++){if(i==36){vi(d,8);vi(d,1);vi(d,0);vi(d,0);}else vi(d,0);}vi(d,0);}));
                                    fixture.handle(packet(0x6b,d->vi(d,0)));
                                }
                                case "player_slot" -> {
                                    if(!player)throw new IllegalArgumentException("fixture_mode");
                                    fixture.handle(packet(0x14,d->{vi(d,0);vi(d,4);d.writeShort(36);vi(d,7);vi(d,1);vi(d,0);vi(d,0);}));
                                }
                                case "player_crafter","player_lectern" -> {
                                    if(!player)throw new IllegalArgumentException("fixture_mode");
                                    boolean crafter=input.equals("player_crafter");int type=crafter?7:17,total=crafter?46:1;
                                    fixture.handle(packet(0x3c,d->{vi(d,1);vi(d,type);d.writeByte(8);d.writeUTF("");}));
                                    fixture.handle(packet(0x12,d->{vi(d,1);vi(d,5);vi(d,total);
                                        for(int slot=0;slot<total;slot++){int count=crafter?(slot==36?8:slot==45?1:0):0;vi(d,count);if(count>0){vi(d,1);vi(d,0);vi(d,0);}}vi(d,0);}));
                                }
                                case "player_mount" -> {
                                    if(!player)throw new IllegalArgumentException("fixture_mode");
                                    fixture.handle(packet(0x2a,d->{vi(d,1);vi(d,0);d.writeInt(99);}));
                                }
                                case "player_furnace","player_smithing","player_cartography" -> {
                                    if(!player)throw new IllegalArgumentException("fixture_mode");
                                    int menu=input.equals("player_furnace")?14:input.equals("player_smithing")?21:23,total=menu==21?40:39;
                                    fixture.handle(packet(0x3c,d->{vi(d,1);vi(d,menu);d.writeByte(8);d.writeUTF("");}));
                                    fixture.handle(packet(0x12,d->{vi(d,1);vi(d,5);vi(d,total);for(int slot=0;slot<=total;slot++)vi(d,0);}));
                                }
                                case "player_smith_error" -> {
                                    if(!player)throw new IllegalArgumentException("fixture_mode");
                                    fixture.handle(packet(0x13,d->{vi(d,1);d.writeShort(0);d.writeShort(1);}));
                                }
                                case "player_property" -> {
                                    if(!player)throw new IllegalArgumentException("fixture_mode");
                                    for(int index=0;index<3;index++){
                                        int property=index,value=index==0?-32768:index==1?0:32767;
                                        fixture.handle(packet(0x13,d->{vi(d,1);d.writeShort(property);d.writeShort(value);}));
                                    }
                                }
                                case "player_tags_partial" -> {
                                    if(!player)throw new IllegalArgumentException("fixture_mode");
                                    fixture.handle(CurrentTagControlTest.packet(new CurrentTagControlTest.Tags("minecraft:item",java.util.Map.of("test:items",new int[]{1}))));
                                }
                                case "player_recipe_add" -> {
                                    if(!player)throw new IllegalArgumentException("fixture_mode");
                                    fixture.handle(packet(0x4b,d->d.write(java.util.HexFormat.of().parseHex("0107000000000000000301"))));
                                    fixture.handle(packet(0x4d,d->d.write(java.util.HexFormat.of().parseHex("0100010001000100"))));
                                }
                                case "player_recipe_ghost" -> {
                                    if(!player)throw new IllegalArgumentException("fixture_mode");
                                    fixture.handle(packet(0x40,d->d.write(java.util.HexFormat.of().parseHex("0103000000"))));
                                }
                                case "player_recipe_properties" -> {
                                    if(!player)throw new IllegalArgumentException("fixture_mode");fixture.handle(packet(0x88,d->d.write(new byte[]{0,0})));
                                }
                                case "player_recipe_report" -> {
                                    if(!player)throw new IllegalArgumentException("fixture_mode");var observed=fixture.control.recipeSnapshot();
                                    marker("RECIPES complete="+observed.bookComplete()+" ids="+observed.bindings().size()+" id7="+observed.bindings().containsKey(new RecipeState.DisplayId(7))
                                        +" ghost="+(observed.ghost()==null?"unknown":observed.ghost().containerId())+" settings="+(observed.settings()==null?"unknown":observed.settings().types().size()));
                                }
                                case "player_transfer_server" -> {
                                    if(!player||fixture.inventoryServer!=null)throw new IllegalArgumentException("fixture_mode");
                                    fixture.installInventoryServer();
                                }
                                case "player_swap_server" -> {
                                    if(!player||fixture.handSwapServer!=null||fixture.inventoryServer!=null)throw new IllegalArgumentException("fixture_mode");
                                    fixture.installHandSwapServer();
                                }
                                case "player_swap_pause" -> {
                                    if(!player||fixture.handSwapServer==null)throw new IllegalArgumentException("fixture_mode");fixture.handSwapServer.pause();
                                }
                                case "player_swap_report" -> {
                                    if(!player||fixture.handSwapServer==null)throw new IllegalArgumentException("fixture_mode");marker(fixture.handSwapServer.report());
                                }
                                case "player_transfer_pause" -> {
                                    if(!player||fixture.inventoryServer==null)throw new IllegalArgumentException("fixture_mode");
                                    fixture.inventoryServer.pauseResponses();
                                }
                                case "player_transfer_report" -> {
                                    if(!player||fixture.inventoryServer==null)throw new IllegalArgumentException("fixture_mode");
                                    marker(fixture.inventoryServer.report());
                                }
                                case "damageheal" -> {fixture.handle(health(19));fixture.handle(health(20));}
                                case "monster" -> fixture.handle(packet(1,d->d.write(EntityTrackerTest.spawn(99,32,8.5,1,13))));
                                case "clear_monster" -> fixture.handle(packet(0x4e,d->d.write(EntityTrackerTest.remove(99))));
                                case "quit" -> quit=true;
                                default -> {if(!advanced||!AdvancedControlFixture.handle(fixture,input))throw new IllegalArgumentException("fixture_input");}
                            }
                            marker("UPDATE");if(quit)break;
                        }
                        if(!quit)client.observeControl(fixture.control.observation());
                    }
                    Thread.sleep(20);
                }
                if(!quit&&closedAt==0)throw new AssertionError("fixture_duration");
                int bytesBeforeClose=fixture.output.size();client.close();client.tick();
                if(fixture.output.size()!=bytesBeforeClose)throw new AssertionError("write_during_close");
            }
            if(!ready||callbacks.get()!=1||fixture.connection.snapshot().stage()!=VanillaConnection.Stage.CLOSED
                    ||fixture.connection.observation().available())throw new AssertionError("fixture_cleanup");
            if(fixture.inventoryServer!=null)marker(fixture.inventoryServer.report());
            if(fixture.handSwapServer!=null)marker(fixture.handSwapServer.report());
            marker("CONTROL_CLOSED");
        }
    }
    private static void marker(String value){System.out.println(value);System.out.flush();}
    ControlFixture() throws Exception {
        this(false);
    }
    ControlFixture(boolean advanced) throws Exception {
        this(advanced,false);
    }
    ControlFixture(boolean advanced,boolean player) throws Exception {
        connection = new VanillaConnection(new UUID(0, 9), "Synthetic", hash -> { throw new AssertionError("authentication forbidden"); });
        control = player?connection.enablePlayerControl(BlockCatalog.loadPinned()):connection.enableControl(BlockCatalog.loadPinned(),advanced);
        set(connection,"stage",VanillaConnection.Stage.CONFIGURATION);
        set(connection,"authenticated",true); set(connection,"encrypted",true);
        set(connection,"in",new ByteArrayInputStream(new byte[0])); set(connection,"out",output);
        handle(packet(0x07,d->{string(d,"minecraft:dimension_type");vi(d,1);string(d,"test:dimension");
            d.writeByte(1);d.writeByte(10);d.writeByte(3);d.writeUTF("min_y");d.writeInt(0);
            d.writeByte(3);d.writeUTF("height");d.writeInt(16);d.writeByte(0);}));
        handle(packet(0x07,d->{string(d,"minecraft:worldgen/biome");vi(d,1);string(d,"test:biome");d.write(new byte[]{1,10,0});}));
        handle(new byte[]{3});
        handle(packet(0x32,d->{d.writeInt(42);d.writeBoolean(false);vi(d,1);string(d,"test:world");
            vi(d,20);vi(d,2);vi(d,2);d.writeBoolean(false);d.writeBoolean(true);d.writeBoolean(false);
            vi(d,0);string(d,"test:world");d.writeLong(0);vi(d,0);vi(d,0);d.writeBoolean(false);d.writeBoolean(false);
            d.writeBoolean(false);vi(d,0);vi(d,63);d.writeBoolean(true);d.writeBoolean(true);}));
        handle(packet(0x41,d->{d.writeByte(0);d.writeFloat(.05f);d.writeFloat(.1f);}));
        handle(health(20)); handle(chunk()); handle(position(8.5,1,8.5,0));
        output.reset();
    }
    void handle(byte[] bytes) throws Exception {
        Method method=VanillaConnection.class.getDeclaredMethod("handlePacket",byte[].class);method.setAccessible(true);
        try {method.invoke(connection,(Object)bytes);} catch(InvocationTargetException e) {
            if(e.getCause() instanceof Exception x)throw x;throw (Error)e.getCause();
        }
    }
    private void installHandSwapServer()throws Exception {
        handSwapServer=new PlayerHandSwapServer();
        set(connection,"out",new OutputStream(){
            @Override public void write(int value)throws IOException{throw new IOException("fragmented_synthetic_game_write");}
            @Override public void write(byte[] bytes,int offset,int length)throws IOException{
                output.write(bytes,offset,length);
                for(byte[] response:handSwapServer.receive(java.util.Arrays.copyOfRange(bytes,offset,offset+length)))
                    try{handle(response);}catch(Exception failure){throw new IOException(failure);}
            }
        });
        handle(handSwapServer.initialContent());handle(packet(0x6b,d->vi(d,0)));
    }
    private void installInventoryServer()throws Exception {
        inventoryServer=new PlayerInventoryServer();
        set(connection,"out",new OutputStream(){
            @Override public void write(int value)throws IOException{throw new IOException("fragmented_synthetic_game_write");}
            @Override public void write(byte[] bytes,int offset,int length)throws IOException {
                output.write(bytes,offset,length);
                for(byte[] response:inventoryServer.receive(java.util.Arrays.copyOfRange(bytes,offset,offset+length)))
                    try{handle(response);}catch(Exception failure){throw new IOException(failure);}
            }
        });
        handle(inventoryServer.initialContent());handle(packet(0x6b,d->vi(d,0)));
    }
    static byte[] position(double x,double y,double z,int flags) throws IOException {
        return packet(0x49,d->{vi(d,1);d.writeDouble(x);d.writeDouble(y);d.writeDouble(z);
            d.writeDouble(0);d.writeDouble(0);d.writeDouble(0);d.writeFloat(0);d.writeFloat(0);d.writeInt(flags);});
    }
    static byte[] health(float value) throws IOException {return packet(0x6a,d->{d.writeFloat(value);vi(d,20);d.writeFloat(5);});}
    static byte[] block(int x,int y,int z,int state) throws IOException {
        return packet(8,d->{d.writeLong(((long)x&0x3ffffff)<<38|((long)z&0x3ffffff)<<12|((long)y&0xfff));vi(d,state);});
    }
    static byte[] chunk() throws IOException {
        byte[] section=body(d->{d.writeShort(256);d.writeShort(0);d.writeByte(4);vi(d,2);vi(d,0);vi(d,1);
            for(int i=0;i<256;i++)d.writeLong(i<16?0x1111111111111111L:0);d.writeByte(0);vi(d,0);});
        return packet(0x2e,d->{d.writeInt(0);d.writeInt(0);vi(d,0);vi(d,section.length);d.write(section);d.write(new byte[7]);});
    }
    static JsonObject navigate(int seq,int x,int y,int z) {
        JsonObject c=new JsonObject();c.addProperty("seq",seq);c.addProperty("action","navigate");c.addProperty("run_ms",15000);
        c.addProperty("target_x",x);c.addProperty("target_y",y);c.addProperty("target_z",z);return c;
    }
    static JsonObject look(int seq,float yaw,float pitch) {
        JsonObject c=new JsonObject();c.addProperty("seq",seq);c.addProperty("action","look");c.addProperty("run_ms",5000);
        c.addProperty("yaw",yaw);c.addProperty("pitch",pitch);return c;
    }
    static byte[] packet(int id,Write write) throws IOException {return body(d->{vi(d,id);write.run(d);});}
    static byte[] body(Write write) throws IOException {ByteArrayOutputStream b=new ByteArrayOutputStream();write.run(new DataOutputStream(b));return b.toByteArray();}
    static void vi(DataOutputStream d,int n)throws IOException{while((n&~127)!=0){d.writeByte((n&127)|128);n>>>=7;}d.writeByte(n);}
    static void string(DataOutputStream d,String s)throws IOException{byte[] b=s.getBytes(StandardCharsets.UTF_8);vi(d,b.length);d.write(b);}
    static void set(Object target,String name,Object value)throws Exception{Field f=target.getClass().getDeclaredField(name);f.setAccessible(true);f.set(target,value);}
    static Object get(Object target,String name)throws Exception{Field f=target.getClass().getDeclaredField(name);f.setAccessible(true);return f.get(target);}
    @FunctionalInterface interface Write{void run(DataOutputStream output)throws IOException;}
    @Override public void close(){connection.close();}
}
