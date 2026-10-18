package dev.evelyn.minecraft;

import java.time.Duration;
import java.io.InputStream;
import java.io.IOException;
import java.net.URI;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.FutureTask;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/** Explicit, one-shot authenticated admission check; no gameplay or automatic reconnect. */
public final class VanillaBot {
    record Arguments(String clientId, String host, int port, URI observerEndpoint, boolean controlMode, boolean advancedControlMode,boolean playerControlMode) { }

    static Arguments arguments(String[] args) {
        int count = args.length;
        URI observerEndpoint = null;
        boolean controlMode=false,advancedControlMode=false,playerControlMode=false;
        if (count >= 3 && (args[count - 2].equals("--observe") || args[count - 2].equals("--control") || args[count-2].equals("--advanced-control")||args[count-2].equals("--player-control"))) {
            playerControlMode=args[count-2].equals("--player-control");
            advancedControlMode=args[count-2].equals("--advanced-control")||playerControlMode;
            controlMode=args[count-2].equals("--control")||advancedControlMode;
            observerEndpoint = URI.create(args[count - 1]);
            DirectClient.validateEndpoint(observerEndpoint);
            count -= 2;
        }
        if (count < 1 || count > 3
                || !args[0].matches("[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"))
            throw new IllegalArgumentException("arguments");
        String host = count > 1 ? args[1] : "127.0.0.1";
        int port = count > 2 ? Integer.parseInt(args[2]) : 25565;
        if (host.isBlank() || host.startsWith("--") || host.length() > 253 || host.chars().anyMatch(Character::isISOControl)
                || port < 1 || port > 65535) throw new IllegalArgumentException("endpoint");
        return new Arguments(args[0], host, port, observerEndpoint, controlMode, advancedControlMode,playerControlMode);
    }

    static String observerToken(InputStream input) throws IOException {
        StringBuilder token = new StringBuilder();
        for (int i = 0; i <= 257; i++) {
            int next = input.read();
            if (next == '\r') next = input.read() == '\n' ? '\n' : -1;
            if (next == '\n' && token.length() >= 32) return token.toString();
            if (next < 33 || next > 126 || token.length() == 256) throw new IllegalArgumentException("invalid_observer_credential");
            token.append((char)next);
        }
        throw new IllegalArgumentException("invalid_observer_credential");
    }

    public static void main(String[] args) {
        int status = run(args);
        if (status != 0) System.exit(status);
    }

    static void configureCallbackLimits() {
        // JDK reads these at its first HttpServer initialization; this CLI owns a fresh JVM.
        System.setProperty("jdk.httpserver.maxConnections", "16");
        System.setProperty("sun.net.httpserver.maxReqHeaders", "32");
        System.setProperty("sun.net.httpserver.maxReqHeaderSize", "16384");
        System.setProperty("sun.net.httpserver.maxReqTime", "5000");
        System.setProperty("sun.net.httpserver.maxRspTime", "5000");
        System.setProperty("sun.net.httpserver.maxIdleConnections", "0");
    }

    static int run(String[] args) {
        configureCallbackLimits();
        final Arguments options;
        final String observerCredential;
        try { options = arguments(args); }
        catch (IllegalArgumentException invalid) {
            System.err.println("Usage: VanillaBot PUBLIC_CLIENT_ID [HOST] [PORT] [--observe|--control|--advanced-control|--player-control LOOPBACK_WS_URL]");
            return 2;
        }
        try { observerCredential = options.observerEndpoint() == null ? null : observerToken(System.in); }
        catch (IOException | IllegalArgumentException invalid) { System.err.println("INVALID_OBSERVER_CREDENTIAL"); return 2; }
        AtomicReference<VanillaConnection> active = new AtomicReference<>();
        try (MicrosoftAuth auth = new MicrosoftAuth(options.clientId())) {
            Thread shutdown = new Thread(() -> {
                VanillaConnection connection = active.get();
                if (connection != null) connection.close();
                auth.close();
            }, "vanilla-bot-shutdown");
            Runtime.getRuntime().addShutdownHook(shutdown);
            try {
                var session = auth.login(Duration.ofMinutes(5),
                        uri -> System.out.println("AUTH_URL " + uri.toASCIIString()),
                        phase -> System.out.println("AUTH_PHASE " + phase));
                try (VanillaConnection connection = new VanillaConnection(
                        session.profileId(), session.playerName(), session::joinServer)) {
                    active.set(connection);
                    if (options.observerEndpoint() == null) admit(connection, options);
                    else observeConnection(connection, options, observerCredential);
                } finally {
                    active.set(null);
                    System.out.println("SERVER_CONNECTION_CLOSED");
                }
            } finally {
                try { Runtime.getRuntime().removeShutdownHook(shutdown); }
                catch (IllegalStateException shuttingDown) { /* The hook already owns shutdown. */ }
            }
            return 0;
        } catch (MicrosoftAuth.AuthFailure failure) {
            System.err.println("AUTH_FAILED phase=" + failure.phase() + " code=" + failure.code()
                    + " http=" + failure.httpStatus());
        } catch (VanillaConnection.Failure failure) {
            System.err.println("SERVER_FAILED code=" + failure.code());
        } catch (Exception failure) {
            // Library exceptions can contain a request URI or response; never echo their message.
            System.err.println("FAILED INTERNAL_ERROR");
        }
        return 1;
    }

    private static void admit(VanillaConnection connection, Arguments options) throws IOException {
        connection.connect(options.host(), options.port(), Duration.ofSeconds(45));
        System.out.println("SERVER_ADMISSION_CONFIRMED worldReady=false capabilities=[]");
        connection.maintain(Duration.ofSeconds(20));
        System.out.println("SERVER_MAINTAINED_20S");
    }

    private static void observeConnection(VanillaConnection connection, Arguments options, String token) throws Exception {
        VanillaControl control=options.playerControlMode()?connection.enablePlayerControl(BlockCatalog.loadPinned()):options.controlMode()?connection.enableControl(BlockCatalog.loadPinned(),options.advancedControlMode()):null;
        FutureTask<Void> server = new FutureTask<>(() -> { admit(connection, options); return null; });
        Thread worker = Thread.ofVirtual().name("minecraft-observed-connection").unstarted(server);
        try (DirectClient observer = control==null?DirectClient.observer(options.observerEndpoint(), token, connection::close)
                :options.playerControlMode()?DirectClient.playerControl(options.observerEndpoint(),token,control)
                :options.advancedControlMode()?DirectClient.advancedControl(options.observerEndpoint(),token,control)
                :DirectClient.control(options.observerEndpoint(),token,control)) {
            try {
                observer.connect().get(4, TimeUnit.SECONDS);
                long welcomeDeadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(3);
                while (!observer.ready() && System.nanoTime() - welcomeDeadline < 0) {
                    observer.tick(); Thread.sleep(25);
                }
                if (!observer.ready()) throw new VanillaConnection.Failure("local_controller_unavailable");
                worker.start();
                while (!server.isDone()) {
                    if(control==null)observer.observe(connection.observation(), connection.observationOriginNanos());
                    else observer.observeControl(control.observation());
                    observer.tick(); Thread.sleep(25);
                }
                try { server.get(); }
                catch (ExecutionException failure) {
                    if (failure.getCause() instanceof VanillaConnection.Failure transport) throw transport;
                    throw new VanillaConnection.Failure("connection_failed");
                }
            } finally {
                connection.close(); server.cancel(true);
                if (worker.isAlive() && !worker.join(Duration.ofSeconds(2)))
                    throw new VanillaConnection.Failure("connection_shutdown_timeout");
            }
        }
    }
}
