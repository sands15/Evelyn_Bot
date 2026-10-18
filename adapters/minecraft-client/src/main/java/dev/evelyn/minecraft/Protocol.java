package dev.evelyn.minecraft;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonObject;
import com.google.gson.JsonNull;
import com.google.gson.JsonPrimitive;
import com.google.gson.Strictness;
import com.google.gson.stream.JsonReader;
import com.google.gson.stream.JsonToken;
import java.io.IOException;
import java.io.StringReader;
import java.nio.charset.StandardCharsets;
import java.util.HashSet;
import java.util.Set;

/** Strict, flat inbound schema; arbitrary payloads never reach the tick executor. */
final class Protocol {
    static final int MAX_BYTES = 32768;
    static final int MAX_SEQUENCE = Integer.MAX_VALUE;
    private static final Gson JSON = new GsonBuilder().serializeNulls().create();
    private Protocol() { }

    static JsonObject decode(String text) {
        return decode(text, 1);
    }

    static JsonObject decode(String text, int version) {
        if (version < 1 || version > 5) throw invalid();
        if (text.length() > MAX_BYTES || text.getBytes(StandardCharsets.UTF_8).length > MAX_BYTES) throw invalid();
        try (JsonReader reader = new JsonReader(new StringReader(text))) {
            reader.setStrictness(Strictness.STRICT);
            if (reader.peek() != JsonToken.BEGIN_OBJECT) throw invalid();
            reader.beginObject();
            JsonObject result = new JsonObject();
            Set<String> keys = new HashSet<>();
            while (reader.hasNext()) {
                String key = reader.nextName();
                if (!keys.add(key) || keys.size() > (version >= 3 ? 15 : 12)) throw invalid();
                switch (reader.peek()) {
                    case STRING -> result.addProperty(key, reader.nextString());
                    case NUMBER -> {
                        String number = reader.nextString();
                        if (version == 5 && Set.of("page_start","inventory_revision","source_slot","destination_slot","amount").contains(key)) {
                            if (!number.matches("0|[1-9][0-9]{0,9}")) throw invalid();
                            long value=Long.parseLong(number);if(value>MAX_SEQUENCE)throw invalid();
                            if(key.equals("page_start")&&(value>4095||value%16!=0))throw invalid();
                            if((key.equals("source_slot")||key.equals("destination_slot"))&&value>4095)throw invalid();
                            if(key.equals("amount")&&(value<1||value>99))throw invalid();
                            result.addProperty(key,value);break;
                        }
                        if (version >= 4 && key.equals("target_id")) {
                            if (!number.matches("0|[1-9][0-9]{0,9}")) throw invalid();
                            long id=Long.parseLong(number);if(id>MAX_SEQUENCE)throw invalid();result.addProperty(key,id);break;
                        }
                        if (version >= 3 && Set.of("target_x", "target_y", "target_z", "yaw", "pitch").contains(key)) {
                            if (key.startsWith("target_")) {
                                if(version>=4&&key.equals("target_y")) {
                                    if(!number.matches("-?(0|[1-9][0-9]{0,3})(\\.[05])?"))throw invalid();
                                    double y=Double.parseDouble(number);if(Math.abs(y)>2048)throw invalid();
                                    result.addProperty(key,y);break;
                                }
                                if (!number.matches("-?(0|[1-9][0-9]{0,7})")) throw invalid();
                                int coordinate = Integer.parseInt(number);
                                if (Math.abs((long)coordinate) > (key.equals("target_y") ? 2048 : 30000000)) throw invalid();
                                result.addProperty(key, coordinate);
                            } else {
                                double angle = Double.parseDouble(number);
                                if (!Double.isFinite(angle) || Math.abs(angle) > (key.equals("pitch") ? 90 : 360)) throw invalid();
                                result.addProperty(key, angle);
                            }
                            break;
                        }
                        if (!number.matches("[1-9][0-9]{0,9}")) throw invalid();
                        long value = Long.parseLong(number);
                        if (value > MAX_SEQUENCE) throw invalid();
                        result.addProperty(key, value);
                    }
                    default -> throw invalid();
                }
            }
            reader.endObject();
            if (reader.peek() != JsonToken.END_DOCUMENT) throw invalid();
            if (integer(result, "v") != version) throw invalid();
            String type = symbol(result, "type");
            if (version == 2 && !type.equals("welcome") && !type.equals("heartbeat")) throw invalid();
            symbol(result, "session");
            symbol(result, "world");
            Set<String> expected = new HashSet<>(Set.of("v", "type", "session", "world"));
            switch (type) {
                case "welcome", "heartbeat" -> { }
                case "off" -> {
                    if (version < 3) throw invalid();
                    expected.add("request_id"); symbol(result, "request_id");
                }
                case "query", "cancel" -> {
                    expected.addAll(Set.of("request_id", "seq"));
                    symbol(result, "request_id");
                    integer(result, "seq");
                }
                case "command" -> {
                    expected.addAll(Set.of("request_id", "seq", "action", "observation_seq", "max_age_ms", "run_ms"));
                    symbol(result, "request_id");
                    symbol(result, "action");
                    integer(result, "seq");
                    integer(result, "observation_seq");
                    integer(result, "max_age_ms");
                    integer(result, "run_ms");
                    if (version >= 3) {
                        String action = symbol(result, "action");
                        Set<String> arguments = switch (action) {
                            case "navigate" -> Set.of("target_x", "target_y", "target_z");
                            case "look" -> Set.of("yaw", "pitch");
                            case "jump" -> {if(version<4)throw invalid();yield Set.of();}
                            case "attack" -> {if(version<4)throw invalid();yield Set.of("target_id","target_epoch");}
                            case "inventory_page" -> {if(version!=5)throw invalid();yield Set.of("page_start","inventory_revision");}
                            case "inventory_transfer" -> {if(version!=5)throw invalid();yield Set.of("source_slot","destination_slot","amount","inventory_revision");}
                            case "swap_hands" -> {if(version!=5)throw invalid();yield Set.of("inventory_revision");}
                            default -> throw invalid();
                        };
                        expected.addAll(arguments);
                        for (String argument : arguments)
                            if (!result.has(argument) || !result.get(argument).isJsonPrimitive()
                                    || !result.getAsJsonPrimitive(argument).isNumber()) throw invalid();
                    }
                }
                default -> throw invalid();
            }
            if (!result.keySet().equals(expected)) throw invalid();
            return result;
        } catch (IOException | IllegalStateException | NumberFormatException failure) {
            throw invalid();
        }
    }

    static String symbol(JsonObject object, String field) {
        if (!object.has(field) || !object.get(field).isJsonPrimitive()) throw invalid();
        JsonPrimitive value = object.getAsJsonPrimitive(field);
        if (!value.isString()) throw invalid();
        String text = value.getAsString();
        checkSymbol(text);
        return text;
    }

    static void checkSymbol(String text) {
        if (text == null || text.isEmpty() || text.length() > 128) throw invalid();
        for (int i = 0; i < text.length(); i++) if (text.charAt(i) < 0x20 || text.charAt(i) > 0x7e) throw invalid();
    }

    static int integer(JsonObject object, String field) {
        if (!object.has(field) || !object.get(field).isJsonPrimitive()) throw invalid();
        JsonPrimitive value = object.getAsJsonPrimitive(field);
        if (!value.isNumber()) throw invalid();
        return value.getAsInt();
    }

    static String encode(JsonObject object) {
        String text = JSON.toJson(object);
        if (text.getBytes(StandardCharsets.UTF_8).length > MAX_BYTES) throw invalid();
        return text;
    }

    static JsonObject observation(PlayerObservation.Snapshot snapshot, long originNanos) {
        if (snapshot == null) throw invalid();
        // nanoTime is a wrapping counter; the bounded connection lifetime is below its half-range.
        long elapsed = snapshot.observedAtNanos() - originNanos;
        if (elapsed < 0 || elapsed / 1_000_000L > 9_007_199_254_740_991L) throw invalid();
        JsonObject result = new JsonObject();
        result.addProperty("seq", snapshot.sequence()); result.addProperty("connection_id", snapshot.connectionId().toString());
        result.addProperty("generation", snapshot.generation()); result.addProperty("event_seq", snapshot.eventSequence());
        result.addProperty("observed_at_ms", elapsed / 1_000_000L); result.addProperty("source", "server_observed");
        result.addProperty("available", snapshot.available()); result.addProperty("dimension", snapshot.dimension());
        JsonObject position = null;
        if (snapshot.position() != null) {
            PlayerObservation.Position value = snapshot.position(); position = new JsonObject();
            position.addProperty("x", value.x()); position.addProperty("y", value.y()); position.addProperty("z", value.z());
            position.addProperty("yaw", value.yaw()); position.addProperty("pitch", value.pitch()); position.addProperty("source", "server_correction");
        }
        result.add("position", position == null ? JsonNull.INSTANCE : position);
        JsonObject health = null;
        if (snapshot.health() != null) {
            PlayerObservation.Health value = snapshot.health(); health = new JsonObject();
            health.addProperty("health", value.health()); health.addProperty("food", value.food()); health.addProperty("saturation", value.saturation());
        }
        result.add("health", health == null ? JsonNull.INSTANCE : health); result.addProperty("world_ready", false);
        JsonObject coverage = new JsonObject();
        for (String field : new String[]{"chunks", "entities", "inventory"}) coverage.addProperty(field, "unknown");
        if (snapshot.terrain() != null) {
            ChunkWorld.Sample sample = snapshot.terrain(); ChunkWorld.Snapshot world = sample.snapshot();
            long worldElapsed = world.observedAtNanos() - originNanos;
            if (worldElapsed < 0 || worldElapsed > elapsed) throw invalid();
            JsonObject terrain = new JsonObject();
            terrain.addProperty("min_y", world.minY()); terrain.addProperty("height", world.height());
            terrain.addProperty("loaded_chunks", world.loadedChunks()); terrain.addProperty("event_seq", world.eventSequence());
            terrain.addProperty("observed_at_ms", worldElapsed / 1_000_000L); terrain.addProperty("encoding", "opaque_state_id");
            com.google.gson.JsonArray blocks = new com.google.gson.JsonArray();
            for (ChunkWorld.Block block : sample.blocks()) {
                JsonObject cell = new JsonObject(); ChunkWorld.BlockPos pos = block.position();
                cell.addProperty("x", pos.x()); cell.addProperty("y", pos.y()); cell.addProperty("z", pos.z());
                cell.addProperty("state_id", block.stateId()); blocks.add(cell);
            }
            terrain.add("blocks", blocks); result.add("terrain", terrain); coverage.addProperty("chunks", "partial");
        }
        result.add("coverage", coverage); return result;
    }

    static IllegalArgumentException invalid() { return new IllegalArgumentException("invalid_message"); }
}
