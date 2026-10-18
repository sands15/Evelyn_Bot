package dev.evelyn.minecraft;

import com.google.gson.*;
import com.google.gson.stream.JsonReader;
import com.google.gson.stream.JsonToken;
import java.io.IOException;
import java.io.InputStream;
import java.io.StringReader;
import java.math.BigDecimal;
import java.nio.ByteBuffer;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.*;

/** Pinned official 26.3 facts; legacy flat-walk policy is separate from reviewed geometry. */
public final class BlockCatalog {
    public static final String RESOURCE_SHA256 = "36af92ca085999788941572aa22a9e4b1b7fd0408b22d5f784e216e25efa2a8f";
    public static final String CLIENT_SHA1 = "e877b6a07acd633fb3bb475002175cec036e7b87";
    private static final String LIBRARY_ARCHIVE_SHA1 = "33680f5f2ac32864d6d7cf5e56a705fdb3e05f4c";
    private static final int STATE_COUNT = 35723, ENTITY_COUNT = 161, BLOCK_COUNT = 1286;
    private static final int MAX_BYTES = 8 * 1024 * 1024;
    private static final Set<String> ROOT_KEYS = Set.of("schema", "game_version", "protocol", "client_sha1",
            "library_archive_sha1", "state_count", "global_bits", "block_types", "states", "entity_types", "default_flow_block_ids");
    public static final int DOWN=1, UP=2, NORTH=4, SOUTH=8, WEST=16, EAST=32;

    public enum Kind { AIR, SAFE_SUPPORT, BLOCKED, UNSUPPORTED, UNKNOWN }
    public enum MobCategory { MONSTER, CREATURE, AMBIENT, AXOLOTLS, UNDERGROUND_WATER_CREATURE,
        WATER_CREATURE, WATER_AMBIENT, MISC, UNKNOWN }
    public enum FluidKind { NONE, WATER, UNSUPPORTED }
    public record Box(double minX, double minY, double minZ, double maxX, double maxY, double maxZ) {
        public Box {
            if (!Double.isFinite(minX+minY+minZ+maxX+maxY+maxZ) || minX<0 || minY<0 || minZ<0
                    || maxX>1 || maxY>1 || maxZ>1 || minX>=maxX || minY>=maxY || minZ>=maxZ)
                throw new IllegalArgumentException("catalog_box");
        }
    }
    /** Waterlogged blocks can contain water while their geometry remains unsupported. */
    public record Fluid(FluidKind kind, int amount, boolean falling, float ownHeight, boolean source) {}
    public record State(int id, String name, Map<String, String> properties, Kind kind, float friction,
                        float speedFactor, float jumpFactor, boolean geometrySupported, List<Box> collisionBoxes,
                        int sturdyFaces, boolean blocksFluidFlow, Fluid fluid) {
        public State {
            properties = Map.copyOf(properties); collisionBoxes=List.copyOf(collisionBoxes);
            Objects.requireNonNull(kind); Objects.requireNonNull(fluid);
        }
    }
    /** MONSTER is an official registry category, not a claim about current hostility or AI. */
    public record EntityType(int id, String name, MobCategory category, float width, float height,
                             float eyeHeight, boolean fixedDimensions) {
        public boolean monster() { return category == MobCategory.MONSTER; }
    }
    private record Descriptor(String name, String blockClass, float friction, float speed, float jump) {}
    private static final State UNKNOWN = new State(-1, null, Map.of(), Kind.UNKNOWN, 0,0,0,false,List.of(),0,false,
            new Fluid(FluidKind.UNSUPPORTED,0,false,0,false));
    private static final EntityType UNKNOWN_ENTITY = new EntityType(-1, null, MobCategory.UNKNOWN,0,0,0,false);
    private static BlockCatalog pinned;
    private final State[] states;
    private final EntityType[] entities;
    private final Set<Integer> flowBlockIds;

    private BlockCatalog(State[] states, EntityType[] entities, Set<Integer> flowBlockIds) {
        this.states = states; this.entities = entities; this.flowBlockIds=Set.copyOf(flowBlockIds);
    }

    public static synchronized BlockCatalog loadPinned() throws IOException {
        if (pinned == null) {
            try (InputStream input = BlockCatalog.class.getResourceAsStream("/dev/evelyn/minecraft/block-catalog-26.3.json")) {
                if (input == null) throw failure("catalog_missing");
                byte[] bytes = input.readNBytes(MAX_BYTES + 1);
                pinned = read(bytes, RESOURCE_SHA256);
            }
        }
        return pinned;
    }

    public int stateCount() { return states.length; }
    public int blockCount() { return BLOCK_COUNT; }
    public int globalBits() { return 32 - Integer.numberOfLeadingZeros(states.length - 1); }
    public int entityTypeCount() { return entities.length; }
    public State lookup(int id) { return id < 0 || id >= states.length ? UNKNOWN : states[id]; }
    public EntityType lookupEntityType(int id) { return id < 0 || id >= entities.length ? UNKNOWN_ENTITY : entities[id]; }
    /** Default data-pack tag; callers must compare the server tag before relying on it. IDs are block, not state, IDs. */
    public Set<Integer> defaultFlowBlockIds() { return flowBlockIds; }

    // Separate from classpath loading so offline tests can exercise the structural checks independently of the pin.
    static BlockCatalog read(byte[] bytes, String expectedSha256) throws IOException {
        if (bytes == null || bytes.length > MAX_BYTES || expectedSha256 == null) throw failure("catalog_size");
        try {
            String digest = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
            if (!digest.equals(expectedSha256)) throw failure("catalog_hash");
            String text = StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString();
            JsonObject root;
            try (JsonReader reader = new JsonReader(new StringReader(text))) {
                reader.setStrictness(Strictness.STRICT);
                JsonElement element = readTree(reader, 0, new int[]{2_000_000});
                if (!element.isJsonObject() || reader.peek() != JsonToken.END_DOCUMENT) throw failure("catalog_root");
                root = element.getAsJsonObject();
            }
            if (!root.keySet().equals(ROOT_KEYS)) throw failure("catalog_fields");
            if (integer(root.get("schema")) != 2 || integer(root.get("protocol")) != 777
                    || !string(root.get("game_version")).equals("26.3")
                    || !string(root.get("client_sha1")).equals(CLIENT_SHA1)
                    || !string(root.get("library_archive_sha1")).equals(LIBRARY_ARCHIVE_SHA1)
                    || integer(root.get("state_count")) != STATE_COUNT
                    || integer(root.get("global_bits")) != 16) throw failure("catalog_version");

            JsonArray types = array(root.get("block_types"), BLOCK_COUNT);
            Descriptor[] descriptors = new Descriptor[BLOCK_COUNT];
            Set<String> names = new HashSet<>();
            for (int index = 0; index < descriptors.length; index++) {
                JsonArray row = array(types.get(index), 5);
                String name = identifier(row.get(0));
                String blockClass = string(row.get(1));
                if (!names.add(name) || !blockClass.matches("net\\.minecraft\\.world\\.level\\.block\\.(?:piston\\.)?[A-Za-z0-9_$]{1,100}"))
                    throw failure("catalog_block_type");
                descriptors[index] = new Descriptor(name, blockClass, factor(row.get(2)), factor(row.get(3)), factor(row.get(4)));
            }

            Set<Integer> flowBlockIds=new HashSet<>();
            for (JsonElement entry : array(root.get("default_flow_block_ids"),1026)) {
                int id=integer(entry);
                if(id>=BLOCK_COUNT || !flowBlockIds.add(id)) throw failure("catalog_flow_ids");
            }

            JsonArray entries = array(root.get("states"), STATE_COUNT);
            State[] states = new State[STATE_COUNT];
            boolean[] used = new boolean[BLOCK_COUNT];
            Set<String> uniqueStates = new HashSet<>();
            for (int id = 0; id < states.length; id++) {
                JsonArray row = array(entries.get(id), 11);
                if (integer(row.get(0)) != id) throw failure("catalog_state_id");
                int type = integer(row.get(1));
                if (type < 0 || type >= descriptors.length) throw failure("catalog_state_type");
                Descriptor descriptor = descriptors[type]; used[type] = true;
                Map<String, String> properties = properties(row.get(2));
                if (!uniqueStates.add(descriptor.name + new TreeMap<>(properties))) throw failure("catalog_duplicate_state");
                boolean air = bool(row.get(3)), fluid = bool(row.get(4)), dynamic = bool(row.get(5));
                String collision = string(row.get(6));
                if (!Set.of("empty", "full_cube", "partial", "contextual").contains(collision)) throw failure("catalog_collision");
                Boolean support = row.get(7).isJsonNull() ? null : bool(row.get(7));
                if (dynamic != collision.equals("contextual") || dynamic != (support == null)) throw failure("catalog_context");
                if (air && (dynamic || fluid || !collision.equals("empty"))) throw failure("catalog_air");
                Kind kind = classify(descriptor, air, fluid, dynamic, collision, Boolean.TRUE.equals(support));
                List<Box> boxes=boxes(row.get(8));
                int faces=integer(row.get(9));
                if(faces>63) throw failure("catalog_faces");
                Fluid fluidState=fluid(row.get(10));
                boolean geometry=geometrySupported(descriptor,kind,fluid,dynamic,collision,fluidState);
                if(geometry && kind!=Kind.AIR && fluidState.kind()!=FluidKind.WATER && boxes.isEmpty())
                    throw failure("catalog_missing_geometry");
                states[id] = new State(id, descriptor.name, properties, kind, descriptor.friction,
                        descriptor.speed,descriptor.jump,geometry,geometry?boxes:List.of(),faces,flowBlockIds.contains(type),fluidState);
            }
            for (boolean present : used) if (!present) throw failure("catalog_unused_type");

            JsonArray entityRows = array(root.get("entity_types"), ENTITY_COUNT);
            EntityType[] entities = new EntityType[ENTITY_COUNT]; names.clear();
            for (int id = 0; id < entities.length; id++) {
                JsonArray row = array(entityRows.get(id), 7);
                if (integer(row.get(0)) != id) throw failure("catalog_entity_id");
                String name = identifier(row.get(1));
                MobCategory category = MobCategory.valueOf(string(row.get(2)));
                if (!names.add(name) || category == MobCategory.UNKNOWN) throw failure("catalog_entity_type");
                float width=dimension(row.get(3)),height=dimension(row.get(4)),eye=dimension(row.get(5));
                if(eye>height) throw failure("catalog_eye_height");
                entities[id] = new EntityType(id, name, category,width,height,eye,bool(row.get(6)));
            }
            return new BlockCatalog(states, entities,flowBlockIds);
        } catch (NoSuchAlgorithmException impossible) {
            throw new IOException("catalog_digest_unavailable", impossible);
        } catch (RuntimeException malformed) {
            throw failure("catalog_malformed");
        }
    }

    private static boolean geometrySupported(Descriptor type,Kind kind,boolean fluid,boolean dynamic,String collision,Fluid state) {
        if(dynamic) return false;
        if(kind==Kind.AIR || kind==Kind.SAFE_SUPPORT) return true;
        if(type.name.equals("minecraft:water") && type.blockClass.equals("net.minecraft.world.level.block.LiquidBlock"))
            return fluid && state.kind()==FluidKind.WATER && collision.equals("empty");
        // These classes inherit the empty contact hooks. Copper subclasses only add weathering ticks.
        boolean reviewed=Set.of("net.minecraft.world.level.block.SlabBlock","net.minecraft.world.level.block.StairBlock",
                "net.minecraft.world.level.block.WeatheringCopperSlabBlock","net.minecraft.world.level.block.WeatheringCopperStairBlock").contains(type.blockClass);
        return reviewed && !fluid && state.kind()==FluidKind.NONE && !collision.equals("contextual")
                && type.friction==0.6f && type.speed==1f && type.jump==1f;
    }

    private static List<Box> boxes(JsonElement element) throws IOException {
        if(element==null || !element.isJsonArray() || element.getAsJsonArray().size()>3) throw failure("catalog_boxes");
        List<Box> result=new ArrayList<>();
        for(JsonElement entry:element.getAsJsonArray()) {
            JsonArray a=array(entry,6);
            Box box=new Box(coordinate(a.get(0)),coordinate(a.get(1)),coordinate(a.get(2)),coordinate(a.get(3)),coordinate(a.get(4)),coordinate(a.get(5)));
            if(result.contains(box)) throw failure("catalog_duplicate_box");
            result.add(box);
        }
        return List.copyOf(result);
    }
    private static double coordinate(JsonElement value) throws IOException {
        if(value==null || !value.isJsonPrimitive() || !value.getAsJsonPrimitive().isNumber()) throw failure("catalog_coordinate");
        double number=value.getAsDouble();
        if(!Double.isFinite(number) || number<0 || number>1) throw failure("catalog_coordinate");
        return number;
    }
    private static Fluid fluid(JsonElement element) throws IOException {
        JsonArray a=array(element,5); FluidKind kind=FluidKind.valueOf(string(a.get(0)));
        int amount=integer(a.get(1)); boolean falling=bool(a.get(2)),source=bool(a.get(4)); float height=factor(a.get(3));
        if(kind==FluidKind.NONE) {
            if(amount!=0 || falling || source || height!=0) throw failure("catalog_empty_fluid");
        } else if(amount<1 || amount>8 || height!=amount/9f || (source && amount!=8))
            throw failure("catalog_fluid");
        return new Fluid(kind,amount,falling,height,source);
    }
    private static float dimension(JsonElement element) throws IOException {
        if(element==null || !element.isJsonPrimitive() || !element.getAsJsonPrimitive().isNumber()) throw failure("catalog_dimension");
        double value=element.getAsDouble();
        if(!Double.isFinite(value) || value<0 || value>32) throw failure("catalog_dimension");
        return (float)value;
    }

    private static Kind classify(Descriptor type, boolean air, boolean fluid, boolean dynamic, String collision, boolean support) {
        if (air) return Kind.AIR;
        if (fluid || dynamic) return Kind.UNSUPPORTED;
        // Official MagmaBlock.stepOn damages living entities unless stepping carefully. Initial walking never assumes that mode.
        if (type.name.equals("minecraft:magma_block")) return Kind.BLOCKED;
        // The exact Block contact hooks are empty. GrassBlock -> SpreadingSnowyBlock -> SnowyBlock
        // adds growth/placement behavior, but inherits those empty contact hooks. Other subclasses remain unclassified.
        boolean inspectedContact = type.blockClass.equals("net.minecraft.world.level.block.Block")
                || (type.name.equals("minecraft:grass_block") && type.blockClass.equals("net.minecraft.world.level.block.GrassBlock"));
        if (collision.equals("full_cube") && support && inspectedContact
                && type.friction == 0.6f && type.speed == 1.0f && type.jump == 1.0f) return Kind.SAFE_SUPPORT;
        return Kind.UNSUPPORTED;
    }

    private static JsonElement readTree(JsonReader reader, int depth, int[] budget) throws IOException {
        if (depth > 8 || --budget[0] < 0) throw failure("catalog_json_budget");
        return switch (reader.peek()) {
            case BEGIN_OBJECT -> {
                JsonObject object = new JsonObject(); reader.beginObject();
                while (reader.hasNext()) {
                    String name = reader.nextName();
                    if (name.length() > 128 || object.has(name) || object.size() >= 32) throw failure("catalog_json_field");
                    object.add(name, readTree(reader, depth + 1, budget));
                }
                reader.endObject(); yield object;
            }
            case BEGIN_ARRAY -> {
                JsonArray array = new JsonArray(); reader.beginArray();
                while (reader.hasNext()) {
                    if (array.size() >= STATE_COUNT) throw failure("catalog_json_array");
                    array.add(readTree(reader, depth + 1, budget));
                }
                reader.endArray(); yield array;
            }
            case STRING -> {
                String value = reader.nextString();
                if (value.length() > 256) throw failure("catalog_json_string");
                yield new JsonPrimitive(value);
            }
            case NUMBER -> {
                String value = reader.nextString();
                if (value.length() > 40) throw failure("catalog_json_number");
                yield new JsonPrimitive(new BigDecimal(value));
            }
            case BOOLEAN -> new JsonPrimitive(reader.nextBoolean());
            case NULL -> { reader.nextNull(); yield JsonNull.INSTANCE; }
            default -> throw failure("catalog_json_token");
        };
    }

    private static JsonArray array(JsonElement element, int size) throws IOException {
        if (element == null || !element.isJsonArray() || element.getAsJsonArray().size() != size) throw failure("catalog_array");
        return element.getAsJsonArray();
    }
    private static String string(JsonElement element) throws IOException {
        if (element == null || !element.isJsonPrimitive() || !element.getAsJsonPrimitive().isString()) throw failure("catalog_string");
        return element.getAsString();
    }
    private static String identifier(JsonElement element) throws IOException {
        String name = string(element);
        if (!name.matches("minecraft:[a-z0-9_./-]{1,100}")) throw failure("catalog_identifier");
        return name;
    }
    private static int integer(JsonElement element) throws IOException {
        if (element == null || !element.isJsonPrimitive() || !element.getAsJsonPrimitive().isNumber()
                || !element.getAsString().matches("0|[1-9][0-9]{0,9}")) throw failure("catalog_integer");
        try { return Integer.parseInt(element.getAsString()); }
        catch (NumberFormatException invalid) { throw failure("catalog_integer"); }
    }
    private static boolean bool(JsonElement element) throws IOException {
        if (element == null || !element.isJsonPrimitive() || !element.getAsJsonPrimitive().isBoolean()) throw failure("catalog_boolean");
        return element.getAsBoolean();
    }
    private static float factor(JsonElement element) throws IOException {
        if (element == null || !element.isJsonPrimitive() || !element.getAsJsonPrimitive().isNumber()) throw failure("catalog_factor");
        double value = element.getAsDouble();
        if (!Double.isFinite(value) || value < 0 || value > 4) throw failure("catalog_factor");
        return (float) value;
    }
    private static Map<String, String> properties(JsonElement element) throws IOException {
        if (element == null || !element.isJsonObject() || element.getAsJsonObject().size() > 16) throw failure("catalog_properties");
        Map<String, String> result = new TreeMap<>();
        for (var entry : element.getAsJsonObject().entrySet()) {
            String value = string(entry.getValue());
            if (!entry.getKey().matches("[a-z0-9_]{1,64}") || !value.matches("[-a-z0-9_]{1,64}")) throw failure("catalog_property");
            result.put(entry.getKey(), value);
        }
        return result;
    }
    private static IOException failure(String code) { return new IOException(code); }
}
