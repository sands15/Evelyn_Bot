package dev.evelyn.minecraft;

import java.net.URI;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Flow;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;
import com.google.gson.JsonParser;

/** Offline authentication contract checks; never connects to an account service. */
public final class MicrosoftAuthTest {
    private static int checks;
    private static final String CLIENT = "00000000-0000-4000-8000-000000000001";
    private static final URI REDIRECT = URI.create("http://localhost:49152/callback");
    private static final Duration LIMIT = Duration.ofSeconds(2);
    private static final String TOKEN_MARKER = "private-token-marker";

    public static void main(String[] args) throws Exception {
        if (args.length == 1 && args[0].equals("callback")) {
            callbackValidation(); callbackResponseBoundary(); denialResponseOrder();
            System.out.println("MICROSOFT_AUTH_CALLBACK_PASS " + checks);
            return;
        }
        equal("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
                MicrosoftAuth.pkceChallenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"), "RFC 7636 vector");
        callbackValidation();
        callbackResponseBoundary();
        denialResponseOrder();
        successfulChainAndJoin();
        optionalMinecraftMetadata();
        failuresStopTheChain();
        timeoutAndClose();
        boundedSubscriber();
        callbackLimitsFailClosed();
        transportConfiguration();
        System.out.println("MICROSOFT_AUTH_PASS " + checks);
    }

    private static void callbackValidation() throws Exception {
        var gate = new MicrosoftAuth.CallbackGate("state-expected", "localhost:49152");
        equal(400, callback(gate, "state=wrong&code=secret-code"), "wrong state rejected");
        check(!gate.code.isDone(), "wrong state must not consume legitimate callback");
        equal(400, callback(gate, "state=state-expected&state=state-expected&code=x"), "duplicate state");
        equal(400, callback(gate, "state=state-expected&code=x&code=y"), "duplicate code");
        equal(400, callback(gate, "state=state-expected&code=x&error=access_denied"), "code and error conflict");
        equal(400, callback(gate, "state=state-expected&code=%zz"), "bad percent escape");
        equal(400, callback(gate, "state=state-expected&code=" + "x".repeat(9000)), "oversized query");
        equal(405, gate.accept("POST", "/callback", List.of("localhost:49152"), "state=state-expected&code=x", 0, false), "method");
        equal(404, gate.accept("GET", "/callback/extra", List.of("localhost:49152"), "state=state-expected&code=x", 0, false), "path");
        equal(400, gate.accept("GET", "/callback", List.of("evil.test"), "state=state-expected&code=x", 0, false), "host");
        equal(400, gate.accept("GET", "/callback", List.of("localhost:49152", "evil.test"), "state=state-expected&code=x", 0, false), "duplicate host");
        equal(400, gate.accept("GET", "/callback", List.of("localhost:49152"), "state=state-expected&code=x", 1, false), "body refused");
        equal(400, gate.accept("GET", "/callback", List.of("localhost:49152"), "state=state-expected&code=x", 0, true), "chunked refused");
        equal(200, callback(gate, "state=state-expected&code=real-code"), "valid callback after attack");
        equal("real-code", gate.code.get(1, TimeUnit.SECONDS), "received code");
        equal(409, callback(gate, "state=state-expected&code=another-code"), "second completion refused");
        var denied = new MicrosoftAuth.CallbackGate("s", "localhost:49152");
        equal(200, callback(denied, "state=s&error=access_denied&error_description=" + TOKEN_MARKER), "denial consumed");
        try { denied.code.join(); throw new AssertionError("denial accepted"); }
        catch (java.util.concurrent.CompletionException expected) {
            var failure = (MicrosoftAuth.AuthFailure) expected.getCause();
            equal("ACCESS_DENIED", failure.code(), "safe denial code");
            check(!failure.toString().contains(TOKEN_MARKER), "callback description private");
        }
    }

    private static int callback(MicrosoftAuth.CallbackGate gate, String query) {
        return gate.accept("GET", "/callback", List.of("localhost:49152"), query, 0, false);
    }

    private static void callbackResponseBoundary() throws Exception {
        var gate = new MicrosoftAuth.CallbackGate("expected", "localhost:49152");
        var incoming = new LocalExchange(URI.create("/callback?state=expected&code=" + TOKEN_MARKER), gate);
        var handler = MicrosoftAuth.class.getDeclaredMethod("handleCallback", MicrosoftAuth.CallbackGate.class,
                com.sun.net.httpserver.HttpExchange.class);
        handler.setAccessible(true);
        handler.invoke(null, gate, incoming);
        equal(200, incoming.status, "handler accepts valid response");
        check(incoming.closed && gate.responseSent.isDone(), "response completed before listener teardown");
        check(!incoming.response.toString(StandardCharsets.UTF_8).contains(TOKEN_MARKER), "callback page does not reflect code");
        equal("no-store", incoming.responseHeaders.getFirst("Cache-Control"), "callback not cached");
        equal("no-referrer", incoming.responseHeaders.getFirst("Referrer-Policy"), "callback no referrer");
        var badGate = new MicrosoftAuth.CallbackGate("expected", "localhost:49152");
        var hugeHeaders = new LocalExchange(URI.create("/callback?state=expected&code=x"), badGate);
        hugeHeaders.requestHeaders.add("X-Large", "x".repeat(8193));
        handler.invoke(null, badGate, hugeHeaders);
        equal(400, hugeHeaders.status, "handler rejects excessive aggregate headers");
        check(!badGate.code.isDone(), "oversized headers cannot consume state");
    }

    private static void denialResponseOrder() throws Exception {
        callbackOutcomeOrder("error=access_denied&error_description=" + TOKEN_MARKER, "ACCESS_DENIED", false);
        callbackOutcomeOrder("code=received-code", "received-code", false);
        callbackOutcomeOrder("error=access_denied", "CLOSED", true);
        var gate = new MicrosoftAuth.CallbackGate("expected", "localhost:49152");
        callback(gate, "state=expected&error=access_denied");
        var deadlineClass = Class.forName("dev.evelyn.minecraft.MicrosoftAuth$Deadline");
        var constructor = deadlineClass.getDeclaredConstructor(Duration.class);
        constructor.setAccessible(true);
        var method = MicrosoftAuth.class.getDeclaredMethod("awaitCallback", MicrosoftAuth.CallbackGate.class, deadlineClass);
        method.setAccessible(true);
        try (var auth = new MicrosoftAuth(CLIENT, (call, remaining) -> { throw new AssertionError("denial exchanged tokens"); })) {
            try { method.invoke(auth, gate, constructor.newInstance(Duration.ofMillis(40))); throw new AssertionError("stalled response escaped deadline"); }
            catch (java.lang.reflect.InvocationTargetException failure) {
                equal("TIMEOUT", ((MicrosoftAuth.AuthFailure) failure.getCause()).code(), "denial uses same remaining deadline");
                check(gate.responseSent.isCancelled(), "stalled denial response wait canceled");
            }
        }
    }

    private static void callbackOutcomeOrder(String outcomeQuery, String expectedOutcome, boolean close) throws Exception {
        var gate = new MicrosoftAuth.CallbackGate("expected", "localhost:49152");
        var deadlineClass = Class.forName("dev.evelyn.minecraft.MicrosoftAuth$Deadline");
        var constructor = deadlineClass.getDeclaredConstructor(Duration.class);
        constructor.setAccessible(true);
        Object deadline = constructor.newInstance(LIMIT);
        var awaitCallback = MicrosoftAuth.class.getDeclaredMethod("awaitCallback", MicrosoftAuth.CallbackGate.class, deadlineClass);
        awaitCallback.setAccessible(true);
        var pendingField = MicrosoftAuth.class.getDeclaredField("pending");
        pendingField.setAccessible(true);
        try (var auth = new MicrosoftAuth(CLIENT, (call, remaining) -> { throw new AssertionError("denial must not exchange tokens"); })) {
            var outcome = CompletableFuture.supplyAsync(() -> {
                try { return (String) awaitCallback.invoke(auth, gate, deadline); }
                catch (java.lang.reflect.InvocationTargetException failure) {
                    return ((MicrosoftAuth.AuthFailure) failure.getCause()).code();
                } catch (ReflectiveOperationException failure) { throw new AssertionError(failure); }
            });
            var pending = (java.util.concurrent.atomic.AtomicReference<?>) pendingField.get(auth);
            long end = System.nanoTime() + TimeUnit.SECONDS.toNanos(1);
            while (pending.get() != gate.code && !outcome.isDone() && end - System.nanoTime() > 0) Thread.onSpinWait();
            check(pending.get() == gate.code, "callback wait started");
            equal(200, callback(gate, "state=expected&" + outcomeQuery), "valid callback outcome accepted");
            end = System.nanoTime() + TimeUnit.SECONDS.toNanos(1);
            while (pending.get() != gate.responseSent && !outcome.isDone() && end - System.nanoTime() > 0) Thread.onSpinWait();
            check(!outcome.isDone() && pending.get() == gate.responseSent,
                    "callback outcome must wait for browser response before reaching caller");
            if (close) auth.close(); else gate.responseSent.complete(null);
            equal(expectedOutcome, outcome.get(1, TimeUnit.SECONDS), "outcome follows response or explicit close");
        }
    }

    private static void successfulChainAndJoin() throws Exception {
        var fixture = new Fixture();
        var progress = new ArrayList<String>();
        try (var auth = new MicrosoftAuth(CLIENT, fixture)) {
            var session = run(auth, LIMIT, progress::add);
            equal("01234567-89ab-4cde-8fab-0123456789ab", session.profileId().toString(), "profile UUID");
            equal("Evelyn_Test", session.playerName(), "profile name");
            check(!session.toString().contains(TOKEN_MARKER), "session redacted");
            equal(6, fixture.calls.size(), "one exchange per phase");
            String oauth = text(fixture.calls.get(0).body());
            check(oauth.contains("grant_type=authorization_code") && oauth.contains("code_verifier="), "PKCE redemption");
            check(!oauth.contains("client_secret") && !oauth.contains("offline_access") && !oauth.contains("User.Read"), "no confidential credentials or unrelated scope");
            var xbox = json(fixture.calls.get(1));
            equal("d=" + TOKEN_MARKER + "-msa", xbox.getAsJsonObject("Properties").get("RpsTicket").getAsString(), "RPS ticket");
            equal("http://auth.xboxlive.com", xbox.get("RelyingParty").getAsString(), "Xbox audience");
            var xsts = json(fixture.calls.get(2));
            equal("rp://api.minecraftservices.com/", xsts.get("RelyingParty").getAsString(), "Minecraft audience");
            equal("RETAIL", xsts.getAsJsonObject("Properties").get("SandboxId").getAsString(), "retail sandbox");
            equal(TOKEN_MARKER + "-xbox", xsts.getAsJsonObject("Properties").getAsJsonArray("UserTokens").get(0).getAsString(), "returned user token unchanged");
            var minecraft = json(fixture.calls.get(4));
            equal(1, minecraft.size(), "no website platform fields in native request");
            equal("XBL3.0 x=123456;" + TOKEN_MARKER + "-xsts", minecraft.get("identityToken").getAsString(), "XSTS identity binding");
            equal("Bearer " + TOKEN_MARKER + "-minecraft", fixture.calls.get(5).headers().get("Authorization"), "profile bearer");
            check(fixture.calls.stream().filter(call -> !call.uri().getPath().endsWith("/profile")).allMatch(call -> !call.headers().containsKey("Authorization")), "no bearer to other endpoints");
            check(fixture.calls.stream().noneMatch(call -> call.toString().contains(TOKEN_MARKER)), "HTTP calls redacted");
            session.joinServer("-123abc");
            var join = json(fixture.calls.get(6));
            equal("0123456789ab4cde8fab0123456789ab", join.get("selectedProfile").getAsString(), "session profile undashed");
            equal("-123abc", join.get("serverId").getAsString(), "signed hash retained");
            equal(TOKEN_MARKER + "-minecraft", join.get("accessToken").getAsString(), "session token");
            expect("SESSION_JOIN", "INVALID_SERVER_HASH", () -> session.joinServer("https://evil.test"));
            equal(7, fixture.calls.size(), "bad hash caused no HTTP");
            check(progress.stream().allMatch(s -> s.matches("[A-Z_]+")), "fixed progress only");
            auth.close();
            expect("SESSION_JOIN", "CLOSED", () -> session.joinServer("abc"));
            equal(7, fixture.calls.size(), "closed session caused no HTTP");
        }
    }

    private static void failuresStopTheChain() throws Exception {
        failAt(0, 302, "{}", "OAUTH", "HTTP_ERROR");
        failAt(0, 400, "{\"error\":\"invalid_grant\",\"error_description\":\"" + TOKEN_MARKER + "\"}", "OAUTH", "INVALID_GRANT");
        failAt(0, 200, "{\"access_token\":\"a\",\"access_token\":\"b\",\"token_type\":\"Bearer\",\"expires_in\":3600}", "OAUTH", "MALFORMED_RESPONSE");
        failAt(0, 200, "{\"access_token\":\"x\",\"token_type\":\"MAC\",\"expires_in\":3600}", "OAUTH", "MALFORMED_RESPONSE");
        failAt(0, 200, "{\"access_token\":\"x\",\"token_type\":\"Bearer\",\"expires_in\":true}", "OAUTH", "MALFORMED_RESPONSE");
        failAt(0, 200, "{\"access_token\":\"x\",\"token_type\":\"Bearer\",\"expires_in\":3600,\"scope\":\"User.Read\"}", "OAUTH", "MALFORMED_RESPONSE");
        failAt(0, 200, "x".repeat(MicrosoftAuth.MAX_BODY + 1), "OAUTH", "RESPONSE_TOO_LARGE");
        failAt(1, 200, "{\"Token\":\"x\",\"NotAfter\":\"2000-01-01T00:00:00Z\"}", "XBOX", "TOKEN_EXPIRED");
        failAt(2, 200, "{\"Token\":\"x\",\"NotAfter\":\"2099-01-01T00:00:00Z\",\"DisplayClaims\":{\"xui\":[]}}", "XSTS", "MALFORMED_RESPONSE");
        failAt(2, 200, "{\"Token\":\"x\",\"NotAfter\":\"2000-01-01T00:00:00Z\",\"DisplayClaims\":{\"xui\":[{\"uhs\":\"123\"}]}}", "XSTS", "TOKEN_EXPIRED");
        failAt(3, 200, discovery().replace("https://api.minecraftservices.com/authentication/login_with_xbox", "https://evil.test/steal"), "DISCOVERY", "UNEXPECTED_ENDPOINT");
        failAt(4, 403, "{\"error\":\"arbitrary-" + TOKEN_MARKER + "\",\"errorMessage\":\"secret\"}", "MINECRAFT", "HTTP_ERROR");
        failAt(4, 200, "{\"access_token\":\"x\",\"token_type\":\"Bearer\",\"expires_in\":-1}", "MINECRAFT", "MALFORMED_RESPONSE");
        failAt(5, 404, "{\"error\":\"NOT_FOUND\"}", "PROFILE", "HTTP_ERROR");
        failAt(5, 200, "{\"id\":\"1-2-3-4-5\",\"name\":\"GoodName\"}", "PROFILE", "MALFORMED_RESPONSE");
        failAt(5, 200, "{\"id\":\"0123456789ab4cde8fab0123456789ab\",\"name\":\"bad\\nname\"}", "PROFILE", "MALFORMED_RESPONSE");
        var fixture = new Fixture();
        fixture.failIndex = 6; fixture.failStatus = 403;
        fixture.failBody = "{\"error\":\"multiplayer.access.banned\",\"errorMessage\":\"" + TOKEN_MARKER + "\"}";
        try (var auth = new MicrosoftAuth(CLIENT, fixture)) {
            var session = run(auth, LIMIT, ignored -> { });
            var refusal = expect("SESSION_JOIN", "MULTIPLAYER_BANNED", () -> session.joinServer("0"));
            equal(403, refusal.httpStatus(), "join refused status");
            equal(7, fixture.calls.size(), "join refusal not retried");
        }
    }

    private static void optionalMinecraftMetadata() throws Exception {
        var fixture = new Fixture();
        fixture.failIndex = 4; fixture.failStatus = 200;
        fixture.failBody = "{\"access_token\":\"" + TOKEN_MARKER + "-minecraft\"}";
        try (var auth = new MicrosoftAuth(CLIENT, fixture)) {
            var session = run(auth, LIMIT, ignored -> { });
            session.joinServer("a");
            equal(7, fixture.calls.size(), "officially observed minimal Minecraft response accepted");
        }
        var completeFixture = new Fixture();
        try (var auth = new MicrosoftAuth(CLIENT, completeFixture)) {
            expect("PROFILE", "CLOSED", () -> run(auth, LIMIT, phase -> {
                if (phase.equals("AUTHENTICATED")) auth.close();
            }));
        }
    }

    private static void failAt(int index, int status, String body, String phase, String code) throws Exception {
        var fixture = new Fixture();
        fixture.failIndex = index; fixture.failStatus = status; fixture.failBody = body;
        try (var auth = new MicrosoftAuth(CLIENT, fixture)) {
            var failure = expect(phase, code, () -> run(auth, LIMIT, ignored -> { }));
            equal(index + 1, fixture.calls.size(), "failure stops later exchanges");
            check(!failure.toString().contains(TOKEN_MARKER) && failure.getCause() == null, "failure does not retain secret cause");
        }
    }

    private static void timeoutAndClose() throws Exception {
        var pending = new CompletableFuture<MicrosoftAuth.Reply>();
        try (var auth = new MicrosoftAuth(CLIENT, (call, remaining) -> pending)) {
            expect("OAUTH", "TIMEOUT", () -> run(auth, Duration.ofMillis(40), ignored -> { }));
            check(pending.isCancelled(), "timeout cancels HTTP future");
        }
        var entered = new CompletableFuture<Void>();
        var stalled = new CompletableFuture<MicrosoftAuth.Reply>();
        var auth = new MicrosoftAuth(CLIENT, (call, remaining) -> { entered.complete(null); return stalled; });
        var outcome = CompletableFuture.supplyAsync(() -> {
            try { run(auth, LIMIT, ignored -> { }); return "unexpected-success"; }
            catch (MicrosoftAuth.AuthFailure failure) { return failure.code(); }
        });
        entered.get(1, TimeUnit.SECONDS);
        expect("OAUTH", "BUSY", () -> run(auth, LIMIT, ignored -> { }));
        auth.close();
        equal("CLOSED", outcome.get(1, TimeUnit.SECONDS), "close cancels blocked authentication");
        check(stalled.isCancelled(), "close cancels in-flight transport");
        try (var failed = new MicrosoftAuth(CLIENT, (call, remaining) -> CompletableFuture.failedFuture(new java.io.IOException(TOKEN_MARKER)))) {
            var failure = expect("OAUTH", "TRANSPORT_ERROR", () -> run(failed, LIMIT, ignored -> { }));
            check(!failure.toString().contains(TOKEN_MARKER) && failure.getCause() == null, "transport cause private");
            expect("CONFIG", "INVALID_TIMEOUT", () -> run(failed, Duration.ofMinutes(11), ignored -> { }));
            expect("CONFIG", "INVALID_TIMEOUT", () -> run(failed, Duration.ZERO, ignored -> { }));
        }
    }

    private static void boundedSubscriber() throws Exception {
        Class<?> bodyClass = Class.forName("dev.evelyn.minecraft.MicrosoftAuth$LimitedBody");
        var constructor = bodyClass.getDeclaredConstructor(int.class);
        constructor.setAccessible(true);
        @SuppressWarnings("unchecked")
        java.net.http.HttpResponse.BodySubscriber<byte[]> subscriber =
                (java.net.http.HttpResponse.BodySubscriber<byte[]>) constructor.newInstance(4);
        var canceled = new AtomicBoolean();
        subscriber.onSubscribe(new Flow.Subscription() {
            public void request(long count) { }
            public void cancel() { canceled.set(true); }
        });
        subscriber.onNext(List.of(ByteBuffer.wrap(new byte[] {1, 2, 3}), ByteBuffer.wrap(new byte[] {4, 5})));
        check(canceled.get(), "oversized body cancels subscription");
        check(subscriber.getBody().toCompletableFuture().isCompletedExceptionally(), "oversized body never succeeds");
        @SuppressWarnings("unchecked")
        java.net.http.HttpResponse.BodySubscriber<byte[]> exact =
                (java.net.http.HttpResponse.BodySubscriber<byte[]>) constructor.newInstance(4);
        exact.onSubscribe(new Flow.Subscription() { public void request(long count) { } public void cancel() { throw new AssertionError("exact body canceled"); } });
        exact.onNext(List.of(ByteBuffer.wrap(new byte[] {1, 2})));
        exact.onNext(List.of(ByteBuffer.wrap(new byte[] {3, 4})));
        exact.onComplete();
        check(java.util.Arrays.equals(new byte[] {1, 2, 3, 4}, exact.getBody().toCompletableFuture().join()), "body preserves chunks at exact cap");
    }

    private static void transportConfiguration() throws Exception {
        try (var auth = new MicrosoftAuth(CLIENT)) {
            var field = MicrosoftAuth.class.getDeclaredField("http");
            field.setAccessible(true);
            var client = (java.net.http.HttpClient) field.get(auth);
            equal(java.net.http.HttpClient.Redirect.NEVER, client.followRedirects(), "credentials cannot follow service redirects");
            check(client.cookieHandler().isEmpty() && client.authenticator().isEmpty(), "no inherited account cookies or authenticator");
        }
    }

    private static void callbackLimitsFailClosed() throws Exception {
        String[] names = {"jdk.httpserver.maxConnections", "sun.net.httpserver.maxReqHeaders",
                "sun.net.httpserver.maxReqHeaderSize", "sun.net.httpserver.maxReqTime",
                "sun.net.httpserver.maxRspTime", "sun.net.httpserver.maxIdleConnections"};
        String[] limits = {"16", "32", "16384", "5000", "5000", "0"};
        String[] original = new String[names.length];
        var validation = MicrosoftAuth.class.getDeclaredMethod("callbackLimits");
        validation.setAccessible(true);
        try {
            for (int i = 0; i < names.length; i++) {
                original[i] = System.getProperty(names[i]); System.setProperty(names[i], limits[i]);
            }
            validation.invoke(null); checks++;
            for (int i = 0; i < names.length; i++) {
                System.clearProperty(names[i]);
                try { validation.invoke(null); throw new AssertionError("missing callback limit accepted"); }
                catch (java.lang.reflect.InvocationTargetException expected) {
                    var failure = (MicrosoftAuth.AuthFailure) expected.getCause();
                    equal("CALLBACK_LIMITS_REQUIRED", failure.code(), "missing callback property refused");
                }
                System.setProperty(names[i], limits[i]);
            }
            System.setProperty(names[0], "-1");
            try { validation.invoke(null); throw new AssertionError("unbounded connections accepted"); }
            catch (java.lang.reflect.InvocationTargetException expected) {
                equal("CALLBACK_LIMITS_REQUIRED", ((MicrosoftAuth.AuthFailure) expected.getCause()).code(), "unbounded callback refused");
            }
        } finally {
            for (int i = 0; i < names.length; i++) {
                if (original[i] == null) System.clearProperty(names[i]); else System.setProperty(names[i], original[i]);
            }
        }
    }

    private static MicrosoftAuth.MinecraftSession run(MicrosoftAuth auth, Duration limit, Consumer<String> progress)
            throws MicrosoftAuth.AuthFailure {
        return auth.exchangeCode("authorization-code", "a".repeat(43), REDIRECT, limit, progress);
    }
    private static com.google.gson.JsonObject json(MicrosoftAuth.Call call) {
        return JsonParser.parseString(text(call.body())).getAsJsonObject();
    }
    private static String text(byte[] bytes) { return new String(bytes, StandardCharsets.UTF_8); }
    private static String discovery() {
        return "{\"environment\":\"prod\",\"product\":\"minecraft\",\"discovery\":{\"authentication\":{\"endpoints\":{\"loginXbox\":{\"uri\":\"https://api.minecraftservices.com/authentication/login_with_xbox\"}}},\"session\":{\"endpoints\":{\"join\":{\"uri\":\"https://sessionserver.mojang.com/session/minecraft/join\"}}}}}";
    }
    private static final class Fixture implements MicrosoftAuth.Exchange {
        final List<MicrosoftAuth.Call> calls = new ArrayList<>();
        int failIndex = -1;
        int failStatus;
        String failBody;
        public CompletableFuture<MicrosoftAuth.Reply> send(MicrosoftAuth.Call call, Duration remaining) {
            check(remaining.compareTo(Duration.ZERO) > 0, "positive remaining HTTP budget");
            int index = calls.size(); calls.add(call);
            String expiry = Instant.now().plusSeconds(3600).toString();
            String body = switch (index) {
                case 0 -> "{\"access_token\":\"" + TOKEN_MARKER + "-msa\",\"token_type\":\"Bearer\",\"expires_in\":3600,\"scope\":\"XboxLive.signin\"}";
                case 1 -> "{\"Token\":\"" + TOKEN_MARKER + "-xbox\",\"NotAfter\":\"" + expiry + "\"}";
                case 2 -> "{\"Token\":\"" + TOKEN_MARKER + "-xsts\",\"NotAfter\":\"" + expiry + "\",\"DisplayClaims\":{\"xui\":[{\"uhs\":\"123456\"}]}}";
                case 3 -> discovery();
                case 4 -> "{\"access_token\":\"" + TOKEN_MARKER + "-minecraft\",\"token_type\":\"Bearer\",\"expires_in\":3600}";
                case 5 -> "{\"id\":\"0123456789ab4cde8fab0123456789ab\",\"name\":\"Evelyn_Test\"}";
                case 6 -> "";
                default -> throw new AssertionError("unexpected HTTP retry");
            };
            String expectedUrl = switch (index) {
                case 0 -> "https://login.microsoftonline.com/consumers/oauth2/v2.0/token";
                case 1 -> "https://user.auth.xboxlive.com/user/authenticate";
                case 2 -> "https://xsts.auth.xboxlive.com/xsts/authorize";
                case 3 -> "https://discovery.minecraftservices.com/minecraft/client";
                case 4 -> "https://api.minecraftservices.com/authentication/login_with_xbox";
                case 5 -> "https://api.minecraftservices.com/minecraft/profile";
                case 6 -> "https://sessionserver.mojang.com/session/minecraft/join";
                default -> throw new AssertionError();
            };
            equal(expectedUrl, call.uri().toString(), "endpoint order");
            equal(index == 3 || index == 5 ? "GET" : "POST", call.method(), "HTTP method");
            return CompletableFuture.completedFuture(new MicrosoftAuth.Reply(index == failIndex ? failStatus : index == 6 ? 204 : 200,
                    (index == failIndex ? failBody : body).getBytes(StandardCharsets.UTF_8)));
        }
    }
    private static final class LocalExchange extends com.sun.net.httpserver.HttpExchange {
        final com.sun.net.httpserver.Headers requestHeaders = new com.sun.net.httpserver.Headers();
        final com.sun.net.httpserver.Headers responseHeaders = new com.sun.net.httpserver.Headers();
        final java.io.ByteArrayOutputStream response = new java.io.ByteArrayOutputStream();
        final URI uri;
        final MicrosoftAuth.CallbackGate gate;
        int status;
        boolean closed;
        LocalExchange(URI uri, MicrosoftAuth.CallbackGate gate) {
            this.uri = uri; this.gate = gate; requestHeaders.add("Host", "localhost:49152");
        }
        public com.sun.net.httpserver.Headers getRequestHeaders() { return requestHeaders; }
        public com.sun.net.httpserver.Headers getResponseHeaders() { return responseHeaders; }
        public URI getRequestURI() { return uri; }
        public String getRequestMethod() { return "GET"; }
        public com.sun.net.httpserver.HttpContext getHttpContext() { return null; }
        public void close() { closed = true; }
        public java.io.InputStream getRequestBody() { return java.io.InputStream.nullInputStream(); }
        public java.io.OutputStream getResponseBody() {
            return new java.io.FilterOutputStream(response) {
                @Override public void write(byte[] value, int start, int length) {
                    check(!gate.responseSent.isDone(), "response completion cannot precede response write");
                    response.write(value, start, length);
                }
            };
        }
        public void sendResponseHeaders(int code, long length) { status = code; }
        public java.net.InetSocketAddress getRemoteAddress() { return new java.net.InetSocketAddress("127.0.0.1", 49153); }
        public int getResponseCode() { return status; }
        public java.net.InetSocketAddress getLocalAddress() { return new java.net.InetSocketAddress("127.0.0.1", 49152); }
        public String getProtocol() { return "HTTP/1.1"; }
        public Object getAttribute(String name) { return null; }
        public void setAttribute(String name, Object value) { }
        public void setStreams(java.io.InputStream input, java.io.OutputStream output) { }
        public com.sun.net.httpserver.HttpPrincipal getPrincipal() { return null; }
    }
    private interface Checked { void run() throws Exception; }
    private static MicrosoftAuth.AuthFailure expect(String phase, String code, Checked run) throws Exception {
        try { run.run(); } catch (MicrosoftAuth.AuthFailure failure) {
            equal(phase, failure.phase(), "failure phase");
            equal(code, failure.code(), "failure code");
            return failure;
        }
        throw new AssertionError("expected " + phase + ":" + code);
    }
    private static void check(boolean condition, String label) {
        checks++; if (!condition) throw new AssertionError(label);
    }
    private static void equal(Object expected, Object actual, String label) {
        check(java.util.Objects.equals(expected, actual), label + " expected=" + expected + " actual=" + actual);
    }
}
