package dev.evelyn.minecraft;

import com.google.gson.Strictness;
import com.google.gson.stream.JsonReader;
import com.google.gson.stream.JsonToken;
import java.io.*;
import java.math.BigInteger;
import java.net.*;
import java.nio.ByteBuffer;
import java.nio.charset.*;
import java.security.*;
import java.security.interfaces.RSAPublicKey;
import java.security.spec.X509EncodedKeySpec;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.zip.*;
import javax.crypto.*;
import javax.crypto.spec.IvParameterSpec;

/**
 * Bounded, online-mode admission probe for Mojang Java 26.3, protocol 777.
 * Wire evidence: official client SHA-1 e877b6a07acd633fb3bb475002175cec036e7b87,
 * the protocol registration builders, packet STREAM_CODEC fields, and client listeners.
 * This is not a world simulator: unneeded play payloads are discarded, no chat or
 * voluntary movement is sent, and PLAYER_LOADED/worldReady are never asserted.
 */
public final class VanillaConnection implements AutoCloseable {
    private static final int PROTOCOL = 777;
    private static final int MAX_FRAME = 0x1fffff, MAX_PACKET = 8 * 1024 * 1024;
    private static final long MAX_CONFIG_BYTES = 64L * 1024 * 1024;
    public enum Stage { NEW, LOGIN, CONFIGURATION, PLAY, CLOSED }
    @FunctionalInterface public interface SessionJoiner { void joinServer(String signedServerHash) throws Exception; }
    public static final class Failure extends IOException {
        private final String code;
        public Failure(String code) { super(code); this.code = code; }
        public String code() { return code; }
    }
    public record Snapshot(Stage stage, boolean onlineAuthenticated, boolean initialPositionConfirmed,
                           boolean worldReady, long packetsReceived, long packetsSent,
                           long keepAlives, long teleportConfirmations) {}

    private final UUID profileId;
    private final String playerName;
    private final SessionJoiner joiner;
    private final long observationOriginNanos = System.nanoTime();
    private final PlayerObservation observations = new PlayerObservation(UUID.randomUUID(), observationOriginNanos);
    final ChunkWorld world = new ChunkWorld(observations.snapshot().connectionId(), observationOriginNanos);
    private int observationEvent;
    private final AtomicBoolean busy = new AtomicBoolean();
    final Object lifetime = new Object();
    private final Object sendLock = new Object();
    private VanillaControl control;
    private TagState playerTags;
    private boolean controlWriting;
    private boolean controlLoaded;
    private volatile Stage stage = Stage.NEW;
    private volatile Socket socket;
    private volatile boolean closed, expired, authenticated, encrypted, positionConfirmed;
    private volatile long deadlineNanos;
    private long operationGeneration;
    private ScheduledExecutorService timer;
    private ScheduledFuture<?> alarm;
    private volatile Future<?> pending;
    private InputStream in;
    private OutputStream out;
    private int compression = -1;
    private boolean playLogin, chunkBatch;
    private volatile boolean bundled;
    private int bundlePackets, batchChunks, registryEntries;
    private long bundleBytes, configBytes;
    private final Map<String, Integer> registries = new HashMap<>();
    private final Map<String, List<String>> registryKeys = new HashMap<>();
    private long registryKeyBytes;
    private record Dimension(int minY, int height) {}
    private List<Dimension> dimensionTypes = List.of();
    private Dimension activeDimension;
    private volatile long packetsReceived, packetsSent, keepAlives, teleportConfirmations;
    private double x, y, z;
    private float yaw, pitch;

    public VanillaConnection(UUID profileId, String playerName, SessionJoiner joiner) {
        this.profileId = Objects.requireNonNull(profileId);
        if (playerName == null || !playerName.matches("[A-Za-z0-9_]{1,16}"))
            throw new IllegalArgumentException("invalid_profile_name");
        this.playerName = playerName;
        this.joiner = Objects.requireNonNull(joiner);
    }
    public Set<String> capabilities() { return Set.of(); }
    VanillaControl enableControl(BlockCatalog catalog) {
        return enableControl(catalog,false);
    }
    VanillaControl enableControl(BlockCatalog catalog,boolean advanced) {
        return enableControl(catalog,advanced,false);
    }
    VanillaControl enablePlayerControl(BlockCatalog catalog) {
        return enableControl(catalog,true,true);
    }
    private VanillaControl enableControl(BlockCatalog catalog,boolean advanced,boolean player) {
        synchronized (lifetime) {
            if (stage != Stage.NEW || closed || control != null) throw new IllegalStateException("control_already_started");
            control = new VanillaControl(this, catalog,advanced,player);if(player)playerTags=new TagState();return control;
        }
    }
    boolean controlReady() { return stage == Stage.PLAY && playLogin && positionConfirmed && !bundled && !closed; }
    boolean controlSuspended() { return stage == Stage.PLAY && bundled && !closed; }
    Map<String,Integer> componentRegistries(){synchronized(lifetime){return Map.copyOf(registries);}}
    TagState.Snapshot tagSnapshot(){synchronized(lifetime){return playerTags==null?null:playerTags.snapshot();}}
    /** Current configuration's ordered reference keys only; entry NBT/default semantics are not retained. */
    Map<String,List<String>> componentRegistryKeys(){synchronized(lifetime){return Map.copyOf(registryKeys);}}
    void fenceControlWrite() { if (controlWriting) close(); }
    public PlayerObservation.Snapshot observation() {
        synchronized (lifetime) {
            PlayerObservation.Snapshot self = observations.snapshot();
            if (!self.available()) return self;
            List<ChunkWorld.BlockPos> points = new ArrayList<>();
            PlayerObservation.Position pos = self.position();
            if (pos != null && pos.x() >= -33554431 && pos.x() < 33554431
                    && pos.z() >= -33554431 && pos.z() < 33554431 && pos.y() >= -2047 && pos.y() < 2047) {
                int px = (int)Math.floor(pos.x()), py = (int)Math.floor(pos.y()), pz = (int)Math.floor(pos.z());
                for (int dy = -1; dy <= 1; dy++) for (int dz = -1; dz <= 1; dz++) for (int dx = -1; dx <= 1; dx++)
                    points.add(new ChunkWorld.BlockPos(px + dx, py + dy, pz + dz));
            }
            return new PlayerObservation.Snapshot(self.connectionId(), self.generation(), self.sequence(), self.eventSequence(),
                    self.observedAtNanos(), true, self.dimension(), self.position(), self.health(), world.sample(points));
        }
    }
    public long observationOriginNanos() { return observationOriginNanos; }
    public Snapshot snapshot() {
        return new Snapshot(stage, authenticated && encrypted, positionConfirmed && !bundled,
                false, packetsReceived, packetsSent, keepAlives, teleportConfirmations);
    }

    /** One attempt only. Status/version check and admission share one absolute deadline. */
    public Snapshot connect(String host, int port, Duration admissionTimeout) throws IOException {
        if (host == null || host.isBlank() || host.length() > 255 || host.indexOf('\0') >= 0 || port < 1 || port > 65535)
            throw new IllegalArgumentException("invalid_endpoint");
        begin(admissionTimeout);
        try {
            if (stage != Stage.NEW) throw fail("already_started");
            InetAddress address = bounded(() -> InetAddress.getByName(host), "address_resolution_failed");
            openSocket(address, port);
            send(handshake(host, port, 1));
            send(new byte[]{0});
            Cursor status = new Cursor(decodeFrame(in, -1));
            if (status.vi() != 0) throw fail("invalid_status_packet");
            validateStatus(status.string(32767)); status.end();
            socket.close(); socket = null;
            openSocket(address, port);
            transition(Stage.LOGIN);
            send(handshake(host, port, 2));
            Writer hello = new Writer(0); hello.string(playerName); hello.uuid(profileId); send(hello.bytes());
            while (!(stage == Stage.PLAY && playLogin && positionConfirmed && !bundled)) receive();
            ensureOpen();
            return snapshot();
        } catch (Exception e) {
            close(); throw sanitized(e, "connection_failed");
        } finally { endOperation(); }
    }

    /** Handles only mandatory traffic, then closes this one-shot connection at the deadline. */
    public Snapshot maintain(Duration duration) throws IOException {
        begin(duration);
        try {
            if (stage != Stage.PLAY || !playLogin || !positionConfirmed) throw fail("not_admitted");
            while (true) receive();
        } catch (Exception e) {
            if (expired && (!(e instanceof Failure f) || f.code().equals("deadline_exceeded") || f.code().equals("connection_closed"))) {
                close(); return snapshot();
            }
            close(); throw sanitized(e, "connection_failed");
        } finally { endOperation(); }
    }

    private void begin(Duration duration) throws IOException {
        if (duration == null || duration.isNegative() || duration.isZero() || duration.compareTo(Duration.ofMinutes(2)) > 0)
            throw new IllegalArgumentException("invalid_duration");
        if (!busy.compareAndSet(false, true)) throw fail("operation_in_progress");
        synchronized (lifetime) {
            if (closed) { busy.set(false); throw fail("connection_closed"); }
            expired = false;
            deadlineNanos = System.nanoTime() + duration.toNanos();
            long generation = ++operationGeneration;
            if (timer == null) timer = Executors.newSingleThreadScheduledExecutor(r -> {
                Thread t = new Thread(r, "minecraft-admission-deadline"); t.setDaemon(true); return t;
            });
            alarm = timer.schedule(() -> {
                synchronized (lifetime) {
                    if (generation != operationGeneration || !busy.get()) return;
                    expired = true;
                    close();
                }
            }, duration.toNanos(), TimeUnit.NANOSECONDS);
        }
    }
    private void endOperation() {
        synchronized (lifetime) {
            ++operationGeneration;
            if (alarm != null) alarm.cancel(false);
            busy.set(false);
        }
    }
    private int remainingMillis() throws IOException {
        ensureOpen(); long remaining = deadlineNanos - System.nanoTime();
        if (remaining <= 0) throw fail("deadline_exceeded");
        return (int)Math.min(Integer.MAX_VALUE, Math.max(1, TimeUnit.NANOSECONDS.toMillis(remaining)));
    }
    private <T> T bounded(Callable<T> task, String code) throws IOException {
        FutureTask<T> future = new FutureTask<>(task);
        pending = future;
        Thread.ofVirtual().name("minecraft-admission-operation").start(future);
        try { return future.get(remainingMillis(), TimeUnit.MILLISECONDS); }
        catch (InterruptedException e) { Thread.currentThread().interrupt(); throw fail("interrupted"); }
        catch (TimeoutException e) { throw fail("deadline_exceeded"); }
        catch (ExecutionException | CancellationException e) { throw fail(code); }
        finally { future.cancel(true); pending = null; }
    }
    private void openSocket(InetAddress address, int port) throws IOException {
        Socket next = new Socket();
        synchronized (lifetime) { ensureOpen(); socket = next; }
        next.connect(new InetSocketAddress(address, port), remainingMillis());
        next.setTcpNoDelay(true);
        in = next.getInputStream(); out = next.getOutputStream();
    }
    private void ensureOpen() throws IOException {
        if (!closed && busy.get() && System.nanoTime() - deadlineNanos >= 0) { expired = true; close(); }
        if (closed) throw fail(expired ? "deadline_exceeded" : "connection_closed");
    }
    private void transition(Stage next) throws IOException {
        synchronized (lifetime) { ensureOpen();if(next==Stage.CONFIGURATION){registryKeys.clear();registryKeyBytes=0;if(playerTags!=null){playerTags.clear();control.flowTags(null,null);}}stage = next; }
    }
    @Override public void close() {
        synchronized (lifetime) {
            if (closed) return;
            closed = true; stage = Stage.CLOSED;
            registryKeys.clear();registryKeyBytes=0;
            if(playerTags!=null)playerTags.clear();
            Future<?> work = pending; if (work != null) work.cancel(true);
            Socket active = socket;
            if (active != null) try { active.close(); } catch (IOException ignored) {}
            if (alarm != null) alarm.cancel(false);
            if (timer != null) timer.shutdownNow();
            long now = System.nanoTime(); observations.invalidate(++observationEvent, now);
            world.close(worldEvent(now)); dimensionTypes = List.of(); activeDimension = null;
            if (control != null) control.closed(now);
        }
    }
    private static Failure fail(String code) { return new Failure(code); }
    private Failure sanitized(Exception e, String fallback) {
        return e instanceof Failure f ? f : fail(expired ? "deadline_exceeded" : fallback);
    }
    private void receive() throws IOException {
        ensureOpen(); byte[] packet = decodeFrame(in, compression); ensureOpen(); handlePacket(packet);
    }
    private void send(byte[] packet) throws IOException {
        synchronized (sendLock) {
            ensureOpen(); out.write(encodeFrame(packet, compression)); out.flush(); ensureOpen(); packetsSent++;
        }
    }
    boolean sendControl(MovementController.PreparedStep step, java.util.function.BooleanSupplier permit, Runnable commit) throws IOException {
        Writer input = new Writer(0x2b); input.data.writeByte((step.forward()?1:0)|(step.jump()?16:0)|(step.sneak()?32:0));
        Writer pose = new Writer(0x1f); MovementController.Pose p=step.pose();
        pose.data.writeDouble(p.x()); pose.data.writeDouble(p.y()); pose.data.writeDouble(p.z());
        pose.data.writeFloat(p.yaw()); pose.data.writeFloat(p.pitch()); pose.data.writeByte((step.onGround()?1:0)|(step.horizontalCollision()?2:0));
        synchronized (sendLock) {
            byte[] a=encodeFrame(input.bytes(),compression), b=encodeFrame(pose.bytes(),compression);
            synchronized (lifetime) { ensureOpen(); if (!controlReady() || !permit.getAsBoolean()) return false; controlWriting=true; }
            try {
                // The cipher advances only here. Cancellation after this commit closes the owned socket.
                if(!controlLoaded)out.write(encodeFrame(new byte[]{0x2c},compression));
                out.write(a); out.write(b); out.write(encodeFrame(new byte[]{0x0d},compression)); out.flush();
                synchronized (lifetime) {
                    ensureOpen(); if (!permit.getAsBoolean()) { close(); throw fail("control_commit_invalidated"); }
                    commit.run(); packetsSent+=controlLoaded?3:4; controlLoaded=true;
                }
                return true;
            } catch (IOException | RuntimeException failure) { close(); throw failure; }
            finally { synchronized (lifetime) { controlWriting=false; } }
        }
    }
    boolean sendInventoryClick(InventoryTransfer.Click click,java.util.function.BooleanSupplier before,java.util.function.BooleanSupplier after,Runnable markPending)throws IOException {
        PlayerActionPackets.Packet packet=MenuPackets.pickup(click.containerId(),click.stateId(),click.slot(),click.right()?MenuPackets.MouseButton.RIGHT:MenuPackets.MouseButton.LEFT,click.carried());
        Writer intent=new Writer(packet.id());intent.data.write(packet.payload());
        synchronized(sendLock){
            byte[] encoded=encodeFrame(intent.bytes(),compression);
            synchronized(lifetime){ensureOpen();if(!controlReady()||!before.getAsBoolean()||!controlReady())return false;controlWriting=true;}
            try{
                // Mark pending before bytes can provoke an immediate server reply; no local inventory prediction is applied.
                // A response bundle can begin between validation phases or during a guard callback.
                synchronized(lifetime){ensureOpen();if(!controlReady()||!before.getAsBoolean()||!controlReady())return false;markPending.run();}
                if(!controlLoaded)out.write(encodeFrame(new byte[]{0x2c},compression));
                out.write(encodeFrame(new byte[]{0x2b,0},compression));out.write(encoded);out.write(encodeFrame(new byte[]{0x0d},compression));out.flush();
                synchronized(lifetime){ensureOpen();if(!after.getAsBoolean()){close();throw fail("inventory_commit_invalidated");}
                    packetsSent+=controlLoaded?3:4;controlLoaded=true;}return true;
            }catch(IOException|RuntimeException failure){close();throw failure;}
            finally{synchronized(lifetime){controlWriting=false;}}
        }
    }
    boolean sendHandSwap(java.util.function.BooleanSupplier before,java.util.function.BooleanSupplier after,Runnable markPending)throws IOException {
        PlayerActionPackets.Packet packet=PlayerActionPackets.swapHands(0);Writer intent=new Writer(packet.id());intent.data.write(packet.payload());
        synchronized(sendLock){
            byte[] encoded=encodeFrame(intent.bytes(),compression);
            synchronized(lifetime){ensureOpen();if(!controlReady()||!before.getAsBoolean()||!controlReady())return false;controlWriting=true;}
            try{
                // Require fresh PLAY readiness at the pending commit, while post-write replies may be bundled.
                synchronized(lifetime){ensureOpen();if(!controlReady()||!before.getAsBoolean()||!controlReady())return false;markPending.run();}
                if(!controlLoaded)out.write(encodeFrame(new byte[]{0x2c},compression));
                out.write(encodeFrame(new byte[]{0x2b,0},compression));out.write(encoded);out.write(encodeFrame(new byte[]{0x0d},compression));out.flush();
                synchronized(lifetime){ensureOpen();if(!after.getAsBoolean()){close();throw fail("hand_swap_commit_invalidated");}
                    packetsSent+=controlLoaded?3:4;controlLoaded=true;}return true;
            }catch(IOException|RuntimeException failure){close();throw failure;}
            finally{synchronized(lifetime){controlWriting=false;}}
        }
    }
    boolean sendAttack(int target,MovementController.Pose aim,java.util.function.BooleanSupplier permit,Runnable commit)throws IOException {
        Writer attack=new Writer(0x01);attack.vi(target);
        Writer pose=new Writer(0x1f);pose.data.writeDouble(aim.x());pose.data.writeDouble(aim.y());pose.data.writeDouble(aim.z());
        pose.data.writeFloat(aim.yaw());pose.data.writeFloat(aim.pitch());pose.data.writeByte(1);
        synchronized(sendLock){
            byte[] a=encodeFrame(attack.bytes(),compression),b=encodeFrame(new byte[]{0x2e},compression);
            byte[] facing=encodeFrame(pose.bytes(),compression),neutral=encodeFrame(new byte[]{0x2b,0},compression);
            synchronized(lifetime){ensureOpen();if(!controlReady()||!permit.getAsBoolean())return false;controlWriting=true;}
            try{
                if(!controlLoaded)out.write(encodeFrame(new byte[]{0x2c},compression));
                out.write(neutral);out.write(facing);out.write(a);out.write(b);out.write(encodeFrame(new byte[]{0x0d},compression));out.flush();
                synchronized(lifetime){ensureOpen();if(!permit.getAsBoolean()){close();throw fail("attack_commit_invalidated");}
                    commit.run();packetsSent+=controlLoaded?5:6;controlLoaded=true;}return true;
            }catch(IOException|RuntimeException failure){close();throw failure;}
            finally{synchronized(lifetime){controlWriting=false;}}
        }
    }
    private static byte[] handshake(String host, int port, int intent) throws IOException {
        Writer w = new Writer(0); w.vi(PROTOCOL); w.string(host); w.data.writeShort(port); w.vi(intent); return w.bytes();
    }

    private void handlePacket(byte[] packet) throws IOException {
        ensureOpen();
        if (packet.length > MAX_PACKET || ++packetsReceived > 200000) throw fail("packet_budget");
        Cursor c = new Cursor(packet); int id = c.vi();
        if (id < 0) throw fail("invalid_packet_id");
        switch (stage) {
            case LOGIN -> login(id, c);
            case CONFIGURATION -> configuration(id, c);
            case PLAY -> play(id, c, packet.length);
            default -> throw fail("unexpected_state");
        }
    }
    private void login(int id, Cursor c) throws IOException {
        switch (id) {
            case 0 -> throw fail("server_login_disconnect");
            case 1 -> encryption(c);
            case 2 -> {
                if (!authenticated || !encrypted) throw fail("online_authentication_required");
                UUID receivedId = c.uuid(); String receivedName = c.string(16);
                int properties = c.count(16);
                for (int i = 0; i < properties; i++) {
                    c.string(64); c.string(32767); if (c.bool()) c.string(1024);
                }
                c.uuid(); c.end(); // 26.3 appends a session UUID after GameProfile.
                if (!profileId.equals(receivedId) || !playerName.equals(receivedName)) throw fail("profile_mismatch");
                send(new byte[]{3}); transition(Stage.CONFIGURATION); clientInformation();
            }
            case 3 -> { int threshold = c.vi(); c.end(); if (threshold < 0 || threshold > MAX_PACKET) throw fail("invalid_compression_threshold"); compression = threshold; }
            case 4 -> { int transaction = c.vi(); c.identifier(); c.discard(1048576); Writer w = new Writer(2); w.vi(transaction); w.data.writeBoolean(false); send(w.bytes()); }
            case 5 -> cookie(c, 4);
            default -> throw fail("unsupported_login_packet");
        }
    }
    private void encryption(Cursor c) throws IOException {
        if (encrypted || authenticated) throw fail("duplicate_encryption_request");
        String serverId = c.string(20); byte[] encodedKey = c.array(1024); byte[] challenge = c.array(1024);
        boolean authenticate = c.bool(); c.end();
        if (!authenticate || challenge.length == 0) throw fail("online_authentication_required");
        byte[] secretBytes = null;
        try {
            PublicKey publicKey = KeyFactory.getInstance("RSA").generatePublic(new X509EncodedKeySpec(encodedKey));
            if (!(publicKey instanceof RSAPublicKey rsa) || rsa.getModulus().bitLength() < 1024 || rsa.getModulus().bitLength() > 4096)
                throw fail("invalid_server_key");
            KeyGenerator generator = KeyGenerator.getInstance("AES"); generator.init(128);
            SecretKey secret = generator.generateKey(); secretBytes = secret.getEncoded();
            MessageDigest digest = MessageDigest.getInstance("SHA-1");
            digest.update(serverId.getBytes(StandardCharsets.ISO_8859_1)); digest.update(secretBytes); digest.update(encodedKey);
            String hash = new BigInteger(digest.digest()).toString(16);
            bounded(() -> { joiner.joinServer(hash); return null; }, "session_join_failed");
            ensureOpen();
            Cipher rsaCipher = Cipher.getInstance("RSA/ECB/PKCS1Padding"); rsaCipher.init(Cipher.ENCRYPT_MODE, publicKey);
            Writer key = new Writer(1); key.array(rsaCipher.doFinal(secretBytes)); key.array(rsaCipher.doFinal(challenge));
            // The key reply is the final plaintext frame. No input buffering crosses this boundary.
            send(key.bytes());
            Cipher decrypt = Cipher.getInstance("AES/CFB8/NoPadding"), encrypt = Cipher.getInstance("AES/CFB8/NoPadding");
            IvParameterSpec iv = new IvParameterSpec(secretBytes);
            decrypt.init(Cipher.DECRYPT_MODE, secret, iv); encrypt.init(Cipher.ENCRYPT_MODE, secret, iv);
            in = new CipherInputStream(in, decrypt); out = new CipherOutputStream(out, encrypt);
            authenticated = true; encrypted = true;
        } catch (Failure e) { throw e; }
        catch (GeneralSecurityException e) { throw fail("encryption_failed"); }
        finally { if (secretBytes != null) Arrays.fill(secretBytes, (byte)0); Arrays.fill(challenge, (byte)0); }
    }
    private void clientInformation() throws IOException {
        Writer w = new Writer(0); w.string("en_us"); w.data.writeByte(2); w.vi(2);
        w.data.writeBoolean(false); w.data.writeByte(0); w.vi(1);
        w.data.writeBoolean(false); w.data.writeBoolean(false); w.vi(2); send(w.bytes());
    }
    private void configuration(int id, Cursor c) throws IOException {
        configBytes += c.size(); if (configBytes > MAX_CONFIG_BYTES) throw fail("configuration_budget");
        switch (id) {
            case 0 -> cookie(c, 1);
            case 1 -> { c.identifier(); c.discard(1048576); }
            case 2 -> throw fail("server_configuration_disconnect");
            case 3 -> {
                c.end();
                if (!registries.containsKey("minecraft:dimension_type")) throw fail("missing_dimension_registry");
                send(new byte[]{3}); transition(Stage.PLAY);
            }
            case 4 -> keepalive(c, 4);
            case 5 -> pong(c, 5);
            case 6, 18 -> c.end();
            case 7 -> registry(c);
            case 8, 9 -> throw fail("resource_pack_unsupported");
            case 10, 16, 17 -> c.discard(MAX_PACKET); // Presentation/reporting only; never acted on or retained.
            case 11 -> storeCookie(c);
            case 12 -> throw fail("server_transfer_unsupported");
            case 13 -> { int n = c.count(1024); for (int i = 0; i < n; i++) c.identifier(); c.end(); }
            case 14 -> tags(c);
            case 15 -> {
                int n = c.count(64); for (int i = 0; i < n; i++) { c.string(32767); c.string(32767); c.string(32767); }
                c.end(); send(new byte[]{7, 0}); // No locally installed known packs are claimed.
            }
            case 19 -> throw fail("consent_ui_unsupported");
            case 20 -> throw fail("code_of_conduct_required");
            default -> throw fail("unsupported_configuration_packet");
        }
    }
    private void registry(Cursor c) throws IOException {
        String name = c.identifier(); int count = c.count(65536);
        if (registries.containsKey(name) || registries.size() >= 256 || registryEntries + count > 100000)
            throw fail("registry_budget");
        Set<String> entryNames = new HashSet<>();
        List<String> keys = new ArrayList<>();
        // Identifiers admit ASCII only, so their character count is the retained UTF-8 byte count.
        long keyBytes=name.length();if(keyBytes>MAX_CONFIG_BYTES-registryKeyBytes)throw fail("registry_key_budget");
        List<Dimension> dimensions = new ArrayList<>();
        for (int i = 0; i < count; i++) {
            String key=c.identifier();keyBytes+=key.length();if(keyBytes>MAX_CONFIG_BYTES-registryKeyBytes)throw fail("registry_key_budget");
            if (!entryNames.add(key)) throw fail("duplicate_registry_entry");keys.add(key);
            if (!c.bool()) throw fail("missing_registry_data");
            int tag = c.data.readUnsignedByte(); if (tag == 0) throw fail("missing_registry_data");
            if (name.equals("minecraft:dimension_type")) dimensions.add(dimension(c, tag)); else c.nbt(tag, 0);
        }
        c.end();
        synchronized (lifetime) {
            ensureOpen();if(keyBytes>MAX_CONFIG_BYTES-registryKeyBytes)throw fail("registry_key_budget");
            registries.put(name, count);registryKeys.put(name,List.copyOf(keys));registryEntries += count;registryKeyBytes+=keyBytes;
            if(playerTags!=null){playerTags.clear();control.flowTags(null,null);}
            if (name.equals("minecraft:dimension_type")) dimensionTypes = List.copyOf(dimensions);
        }
    }
    private static Dimension dimension(Cursor c, int tag) throws IOException {
        if (tag != 10 || ++c.nbtNodes > 65536) throw fail("invalid_dimension_type");
        Integer minY = null, height = null;
        while (true) {
            int child = c.data.readUnsignedByte(); if (child == 0) break;
            String key = c.data.readUTF();
            if (key.equals("min_y") || key.equals("height")) {
                if (child != 3 || ++c.nbtNodes > 65536) throw fail("invalid_dimension_type");
                int value = c.data.readInt();
                if (key.equals("min_y")) { if (minY != null) throw fail("invalid_dimension_type"); minY = value; }
                else { if (height != null) throw fail("invalid_dimension_type"); height = value; }
            } else c.nbt(child, 1);
        }
        if (minY == null || height == null || minY < -2032 || minY > 2031 || height < 16 || height > 4064
                || minY % 16 != 0 || height % 16 != 0 || minY + height > 2032) throw fail("invalid_dimension_type");
        return new Dimension(minY, height);
    }
    private void tags(Cursor c) throws IOException {
        if(playerTags!=null){
            synchronized(lifetime){
                ensureOpen();
                try{
                    Map<String,Integer> extents=new HashMap<>(registries);
                    // Structural IDs of the supported vanilla777 version; tag membership always comes from the server.
                    extents.put("minecraft:block",control.catalog.blockCount());extents.put("minecraft:item",ItemCatalog.ITEM_COUNT);
                    extents.put("minecraft:fluid",5);extents.put("minecraft:entity_type",control.catalog.entityTypeCount());
                    playerTags.accept(playerTags.snapshot().generation(),c,extents);TagState.Snapshot current=playerTags.snapshot();
                    if(current.duplicateMembers("minecraft:block","minecraft:blocks_fluid_flow")||current.duplicateMembers("minecraft:fluid","minecraft:water"))throw fail("duplicate_tag_entry");
                    control.flowTags(current.members("minecraft:block","minecraft:blocks_fluid_flow").orElse(null),current.members("minecraft:fluid","minecraft:water").orElse(null));
                }catch(IOException|RuntimeException malformed){control.flowTags(null,null);close();throw malformed;}
            }
            return;
        }
        int registries = c.count(256), total = 0;
        boolean strict=control!=null&&control.advanced();Set<Integer> flow=null,water=null;Set<String> seenRegistries=new HashSet<>();
        for (int r = 0; r < registries; r++) {
            String registry=c.identifier();if(strict&&!seenRegistries.add(registry))throw fail("duplicate_tag_registry");int tags = c.count(65536);Set<String> names=new HashSet<>();
            for (int t = 0; t < tags; t++) {
                String name=c.identifier();if(strict&&!names.add(name))throw fail("duplicate_tag");int entries = c.count(1000000); total += entries;
                if (total > 1000000) throw fail("tags_budget");
                boolean blockTag=strict&&registry.equals("minecraft:block")&&name.equals("minecraft:blocks_fluid_flow");
                boolean waterTag=strict&&registry.equals("minecraft:fluid")&&name.equals("minecraft:water");
                Set<Integer> selected=blockTag||waterTag?new HashSet<>():null;if(blockTag)flow=selected;if(waterTag)water=selected;
                for (int e = 0; e < entries; e++){int entry=c.count(Integer.MAX_VALUE);if(selected!=null&&!selected.add(entry))throw fail("duplicate_tag_entry");}
            }
        }
        c.end();
        if(control!=null)synchronized(lifetime){ensureOpen();control.flowTags(flow,water);}
    }
    private void play(int id, Cursor c, int packetBytes) throws IOException {
        if (id > 143) throw fail("unsupported_play_packet");
        if (id == 0) {
            c.end();
            synchronized (lifetime) {
                ensureOpen();
                if (bundled) { world.endBundle(); observations.endBundle(); if(control!=null) control.endBundle(); }
                else { world.beginBundle(); observations.beginBundle(); if(control!=null) control.beginBundle(); }
                bundled = !bundled; bundlePackets = 0; bundleBytes = 0;
            }
            return;
        }
        if (bundled && (++bundlePackets > 4096 || (bundleBytes += packetBytes) > 16L * 1024 * 1024)) throw fail("bundle_budget");
        if (control != null) synchronized (lifetime) {
            ensureOpen();
            try { if (control.packet(id,c)) return; }
            catch(IOException | RuntimeException failure) { close();throw failure; }
        }
        switch (id) {
            case 0x08, 0x26, 0x2e, 0x56 -> worldPacket(id, c);
            case 0x0b -> {
                int count = c.count(65536); c.end();
                if (!chunkBatch || count != batchChunks) throw fail("invalid_chunk_batch");
                chunkBatch = false; Writer w = new Writer(0x0b); w.data.writeFloat(1.0f); send(w.bytes());
            }
            case 0x0c -> { c.end(); if (chunkBatch) throw fail("invalid_chunk_batch"); chunkBatch = true; batchChunks = 0; }
            case 0x15 -> cookie(c, 0x15);
            case 0x20 -> throw fail("server_play_disconnect");
            case 0x27 -> {
                int type=c.data.readUnsignedByte();float value=c.finiteFloat();c.end();
                if(control!=null)synchronized(lifetime){ensureOpen();control.gameEvent(type,value);}
            }
            case 0x2d -> keepalive(c, 0x1c);
            case 0x32 -> playLogin(c);
            case 0x3e -> pong(c, 0x2d);
            case 0x49 -> position(c);
            case 0x4a -> {
                if(control==null) throw fail("unsupported_rotation");
                float ry=c.finiteFloat(); boolean relativeYaw=c.bool(); float rp=c.finiteFloat(); boolean relativePitch=c.bool(); c.end();
                synchronized(lifetime) { ensureOpen(); control.rotation(ry,relativeYaw,rp,relativePitch); }
            }
            case 0x51, 0x52 -> throw fail("resource_pack_unsupported");
            case 0x54 -> throw fail("respawn_unsupported");
            case 0x6a -> {
                if (!playLogin) throw fail("health_before_play_login");
                float health = c.finiteFloat(); int food = c.count(Integer.MAX_VALUE); float saturation = c.finiteFloat(); c.end();
                if (health < 0 || saturation < 0) throw fail("invalid_health");
                synchronized (lifetime) {
                    ensureOpen(); observations.health(++observationEvent, System.nanoTime(), health, food, saturation);
                    if(control!=null) control.health(health);
                }
            }
            case 0x78 -> {
                c.end(); if (bundled) throw fail("invalid_reconfiguration");
                send(new byte[]{0x10}); playLogin = false; positionConfirmed = false; chunkBatch = false;
                synchronized (lifetime) {
                    ensureOpen(); registries.clear();registryKeys.clear();registryKeyBytes=0; dimensionTypes = List.of(); activeDimension = null; registryEntries = 0; configBytes = 0;
                    long now = System.nanoTime(); observations.invalidate(++observationEvent, now); world.invalidate(worldEvent(now));
                    if(control!=null) { control.off(); throw fail("control_world_changed"); }
                }
                transition(Stage.CONFIGURATION);
            }
            case 0x7a -> storeCookie(c);
            case 0x84 -> throw fail("server_transfer_unsupported");
            case 0x89 -> tags(c);
            case 0x8f -> throw fail("consent_ui_unsupported");
            default -> c.discard(MAX_PACKET); // Known 26.3 play packet; no observation, persistence or action.
        }
    }
    private void playLogin(Cursor c) throws IOException {
        if (playLogin) throw fail("duplicate_play_login");
        int entityId=c.data.readInt(); c.bool(); int dimensions = c.count(1024); Set<String> names = new HashSet<>();
        for (int i = 0; i < dimensions; i++) if (!names.add(c.identifier())) throw fail("duplicate_dimension");
        c.count(Integer.MAX_VALUE); c.count(1024); c.count(1024); c.bool(); c.bool(); c.bool();
        int dimensionType = c.count(65536); String dimension = c.identifier();
        if (dimensionType >= registries.getOrDefault("minecraft:dimension_type", 0) || !names.contains(dimension)) throw fail("unknown_dimension");
        c.data.readLong(); int gameMode=c.count(3); c.count(4); c.bool(); c.bool();
        if (c.bool()) { c.identifier(); c.data.readLong(); }
        c.count(Integer.MAX_VALUE); c.vi(); boolean online = c.bool(); c.bool(); c.end();
        if (!online || !authenticated || !encrypted) throw fail("online_authentication_required");
        synchronized (lifetime) {
            ensureOpen();
            if (dimensionType >= dimensionTypes.size()) throw fail("missing_dimension_type");
            activeDimension = dimensionTypes.get(dimensionType);
            long now = System.nanoTime(); observations.player(++observationEvent, now, dimension);
            world.bind(worldEvent(now), dimension, activeDimension.minY(), activeDimension.height()); playLogin = true;
            if(control!=null) control.player(entityId,gameMode);
        }
    }
    private ChunkWorld.Event worldEvent(long now) {
        return new ChunkWorld.Event(observations.snapshot().generation(), observationEvent, now);
    }
    private void worldPacket(int id, Cursor c) throws IOException {
        int generation, sections, biomes;
        synchronized (lifetime) {
            ensureOpen(); if (!playLogin || activeDimension == null) throw fail("world_before_play_login");
            generation = observations.snapshot().generation(); sections = activeDimension.height() / 16;
            biomes = registries.getOrDefault("minecraft:worldgen/biome", 0);
        }
        Object decoded;
        if(control!=null&&control.playerControl()&&(id==0x08||id==0x56)){
            try{decoded=control.blockPacket(id,c);}catch(IOException|RuntimeException malformed){close();throw malformed;}
        }else decoded = switch (id) {
            case 0x08 -> ChunkCodec.decodeBlock(c);
            case 0x26 -> ChunkCodec.decodeUnload(c);
            case 0x56 -> ChunkCodec.decodeSection(c);
            default -> ChunkCodec.decodeChunk(c, sections, biomes, control == null ? 0 : control.catalog.stateCount());
        };
        synchronized (lifetime) {
            ensureOpen(); if (observations.snapshot().generation() != generation) throw fail("stale_world_packet");
            long now = System.nanoTime(); observationEvent++; ChunkWorld.Event event = worldEvent(now);
            switch (decoded) {
                case ChunkCodec.Chunk chunk -> {
                    if (chunk.sections() == null) world.unload(event, chunk.x(), chunk.z());
                    else world.replaceChunk(event, chunk.x(), chunk.z(), chunk.sections());
                    if (chunkBatch && ++batchChunks > 65536) throw fail("chunk_budget");
                }
                case ChunkCodec.BlockUpdate block -> world.updateBlock(event, block.x(), block.y(), block.z(), block.stateId());
                case ChunkCodec.SectionUpdate section -> world.updateSection(event, section.x(), section.y(), section.z(), section.positions(), section.states());
                case BlockInteractionState.BlockChange block -> world.updateBlock(event,block.position().x(),block.position().y(),block.position().z(),block.stateId());
                case BlockInteractionState.SectionChange section -> {
                    int[] positions=new int[section.cells().size()],states=new int[positions.length];
                    for(int n=0;n<positions.length;n++){positions[n]=section.cells().get(n).packedPosition();states[n]=section.cells().get(n).stateId();}
                    world.updateSection(event,section.x(),section.y(),section.z(),positions,states);
                }
                case ChunkCodec.ChunkPos chunk -> world.unload(event, chunk.x(), chunk.z());
                default -> throw fail("invalid_world_packet");
            }
            observations.touch(observationEvent, now);
            if(control!=null) control.environmentChanged();
        }
    }
    private void position(Cursor c) throws IOException {
        if (!playLogin) throw fail("position_before_play_login");
        int teleportId = c.vi(); double px = c.finiteDouble(), py = c.finiteDouble(), pz = c.finiteDouble();
        double vx=c.finiteDouble(),vy=c.finiteDouble(),vz=c.finiteDouble();
        float ry = c.finiteFloat(), rp = c.finiteFloat(); int flags = c.data.readInt(); c.end();
        if ((flags & ~0x1ff) != 0 || (!positionConfirmed && flags != 0)) throw fail("unsupported_relative_position");
        if(control!=null) {
            synchronized(sendLock) {
                double ax,ay,az;float ar,ap;
                synchronized(lifetime) {
                    ensureOpen(); MovementController.Pose base=control.pose(); MovementController.Velocity velocity=control.velocity();
                    if(base==null&&flags!=0)throw fail("unsupported_relative_position");
                    ax=px+((flags&1)!=0?base.x():0);ay=py+((flags&2)!=0?base.y():0);az=pz+((flags&4)!=0?base.z():0);
                    ar=ry+((flags&8)!=0?base.yaw():0);ap=Math.max(-90,Math.min(90,rp+((flags&16)!=0?base.pitch():0)));
                    if((flags&256)!=0&&(velocity.x()!=0||velocity.y()!=0||velocity.z()!=0))throw fail("unsupported_velocity_rotation");
                    control.correction(new MovementController.Pose(ax,ay,az,ar,ap),new MovementController.Velocity(
                        vx+((flags&32)!=0?velocity.x():0),vy+((flags&64)!=0?velocity.y():0),vz+((flags&128)!=0?velocity.z():0)),System.nanoTime());
                }
                Writer w=new Writer(0);w.vi(teleportId);w.data.writeDouble(ax);w.data.writeDouble(ay);w.data.writeDouble(az);w.data.writeFloat(ar);w.data.writeFloat(ap);send(w.bytes());
                synchronized(lifetime) {
                    ensureOpen();observations.position(++observationEvent,System.nanoTime(),ax,ay,az,ar,ap);
                    x=ax;y=ay;z=az;yaw=ar;pitch=ap;positionConfirmed=true;teleportConfirmations++;
                }
            }
            return;
        }
        double ax = px + ((flags & 1) != 0 ? x : 0), ay = py + ((flags & 2) != 0 ? y : 0), az = pz + ((flags & 4) != 0 ? z : 0);
        float ar = ry + ((flags & 8) != 0 ? yaw : 0), ap = rp + ((flags & 16) != 0 ? pitch : 0);
        if (!Double.isFinite(ax) || !Double.isFinite(ay) || !Double.isFinite(az) || !Float.isFinite(ar) || !Float.isFinite(ap)) throw fail("nonfinite_position");
        ap = Math.max(-90.0f, Math.min(90.0f, ap));
        Writer w = new Writer(0); w.vi(teleportId); w.data.writeDouble(ax); w.data.writeDouble(ay); w.data.writeDouble(az);
        w.data.writeFloat(ar); w.data.writeFloat(ap); send(w.bytes());
        synchronized (lifetime) {
            ensureOpen();
            observations.position(++observationEvent, System.nanoTime(), ax, ay, az, ar, ap);
            x = ax; y = ay; z = az; yaw = ar; pitch = ap; positionConfirmed = true; teleportConfirmations++;
        }
    }
    private void keepalive(Cursor c, int response) throws IOException {
        long value = c.data.readLong(); c.end(); Writer w = new Writer(response); w.data.writeLong(value); send(w.bytes()); keepAlives++;
    }
    private void pong(Cursor c, int response) throws IOException {
        int value = c.data.readInt(); c.end(); Writer w = new Writer(response); w.data.writeInt(value); send(w.bytes());
    }
    private void cookie(Cursor c, int response) throws IOException {
        String name = c.identifier(); c.end(); Writer w = new Writer(response); w.string(name); w.data.writeBoolean(false); send(w.bytes());
    }
    private static void storeCookie(Cursor c) throws IOException { c.identifier(); c.array(5120); c.end(); }

    private static int readVarInt(InputStream source, int maxBytes) throws IOException {
        int value = 0;
        for (int i = 0; i < maxBytes; i++) {
            int b = source.read(); if (b < 0) throw fail("truncated_packet");
            if (i == 4 && (b & 0xf0) != 0) throw fail("varint_overflow");
            value |= (b & 127) << (i * 7);
            if ((b & 128) == 0) {
                if (i > 0 && (b & 127) == 0) throw fail("noncanonical_varint");
                return value;
            }
        }
        throw fail("varint_overflow");
    }
    private static void writeVarInt(OutputStream target, int value) throws IOException {
        while ((value & ~127) != 0) { target.write((value & 127) | 128); value >>>= 7; }
        target.write(value);
    }
    private static byte[] decodeFrame(InputStream source, int threshold) throws IOException {
        int length = readVarInt(source, 3);
        if (length < 1 || length > MAX_FRAME) throw fail("frame_length");
        // CipherInputStream can fetch the next frame on the allocating overload's final zero-length read.
        byte[] frame = new byte[length];
        if (source.readNBytes(frame, 0, length) != length) throw fail("truncated_packet");
        if (threshold < 0) return frame;
        ByteArrayInputStream content = new ByteArrayInputStream(frame);
        int size = readVarInt(content, 5); byte[] payload = content.readAllBytes();
        if (size == 0) {
            if (payload.length == 0 || payload.length >= threshold) throw fail("compression_threshold");
            return payload;
        }
        if (size < threshold || size > MAX_PACKET || size < 1) throw fail("compression_length");
        Inflater inflater = new Inflater();
        try {
            inflater.setInput(payload); byte[] packet = new byte[size]; int written = 0;
            while (written < size && !inflater.finished()) {
                int n = inflater.inflate(packet, written, size - written);
                if (n == 0) throw fail("invalid_compression"); written += n;
            }
            // Drain the checksum when output exactly fills the requested buffer.
            if (!inflater.finished() && inflater.inflate(new byte[1]) != 0) throw fail("compression_length");
            if (written != size || !inflater.finished() || inflater.getRemaining() != 0) throw fail("invalid_compression");
            return packet;
        } catch (DataFormatException e) { throw fail("invalid_compression"); }
        finally { inflater.end(); }
    }
    private static byte[] encodeFrame(byte[] packet, int threshold) throws IOException {
        if (packet.length < 1 || packet.length > MAX_PACKET) throw fail("packet_length");
        ByteArrayOutputStream payload = new ByteArrayOutputStream();
        if (threshold < 0) payload.writeBytes(packet);
        else if (packet.length < threshold) { writeVarInt(payload, 0); payload.writeBytes(packet); }
        else {
            writeVarInt(payload, packet.length); Deflater deflater = new Deflater();
            try {
                deflater.setInput(packet); deflater.finish(); byte[] buffer = new byte[8192];
                while (!deflater.finished()) {
                    int n = deflater.deflate(buffer); if (n == 0) throw fail("invalid_compression"); payload.write(buffer, 0, n);
                    if (payload.size() > MAX_FRAME) throw fail("frame_length");
                }
            } finally { deflater.end(); }
        }
        if (payload.size() > MAX_FRAME) throw fail("frame_length");
        ByteArrayOutputStream frame = new ByteArrayOutputStream(); writeVarInt(frame, payload.size()); payload.writeTo(frame); return frame.toByteArray();
    }
    private static final class Writer {
        final ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        final DataOutputStream data = new DataOutputStream(bytes);
        Writer(int id) throws IOException { vi(id); }
        void vi(int v) throws IOException { writeVarInt(bytes, v); }
        void string(String s) throws IOException { array(s.getBytes(StandardCharsets.UTF_8)); }
        void array(byte[] b) throws IOException { vi(b.length); data.write(b); }
        void uuid(UUID id) throws IOException { data.writeLong(id.getMostSignificantBits()); data.writeLong(id.getLeastSignificantBits()); }
        byte[] bytes() { return bytes.toByteArray(); }
    }
    static final class Cursor {
        final ByteArrayInputStream bytes;
        final DataInputStream data;
        final int length;
        int nbtNodes;
        Cursor(byte[] packet) { bytes = new ByteArrayInputStream(packet); data = new DataInputStream(bytes); length = packet.length; }
        int size() { return length; }
        int vi() throws IOException { return readVarInt(bytes, 5); }
        int count(int max) throws IOException { int n = vi(); if (n < 0 || n > max) throw fail("count_budget"); return n; }
        boolean bool() throws IOException { int n = data.readUnsignedByte(); if (n > 1) throw fail("invalid_boolean"); return n == 1; }
        UUID uuid() throws IOException { return new UUID(data.readLong(), data.readLong()); }
        byte[] array(int max) throws IOException { int n = count(max); if (n > bytes.available()) throw fail("truncated_packet"); return data.readNBytes(n); }
        String string(int maxChars) throws IOException {
            byte[] encoded = array(maxChars * 3);
            try {
                String result = StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT).onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(encoded)).toString();
                if (result.length() > maxChars) throw fail("string_budget"); return result;
            } catch (CharacterCodingException e) { throw fail("invalid_utf8"); }
        }
        String identifier() throws IOException {
            String id = string(32767);
            if (!id.matches("[a-z0-9_.-]+:[a-z0-9/._-]+")) throw fail("invalid_identifier"); return id;
        }
        double finiteDouble() throws IOException { double d = data.readDouble(); if (!Double.isFinite(d)) throw fail("nonfinite_position"); return d; }
        float finiteFloat() throws IOException { float f = data.readFloat(); if (!Float.isFinite(f)) throw fail("nonfinite_position"); return f; }
        void end() throws IOException { if (bytes.available() != 0) throw fail("trailing_packet_data"); }
        void discard(int max) throws IOException { if (bytes.available() > max) throw fail("payload_budget"); bytes.skip(bytes.available()); }
        void skip(long n) throws IOException { if (n < 0 || n > bytes.available()) throw fail("truncated_nbt"); data.skipNBytes(n); }
        int nbtCount(int width) throws IOException { int n = data.readInt(); if (n < 0 || n > MAX_PACKET / width) throw fail("nbt_budget"); return n; }
        void nbt(int type, int depth) throws IOException {
            if (depth > 32 || ++nbtNodes > 65536) throw fail("nbt_budget");
            switch (type) {
                case 1 -> skip(1); case 2 -> skip(2); case 3, 5 -> skip(4); case 4, 6 -> skip(8);
                case 7 -> skip(nbtCount(1));
                case 8 -> skip(data.readUnsignedShort());
                case 9 -> {
                    int element = data.readUnsignedByte(), n = nbtCount(1);
                    if (element > 12 || element == 0 && n != 0 || n > 65536) throw fail("invalid_nbt");
                    for (int i = 0; i < n; i++) nbt(element, depth + 1);
                }
                case 10 -> {
                    while (true) { int child = data.readUnsignedByte(); if (child == 0) break; skip(data.readUnsignedShort()); nbt(child, depth + 1); }
                }
                case 11 -> skip((long)nbtCount(4) * 4);
                case 12 -> skip((long)nbtCount(8) * 8);
                default -> throw fail("invalid_nbt");
            }
        }
    }
    private static void validateStatus(String json) throws IOException {
        try (JsonReader reader = new JsonReader(new StringReader(json))) {
            reader.setStrictness(Strictness.STRICT); reader.beginObject(); boolean version = false; Set<String> fields = new HashSet<>(); int[] budget = {8192};
            while (reader.hasNext()) {
                String key = reader.nextName(); if (!fields.add(key)) throw fail("invalid_status");
                if (!key.equals("version")) { skipJson(reader, 0, budget); continue; }
                version = true; reader.beginObject(); String name = null, protocol = null; Set<String> versionFields = new HashSet<>();
                while (reader.hasNext()) {
                    String field = reader.nextName(); if (!versionFields.add(field)) throw fail("invalid_status");
                    if (field.equals("name") && reader.peek() == JsonToken.STRING) name = reader.nextString();
                    else if (field.equals("protocol") && reader.peek() == JsonToken.NUMBER) protocol = reader.nextString();
                    else skipJson(reader, 0, budget);
                }
                reader.endObject(); if (!"26.3".equals(name) || !"777".equals(protocol)) throw fail("server_version_mismatch");
            }
            reader.endObject(); if (!version || reader.peek() != JsonToken.END_DOCUMENT) throw fail("invalid_status");
        } catch (Failure e) { throw e; }
        catch (IOException | IllegalStateException e) { throw fail("invalid_status"); }
    }
    private static void skipJson(JsonReader r, int depth, int[] budget) throws IOException {
        if (depth > 32 || --budget[0] < 0) throw fail("status_budget");
        switch (r.peek()) {
            case BEGIN_ARRAY -> { r.beginArray(); while (r.hasNext()) skipJson(r, depth + 1, budget); r.endArray(); }
            case BEGIN_OBJECT -> { r.beginObject(); while (r.hasNext()) { r.nextName(); skipJson(r, depth + 1, budget); } r.endObject(); }
            case STRING, NUMBER -> r.nextString(); case BOOLEAN -> r.nextBoolean(); case NULL -> r.nextNull();
            default -> throw fail("invalid_status");
        }
    }
}
