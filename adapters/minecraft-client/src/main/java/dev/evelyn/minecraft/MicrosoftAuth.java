package dev.evelyn.minecraft;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;
import com.google.gson.Strictness;
import com.google.gson.stream.JsonReader;
import com.google.gson.stream.JsonToken;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.StringReader;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.URLDecoder;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.ByteBuffer;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.time.Duration;
import java.time.Instant;
import java.util.Base64;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.CancellationException;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Flow;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Consumer;

/**
 * One native public-client flow at a time. No token cache, password, or client secret.
 * The callback listener requires a fresh dedicated JVM with bounded JDK HttpServer properties
 * installed before its first initialization; the standalone VanillaBot launcher owns that setup.
 * Reading those properties cannot establish whether some other code initialized the server earlier.
 */
public final class MicrosoftAuth implements AutoCloseable {
    static final int MAX_BODY = 131072;
    private static final URI AUTHORIZE = URI.create("https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize");
    private static final URI OAUTH = URI.create("https://login.microsoftonline.com/consumers/oauth2/v2.0/token");
    private static final URI XBOX = URI.create("https://user.auth.xboxlive.com/user/authenticate");
    private static final URI XSTS = URI.create("https://xsts.auth.xboxlive.com/xsts/authorize");
    private static final URI DISCOVERY = URI.create("https://discovery.minecraftservices.com/minecraft/client");
    private static final URI MINECRAFT = URI.create("https://api.minecraftservices.com/authentication/login_with_xbox");
    private static final URI PROFILE = URI.create("https://api.minecraftservices.com/minecraft/profile");
    private static final URI JOIN = URI.create("https://sessionserver.mojang.com/session/minecraft/join");
    private static final Set<URI> ENDPOINTS = Set.of(OAUTH, XBOX, XSTS, DISCOVERY, MINECRAFT, PROFILE, JOIN);
    private static final SecureRandom RANDOM = new SecureRandom();
    private static final Duration MAX_LOGIN = Duration.ofMinutes(10);
    private final String clientId;
    private final HttpClient http;
    private final Exchange exchange;
    private final AtomicBoolean closed = new AtomicBoolean();
    private final AtomicBoolean busy = new AtomicBoolean();
    private final AtomicReference<CompletableFuture<?>> pending = new AtomicReference<>();
    private final Object lifecycle = new Object();
    private HttpServer callbackServer;
    private ThreadPoolExecutor callbackExecutor;
    private CallbackGate callback;
    private volatile MinecraftSession session;

    interface Exchange { CompletableFuture<Reply> send(Call call, Duration remaining); }
    record Call(URI uri, String method, Map<String, String> headers, byte[] body) {
        @Override public String toString() { return "Call[redacted]"; }
    }
    record Reply(int status, byte[] body) {
        @Override public String toString() { return "Reply[redacted]"; }
    }

    /** Only fixed phase/code and numeric HTTP status leave the authentication boundary. */
    public static final class AuthFailure extends Exception {
        private final String phase;
        private final String code;
        private final int httpStatus;
        private AuthFailure(String phase, String code, int status) {
            super(phase + ":" + code, null, false, false);
            this.phase = phase; this.code = code; this.httpStatus = status;
        }
        public String phase() { return phase; }
        public String code() { return code; }
        public int httpStatus() { return httpStatus; }
    }

    public final class MinecraftSession {
        private final UUID profileId;
        private final String playerName;
        private final long expiresAt;
        private volatile String token;
        private MinecraftSession(UUID profileId, String playerName, String token, long expiresAt) {
            this.profileId = profileId; this.playerName = playerName;
            this.token = token; this.expiresAt = expiresAt;
        }
        public UUID profileId() { return profileId; }
        public String playerName() { return playerName; }
        public void joinServer(String signedServerHash) throws AuthFailure {
            String phase = "SESSION_JOIN";
            if (signedServerHash == null || !signedServerHash.matches("-?[0-9a-f]{1,40}")) {
                throw failure(phase, "INVALID_SERVER_HASH");
            }
            begin(phase);
            try {
                String current = token;
                if (current == null || session != this) throw failure(phase, "CLOSED");
                if (expiresAt - System.nanoTime() <= 0) throw failure(phase, "TOKEN_EXPIRED");
                JsonObject body = object("accessToken", current, "selectedProfile",
                        profileId.toString().replace("-", ""), "serverId", signedServerHash);
                request(phase, new Call(JOIN, "POST", jsonHeaders(), utf8(body.toString())),
                        new Deadline(Duration.ofSeconds(30)), false);
            } finally { busy.set(false); }
        }
        @Override public String toString() { return "MinecraftSession[redacted]"; }
    }

    public MicrosoftAuth(String publicClientId) {
        this.clientId = validClient(publicClientId);
        this.http = HttpClient.newBuilder().followRedirects(HttpClient.Redirect.NEVER)
                .connectTimeout(Duration.ofSeconds(10)).build();
        this.exchange = this::send;
    }
    MicrosoftAuth(String publicClientId, Exchange exchange) {
        this.clientId = validClient(publicClientId);
        this.http = null;
        this.exchange = Objects.requireNonNull(exchange);
    }

    /** Callbacks must return promptly; the application owns browser display and user interaction. */
    public MinecraftSession login(Duration timeout, Consumer<URI> showAuthorizationUrl,
                                  Consumer<String> progress) throws AuthFailure {
        Deadline deadline = new Deadline(timeout);
        callbackLimits();
        begin("CALLBACK");
        try {
            Objects.requireNonNull(showAuthorizationUrl);
            String verifier = randomValue();
            String state = randomValue();
            CallbackGate gate;
            URI redirect;
            synchronized (lifecycle) {
                ensureOpen("CALLBACK");
                callbackServer = HttpServer.create(new InetSocketAddress(
                        InetAddress.getByAddress(new byte[] {127, 0, 0, 1}), 0), 8);
                int port = callbackServer.getAddress().getPort();
                redirect = URI.create("http://localhost:" + port + "/callback");
                gate = new CallbackGate(state, "localhost:" + port);
                callback = gate;
                callbackExecutor = new ThreadPoolExecutor(1, 1, 0, TimeUnit.SECONDS,
                        new ArrayBlockingQueue<>(8), runnable -> {
                            Thread thread = new Thread(runnable, "minecraft-auth-callback");
                            thread.setDaemon(true); return thread;
                        }, new ThreadPoolExecutor.AbortPolicy());
                callbackServer.setExecutor(callbackExecutor);
                callbackServer.createContext("/callback", incoming -> handleCallback(gate, incoming));
                callbackServer.start();
            }
            progress(progress, "CALLBACK");
            String query = form(Map.of("client_id", clientId, "response_type", "code",
                    "redirect_uri", redirect.toString(), "response_mode", "query", "scope", "XboxLive.signin",
                    "state", state, "code_challenge", pkceChallenge(verifier), "code_challenge_method", "S256"));
            try { showAuthorizationUrl.accept(URI.create(AUTHORIZE + "?" + query)); }
            catch (RuntimeException exception) { throw failure("CALLBACK", "DISPLAY_FAILED"); }
            String code = awaitCallback(gate, deadline);
            stopCallback();
            return authenticateCode(code, verifier, redirect, deadline, progress);
        } catch (AuthFailure failure) { throw failure; }
        catch (IOException | RuntimeException exception) { throw failure("CALLBACK", "LOCAL_CALLBACK_FAILED"); }
        finally { stopCallback(); busy.set(false); }
    }

    MinecraftSession exchangeCode(String code, String verifier, URI redirect, Duration timeout,
                                  Consumer<String> progress) throws AuthFailure {
        Deadline deadline = new Deadline(timeout);
        begin("OAUTH");
        try { return authenticateCode(code, verifier, redirect, deadline, progress); }
        finally { busy.set(false); }
    }

    private MinecraftSession authenticateCode(String code, String verifier, URI redirect, Deadline deadline,
                                             Consumer<String> progress) throws AuthFailure {
        if (!printable(code, 8192) || verifier == null || !verifier.matches("[A-Za-z0-9._~-]{43,128}")
                || redirect == null || !"http".equals(redirect.getScheme()) || !"localhost".equals(redirect.getHost())
                || !"/callback".equals(redirect.getRawPath()) || redirect.getRawQuery() != null
                || redirect.getRawFragment() != null || redirect.getRawUserInfo() != null || redirect.getPort() < 1) {
            throw failure("OAUTH", "INVALID_REQUEST");
        }
        progress(progress, "OAUTH");
        String form = form(Map.of("client_id", clientId, "code", code, "redirect_uri", redirect.toString(),
                "grant_type", "authorization_code", "code_verifier", verifier));
        JsonObject msa = request("OAUTH", new Call(OAUTH, "POST",
                Map.of("Content-Type", "application/x-www-form-urlencoded", "Accept", "application/json"), utf8(form)), deadline, true);
        String msaToken = token(msa, "access_token", "OAUTH");
        expiresIn(msa, "OAUTH");
        if (!"Bearer".equalsIgnoreCase(string(msa, "token_type", "OAUTH"))) throw failure("OAUTH", "MALFORMED_RESPONSE");
        if (msa.has("scope") && !List.of(string(msa, "scope", "OAUTH").split(" "))
                .stream().anyMatch("XboxLive.signin"::equalsIgnoreCase)) throw failure("OAUTH", "MALFORMED_RESPONSE");

        progress(progress, "XBOX");
        JsonObject xboxRequest = object("RelyingParty", "http://auth.xboxlive.com", "TokenType", "JWT");
        xboxRequest.add("Properties", object("AuthMethod", "RPS", "SiteName", "user.auth.xboxlive.com", "RpsTicket", "d=" + msaToken));
        JsonObject xbox = post("XBOX", XBOX, xboxRequest, deadline, true);
        String xboxToken = token(xbox, "Token", "XBOX");
        checkNotAfter(xbox, "XBOX");

        progress(progress, "XSTS");
        JsonObject xstsRequest = object("RelyingParty", "rp://api.minecraftservices.com/", "TokenType", "JWT");
        JsonObject properties = object("SandboxId", "RETAIL");
        JsonArray userTokens = new JsonArray(); userTokens.add(xboxToken); properties.add("UserTokens", userTokens);
        xstsRequest.add("Properties", properties);
        JsonObject xsts = post("XSTS", XSTS, xstsRequest, deadline, true);
        String xstsToken = token(xsts, "Token", "XSTS");
        checkNotAfter(xsts, "XSTS");
        JsonObject claims = child(xsts, "DisplayClaims", "XSTS");
        JsonElement xui = claims.get("xui");
        if (xui == null || !xui.isJsonArray() || xui.getAsJsonArray().size() != 1
                || !xui.getAsJsonArray().get(0).isJsonObject()) throw failure("XSTS", "MALFORMED_RESPONSE");
        String uhs = string(xui.getAsJsonArray().get(0).getAsJsonObject(), "uhs", "XSTS");
        if (!uhs.matches("[0-9]{1,32}")) throw failure("XSTS", "MALFORMED_RESPONSE");

        progress(progress, "DISCOVERY");
        JsonObject discovery = request("DISCOVERY", new Call(DISCOVERY, "GET", Map.of("Accept", "application/json"), new byte[0]), deadline, true);
        if (!"prod".equals(string(discovery, "environment", "DISCOVERY"))
                || !"minecraft".equals(string(discovery, "product", "DISCOVERY"))) throw failure("DISCOVERY", "UNEXPECTED_ENDPOINT");
        JsonObject services = child(discovery, "discovery", "DISCOVERY");
        verifyEndpoint(services, "authentication", "loginXbox", MINECRAFT);
        verifyEndpoint(services, "session", "join", JOIN);

        progress(progress, "MINECRAFT");
        // Minimal native body is an explicit integration assumption; website-only flags are excluded.
        JsonObject minecraft = post("MINECRAFT", MINECRAFT,
                object("identityToken", "XBL3.0 x=" + uhs + ";" + xstsToken), deadline, false);
        String minecraftToken = token(minecraft, "access_token", "MINECRAFT");
        // Missing service expiry gets a local ten-minute cap, not an assertion about server validity.
        int lifetime = minecraft.has("expires_in") ? expiresIn(minecraft, "MINECRAFT") : 600;
        if (minecraft.has("token_type") && !"Bearer".equalsIgnoreCase(string(minecraft, "token_type", "MINECRAFT"))) {
            throw failure("MINECRAFT", "MALFORMED_RESPONSE");
        }
        long expiresAt = System.nanoTime() + TimeUnit.SECONDS.toNanos(lifetime - 5L);

        progress(progress, "PROFILE");
        JsonObject profile = request("PROFILE", new Call(PROFILE, "GET",
                Map.of("Authorization", "Bearer " + minecraftToken, "Accept", "application/json"), new byte[0]), deadline, true);
        String id = string(profile, "id", "PROFILE");
        if (!id.matches("[0-9a-fA-F]{32}")) throw failure("PROFILE", "MALFORMED_RESPONSE");
        UUID profileId = UUID.fromString(id.substring(0, 8) + "-" + id.substring(8, 12) + "-"
                + id.substring(12, 16) + "-" + id.substring(16, 20) + "-" + id.substring(20));
        String playerName = string(profile, "name", "PROFILE");
        if (!playerName.matches("[A-Za-z0-9_]{1,16}")) throw failure("PROFILE", "MALFORMED_RESPONSE");
        deadline.remaining("PROFILE");
        MinecraftSession result = new MinecraftSession(profileId, playerName, minecraftToken, expiresAt);
        progress(progress, "AUTHENTICATED");
        synchronized (lifecycle) {
            ensureOpen("PROFILE");
            if (session != null) session.token = null;
            session = result;
        }
        return result;
    }

    private JsonObject post(String phase, URI uri, JsonObject body, Deadline deadline, boolean xbox) throws AuthFailure {
        Map<String, String> headers = xbox
                ? Map.of("Content-Type", "application/json", "Accept", "application/json", "x-xbl-contract-version", "1")
                : jsonHeaders();
        return request(phase, new Call(uri, "POST", headers, utf8(body.toString())), deadline, true);
    }

    private JsonObject request(String phase, Call call, Deadline deadline, boolean expectJson) throws AuthFailure {
        ensureOpen(phase);
        if (!ENDPOINTS.contains(call.uri())) throw failure(phase, "UNEXPECTED_ENDPOINT");
        Reply reply;
        try { reply = await(exchange.send(call, deadline.remaining(phase)), deadline, phase); }
        catch (AuthFailure failure) { throw failure; }
        catch (RuntimeException exception) { throw failure(phase, "TRANSPORT_ERROR"); }
        if (reply == null || reply.body() == null) throw failure(phase, "MALFORMED_RESPONSE");
        if (reply.body().length > MAX_BODY) throw failure(phase, "RESPONSE_TOO_LARGE");
        if (reply.status() < 200 || reply.status() >= 300) {
            String safeCode = "HTTP_ERROR";
            try {
                JsonObject error = parse(reply.body(), phase);
                JsonElement value = error.get("error");
                if (value != null && value.isJsonPrimitive() && value.getAsJsonPrimitive().isString()) {
                    safeCode = safeError(value.getAsString());
                }
            } catch (AuthFailure ignored) { }
            throw new AuthFailure(phase, safeCode, reply.status());
        }
        return expectJson ? parse(reply.body(), phase) : new JsonObject();
    }

    private CompletableFuture<Reply> send(Call call, Duration remaining) {
        HttpRequest.Builder builder = HttpRequest.newBuilder(call.uri()).timeout(remaining);
        call.headers().forEach(builder::header);
        builder.method(call.method(), call.body().length == 0 ? HttpRequest.BodyPublishers.noBody()
                : HttpRequest.BodyPublishers.ofByteArray(call.body()));
        CompletableFuture<HttpResponse<byte[]>> original = http.sendAsync(builder.build(), info -> new LimitedBody(MAX_BODY));
        CompletableFuture<Reply> result = new CompletableFuture<>();
        original.whenComplete((response, error) -> {
            if (error != null) result.completeExceptionally(error);
            else result.complete(new Reply(response.statusCode(), response.body()));
        });
        result.whenComplete((value, error) -> { if (result.isCancelled()) original.cancel(true); });
        return result;
    }

    private <T> T await(CompletableFuture<T> future, Deadline deadline, String phase) throws AuthFailure {
        if (future == null) throw failure(phase, "TRANSPORT_ERROR");
        if (!pending.compareAndSet(null, future)) { future.cancel(true); throw failure(phase, "BUSY"); }
        try {
            ensureOpen(phase);
            T result = future.get(deadline.remaining(phase).toNanos(), TimeUnit.NANOSECONDS);
            ensureOpen(phase);
            deadline.remaining(phase);
            return result;
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt(); throw failure(phase, "CANCELLED");
        } catch (TimeoutException exception) { throw failure(phase, "TIMEOUT"); }
        catch (CancellationException exception) { throw failure(phase, closed.get() ? "CLOSED" : "CANCELLED"); }
        catch (ExecutionException exception) {
            Throwable cause = exception.getCause();
            for (int i = 0; cause != null && i < 8; i++, cause = cause.getCause()) {
                if (cause instanceof AuthFailure authFailure) throw authFailure;
                if (cause instanceof BodyLimitException) throw failure(phase, "RESPONSE_TOO_LARGE");
                if (cause instanceof java.net.http.HttpTimeoutException) throw failure(phase, "TIMEOUT");
            }
            throw failure(phase, "TRANSPORT_ERROR");
        } finally {
            pending.compareAndSet(future, null);
            if (!future.isDone()) future.cancel(true);
        }
    }

    private String awaitCallback(CallbackGate gate, Deadline deadline) throws AuthFailure {
        String code = null;
        AuthFailure callbackFailure = null;
        try { code = await(gate.code, deadline, "CALLBACK"); }
        catch (AuthFailure failure) {
            if (gate.code.isCancelled() || !gate.code.isCompletedExceptionally()) throw failure;
            callbackFailure = failure;
        }
        // Both valid outcomes wait for the fixed browser response before listener teardown.
        await(gate.responseSent, deadline, "CALLBACK");
        if (callbackFailure != null) throw callbackFailure;
        return code;
    }

    static final class CallbackGate {
        final CompletableFuture<String> code = new CompletableFuture<>();
        final CompletableFuture<Void> responseSent = new CompletableFuture<>();
        private final String state;
        private final String host;
        CallbackGate(String state, String host) { this.state = state; this.host = host; }
        int accept(String method, String path, List<String> hosts, String query,
                   long contentLength, boolean transferEncoding) {
            if (!"GET".equals(method)) return 405;
            if (!"/callback".equals(path)) return 404;
            if (hosts == null || hosts.size() != 1 || !host.equalsIgnoreCase(hosts.get(0))
                    || contentLength > 0 || contentLength < -1 || transferEncoding || query == null || query.length() > 8192) return 400;
            Map<String, String> fields = new HashMap<>();
            try {
                String[] parts = query.split("&", -1);
                if (parts.length > 12) return 400;
                for (String part : parts) {
                    int equals = part.indexOf('=');
                    if (equals < 1) return 400;
                    String key = URLDecoder.decode(part.substring(0, equals), StandardCharsets.UTF_8);
                    String value = URLDecoder.decode(part.substring(equals + 1), StandardCharsets.UTF_8);
                    if (fields.putIfAbsent(key, value) != null) return 400;
                }
            } catch (IllegalArgumentException exception) { return 400; }
            String received = fields.get("state");
            if (received == null || !MessageDigest.isEqual(utf8(state), utf8(received))) return 400;
            if (code.isDone()) return 409;
            String value = fields.get("code");
            String error = fields.get("error");
            if ((value == null) == (error == null)) return 400;
            if (error != null) return code.completeExceptionally(new AuthFailure("CALLBACK", safeError(error), 0)) ? 200 : 409;
            if (!printable(value, 8192)) return 400;
            return code.complete(value) ? 200 : 409;
        }
    }

    private static void handleCallback(CallbackGate gate, HttpExchange incoming) throws IOException {
        boolean accepted = false;
        try (incoming) {
            int size = 0;
            for (var entry : incoming.getRequestHeaders().entrySet()) {
                size += entry.getKey().length();
                for (String value : entry.getValue()) size += value.length();
            }
            int status = 400;
            if (size <= 8192 && incoming.getRequestHeaders().size() <= 32 && incoming.getRequestURI().getScheme() == null) {
                List<String> lengths = incoming.getRequestHeaders().get("Content-Length");
                long length = -1;
                try {
                    if (lengths != null) length = lengths.size() == 1 ? Long.parseLong(lengths.get(0)) : -2;
                    status = gate.accept(incoming.getRequestMethod(), incoming.getRequestURI().getRawPath(),
                            incoming.getRequestHeaders().get("Host"), incoming.getRequestURI().getRawQuery(), length,
                            incoming.getRequestHeaders().containsKey("Transfer-Encoding"));
                } catch (NumberFormatException ignored) { }
            }
            byte[] response = utf8(status == 200 ? "Authentication response received. You may close this tab." : "Authentication response rejected.");
            accepted = status == 200;
            incoming.getResponseHeaders().set("Content-Type", "text/plain; charset=utf-8");
            incoming.getResponseHeaders().set("Cache-Control", "no-store");
            incoming.getResponseHeaders().set("Referrer-Policy", "no-referrer");
            incoming.sendResponseHeaders(status, response.length);
            incoming.getResponseBody().write(response);
        } finally {
            if (accepted) gate.responseSent.complete(null);
        }
    }

    private void begin(String phase) throws AuthFailure {
        ensureOpen(phase);
        if (!busy.compareAndSet(false, true)) throw failure(phase, "BUSY");
        if (closed.get()) { busy.set(false); throw failure(phase, "CLOSED"); }
    }
    private void ensureOpen(String phase) throws AuthFailure {
        if (closed.get()) throw failure(phase, "CLOSED");
    }
    private void stopCallback() {
        synchronized (lifecycle) {
            if (callback != null) { callback.code.cancel(true); callback.responseSent.cancel(true); callback = null; }
            if (callbackServer != null) { callbackServer.stop(0); callbackServer = null; }
            if (callbackExecutor != null) { callbackExecutor.shutdownNow(); callbackExecutor = null; }
        }
    }
    @Override public void close() {
        if (!closed.compareAndSet(false, true)) return;
        CompletableFuture<?> active = pending.get();
        if (active != null) active.cancel(true);
        stopCallback();
        synchronized (lifecycle) { if (session != null) { session.token = null; session = null; } }
        if (http != null) http.shutdownNow();
    }

    private static final class Deadline {
        private final long end;
        Deadline(Duration timeout) throws AuthFailure {
            if (timeout == null || timeout.isNegative() || timeout.isZero() || timeout.compareTo(MAX_LOGIN) > 0) {
                throw failure("CONFIG", "INVALID_TIMEOUT");
            }
            end = System.nanoTime() + timeout.toNanos();
        }
        Duration remaining(String phase) throws AuthFailure {
            long left = end - System.nanoTime();
            if (left <= 0) throw failure(phase, "TIMEOUT");
            return Duration.ofNanos(left);
        }
    }

    static final class LimitedBody implements HttpResponse.BodySubscriber<byte[]> {
        private final int limit;
        private final ByteArrayOutputStream bytes;
        private final CompletableFuture<byte[]> result = new CompletableFuture<>();
        private Flow.Subscription subscription;
        LimitedBody(int limit) { this.limit = limit; bytes = new ByteArrayOutputStream(Math.min(limit, 8192)); }
        public CompletionStage<byte[]> getBody() { return result; }
        public void onSubscribe(Flow.Subscription subscription) {
            if (this.subscription != null || result.isDone()) { subscription.cancel(); return; }
            this.subscription = subscription; subscription.request(1);
        }
        public void onNext(List<ByteBuffer> chunks) {
            if (result.isDone()) return;
            long count = bytes.size();
            for (ByteBuffer chunk : chunks) count += chunk.remaining();
            if (count > limit) {
                subscription.cancel(); result.completeExceptionally(new BodyLimitException()); return;
            }
            for (ByteBuffer chunk : chunks) { byte[] part = new byte[chunk.remaining()]; chunk.get(part); bytes.writeBytes(part); }
            subscription.request(1);
        }
        public void onError(Throwable error) { result.completeExceptionally(error); }
        public void onComplete() { result.complete(bytes.toByteArray()); }
    }
    private static final class BodyLimitException extends IOException { }

    private static JsonObject parse(byte[] body, String phase) throws AuthFailure {
        try {
            String text = StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(body)).toString();
            try (JsonReader reader = new JsonReader(new StringReader(text))) {
                reader.setStrictness(Strictness.STRICT);
                JsonElement value = readValue(reader, 0);
                if (!value.isJsonObject() || reader.peek() != JsonToken.END_DOCUMENT) throw new IOException();
                return value.getAsJsonObject();
            }
        } catch (IOException | RuntimeException exception) { throw failure(phase, "MALFORMED_RESPONSE"); }
    }
    private static JsonElement readValue(JsonReader reader, int depth) throws IOException {
        if (depth > 12) throw new IOException();
        switch (reader.peek()) {
            case BEGIN_OBJECT: {
                JsonObject object = new JsonObject(); Set<String> keys = new HashSet<>(); reader.beginObject();
                while (reader.hasNext()) {
                    String key = reader.nextName();
                    if (!keys.add(key) || keys.size() > 256) throw new IOException();
                    object.add(key, readValue(reader, depth + 1));
                }
                reader.endObject(); return object;
            }
            case BEGIN_ARRAY: {
                JsonArray array = new JsonArray(); reader.beginArray();
                while (reader.hasNext()) {
                    if (array.size() >= 256) throw new IOException();
                    array.add(readValue(reader, depth + 1));
                }
                reader.endArray(); return array;
            }
            case STRING: return new JsonPrimitive(reader.nextString());
            case NUMBER: return new JsonPrimitive(new java.math.BigDecimal(reader.nextString()));
            case BOOLEAN: return new JsonPrimitive(reader.nextBoolean());
            case NULL: reader.nextNull(); return JsonNull.INSTANCE;
            default: throw new IOException();
        }
    }
    private static JsonObject child(JsonObject object, String key, String phase) throws AuthFailure {
        JsonElement element = object.get(key);
        if (element == null || !element.isJsonObject()) throw failure(phase, "MALFORMED_RESPONSE");
        return element.getAsJsonObject();
    }
    private static String string(JsonObject object, String key, String phase) throws AuthFailure {
        JsonElement element = object.get(key);
        if (element == null || !element.isJsonPrimitive() || !element.getAsJsonPrimitive().isString()) {
            throw failure(phase, "MALFORMED_RESPONSE");
        }
        return element.getAsString();
    }
    private static String token(JsonObject object, String key, String phase) throws AuthFailure {
        String value = string(object, key, phase);
        if (!printable(value, 16384)) throw failure(phase, "MALFORMED_RESPONSE");
        return value;
    }
    private static int expiresIn(JsonObject object, String phase) throws AuthFailure {
        JsonElement element = object.get("expires_in");
        if (element == null || !element.isJsonPrimitive() || !element.getAsJsonPrimitive().isNumber()
                || !element.getAsString().matches("[1-9][0-9]{0,5}")) throw failure(phase, "MALFORMED_RESPONSE");
        int seconds = element.getAsInt();
        if (seconds > 604800) throw failure(phase, "MALFORMED_RESPONSE");
        if (seconds <= 5) throw failure(phase, "TOKEN_EXPIRED");
        return seconds;
    }
    private static void checkNotAfter(JsonObject object, String phase) throws AuthFailure {
        try {
            if (!Instant.parse(string(object, "NotAfter", phase)).isAfter(Instant.now().plusSeconds(5))) {
                throw failure(phase, "TOKEN_EXPIRED");
            }
        } catch (java.time.DateTimeException exception) { throw failure(phase, "MALFORMED_RESPONSE"); }
    }
    private static void verifyEndpoint(JsonObject services, String service, String name, URI expected) throws AuthFailure {
        JsonObject endpoint = child(child(child(services, service, "DISCOVERY"), "endpoints", "DISCOVERY"), name, "DISCOVERY");
        if (!expected.toString().equals(string(endpoint, "uri", "DISCOVERY"))) throw failure("DISCOVERY", "UNEXPECTED_ENDPOINT");
    }
    private static void progress(Consumer<String> progress, String phase) throws AuthFailure {
        try { if (progress != null) progress.accept(phase); }
        catch (RuntimeException exception) { throw failure(phase, "PROGRESS_FAILED"); }
    }
    private static String validClient(String value) {
        if (value == null || !value.matches("[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")) {
            throw new IllegalArgumentException("invalid public client ID");
        }
        return value;
    }
    private static void callbackLimits() throws AuthFailure {
        String[] names = {"jdk.httpserver.maxConnections", "sun.net.httpserver.maxReqHeaders",
                "sun.net.httpserver.maxReqHeaderSize", "sun.net.httpserver.maxReqTime",
                "sun.net.httpserver.maxRspTime", "sun.net.httpserver.maxIdleConnections"};
        int[] maximum = {16, 32, 16384, 5000, 5000, 0};
        try {
            for (int i = 0; i < names.length; i++) {
                int value = Integer.parseInt(System.getProperty(names[i], ""));
                if (value > maximum[i] || value < (maximum[i] == 0 ? 0 : 1)) {
                    throw failure("CONFIG", "CALLBACK_LIMITS_REQUIRED");
                }
            }
        } catch (NumberFormatException exception) { throw failure("CONFIG", "CALLBACK_LIMITS_REQUIRED"); }
    }
    private static boolean printable(String value, int limit) {
        return value != null && !value.isEmpty() && value.length() <= limit
                && value.chars().allMatch(ch -> ch >= 33 && ch <= 126);
    }
    private static JsonObject object(String... pairs) {
        JsonObject result = new JsonObject();
        for (int i = 0; i < pairs.length; i += 2) result.addProperty(pairs[i], pairs[i + 1]);
        return result;
    }
    private static Map<String, String> jsonHeaders() { return Map.of("Content-Type", "application/json", "Accept", "application/json"); }
    private static byte[] utf8(String value) { return value.getBytes(StandardCharsets.UTF_8); }
    private static String form(Map<String, String> fields) {
        return fields.entrySet().stream().map(entry -> URLEncoder.encode(entry.getKey(), StandardCharsets.UTF_8)
                + "=" + URLEncoder.encode(entry.getValue(), StandardCharsets.UTF_8)).collect(java.util.stream.Collectors.joining("&"));
    }
    private static String randomValue() {
        byte[] bytes = new byte[32]; RANDOM.nextBytes(bytes); return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }
    static String pkceChallenge(String verifier) {
        try { return Base64.getUrlEncoder().withoutPadding().encodeToString(MessageDigest.getInstance("SHA-256").digest(utf8(verifier))); }
        catch (NoSuchAlgorithmException impossible) { throw new IllegalStateException("SHA-256 unavailable"); }
    }
    private static String safeError(String error) {
        return switch (error) {
            case "access_denied" -> "ACCESS_DENIED";
            case "invalid_grant" -> "INVALID_GRANT";
            case "invalid_scope" -> "INVALID_SCOPE";
            case "invalid_client" -> "INVALID_CLIENT";
            case "unauthorized_client" -> "UNAUTHORIZED_CLIENT";
            case "interaction_required" -> "INTERACTION_REQUIRED";
            case "consent_required" -> "CONSENT_REQUIRED";
            case "ForbiddenOperationException" -> "INVALID_CREDENTIALS";
            case "multiplayer.access.banned" -> "MULTIPLAYER_BANNED";
            case "FORCED_USERNAME_CHANGE" -> "FORCED_USERNAME_CHANGE";
            case "InsufficientPrivilegesException" -> "INSUFFICIENT_PRIVILEGES";
            default -> "HTTP_ERROR";
        };
    }
    private static AuthFailure failure(String phase, String code) { return new AuthFailure(phase, code, 0); }
}
