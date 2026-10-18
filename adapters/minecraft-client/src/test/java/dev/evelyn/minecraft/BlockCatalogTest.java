package dev.evelyn.minecraft;

import com.google.gson.*;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.Map;
import java.util.List;

/** Offline fixed official-registry oracles; never starts Minecraft or a connection. */
public final class BlockCatalogTest {
    private static int checks;

    public static void main(String[] args) throws Exception {
        BlockCatalog catalog = BlockCatalog.loadPinned();
        geometryOracles(catalog);
        check(catalog.stateCount() == 35723 && catalog.globalBits() == 16, "official state extent");
        check(catalog.entityTypeCount() == 161, "official entity extent");
        state(catalog, 0, "minecraft:air", BlockCatalog.Kind.AIR);
        state(catalog, 18649, "minecraft:void_air", BlockCatalog.Kind.AIR);
        state(catalog, 18650, "minecraft:cave_air", BlockCatalog.Kind.AIR);
        state(catalog, 1, "minecraft:stone", BlockCatalog.Kind.SAFE_SUPPORT);
        state(catalog, 10, "minecraft:dirt", BlockCatalog.Kind.SAFE_SUPPORT);
        state(catalog, 14, "minecraft:cobblestone", BlockCatalog.Kind.SAFE_SUPPORT);
        state(catalog, 15, "minecraft:oak_planks", BlockCatalog.Kind.SAFE_SUPPORT);
        state(catalog, 8, "minecraft:grass_block", BlockCatalog.Kind.SAFE_SUPPORT);
        state(catalog, 16826, "minecraft:magma_block", BlockCatalog.Kind.BLOCKED);
        state(catalog, 89, "minecraft:water", BlockCatalog.Kind.UNSUPPORTED);
        state(catalog, 15178, "minecraft:oak_slab", BlockCatalog.Kind.UNSUPPORTED);
        state(catalog, 30519, "minecraft:powder_snow", BlockCatalog.Kind.UNSUPPORTED);
        state(catalog, 8597, "minecraft:ice", BlockCatalog.Kind.UNSUPPORTED);
        state(catalog, 121, "minecraft:sand", BlockCatalog.Kind.UNSUPPORTED);
        check(catalog.lookup(15178).properties().equals(Map.of("type", "top", "waterlogged", "false")), "fixed slab properties");
        check(catalog.lookup(1).friction() == 0.6f, "official normal friction");
        for (int id : new int[]{-1, Integer.MIN_VALUE, 35723, Integer.MAX_VALUE}) {
            check(catalog.lookup(id).kind() == BlockCatalog.Kind.UNKNOWN, "invalid state never air");
        }
        int air = 0;
        for (int id = 0; id < catalog.stateCount(); id++) {
            var state = catalog.lookup(id);
            if (state.id() != id || state.name() == null || state.kind() == BlockCatalog.Kind.UNKNOWN)
                throw new AssertionError("missing contiguous state");
            if (state.kind() == BlockCatalog.Kind.AIR) air++;
        }
        check(air == 3, "only official air states are passable");
        rejectMutation(() -> catalog.lookup(8).properties().put("snowy", "false"));
        check(catalog.lookupEntityType(154).name().equals("minecraft:zombie") && catalog.lookupEntityType(154).monster(), "fixed zombie category");
        check(catalog.lookupEntityType(32).name().equals("minecraft:creeper") && catalog.lookupEntityType(32).monster(), "fixed creeper category");
        check(catalog.lookupEntityType(30).category() == BlockCatalog.MobCategory.CREATURE, "cow is not monster");
        check(catalog.lookupEntityType(159).category() == BlockCatalog.MobCategory.MISC, "player is not monster");
        int monsters = 0;
        for (int id = 0; id < catalog.entityTypeCount(); id++) {
            if (catalog.lookupEntityType(id).id() != id) throw new AssertionError("noncontiguous entities");
            if (catalog.lookupEntityType(id).monster()) monsters++;
        }
        check(monsters == 45, "independent official entity audit count");
        for (int id : new int[]{-1, 161, Integer.MAX_VALUE})
            check(catalog.lookupEntityType(id).category() == BlockCatalog.MobCategory.UNKNOWN, "unknown entity never safe");

        byte[] bytes;
        try (InputStream input = BlockCatalogTest.class.getResourceAsStream("/dev/evelyn/minecraft/block-catalog-26.3.json")) {
            if (input == null) throw new AssertionError("catalog fixture missing");
            bytes = input.readAllBytes();
        }
        reject(() -> BlockCatalog.read(bytes, "0".repeat(64)));
        byte[] mutated = bytes.clone(); mutated[mutated.length - 2] ^= 1;
        reject(() -> BlockCatalog.read(mutated, BlockCatalog.RESOURCE_SHA256));
        reject(() -> BlockCatalog.read(new byte[8 * 1024 * 1024 + 1], "0".repeat(64)));
        JsonObject original = JsonParser.parseString(new String(bytes, StandardCharsets.UTF_8)).getAsJsonObject();
        invalid(original, root -> root.addProperty("protocol", 776));
        invalid(original, root -> root.addProperty("game_version", "26.2"));
        invalid(original, root -> root.addProperty("client_sha1", "0".repeat(40)));
        invalid(original, root -> root.addProperty("global_bits", 15));
        invalid(original, root -> root.addProperty("state_count", 35722));
        invalid(original, root -> root.addProperty("unexpected", true));
        invalid(original, root -> root.getAsJsonArray("states").remove(0));
        invalid(original, root -> root.getAsJsonArray("states").get(0).getAsJsonArray().set(0, new JsonPrimitive(1)));
        invalid(original, root -> root.getAsJsonArray("states").get(0).getAsJsonArray().set(1, new JsonPrimitive(-1)));
        invalid(original, root -> root.getAsJsonArray("states").get(0).getAsJsonArray().set(3, new JsonPrimitive("true")));
        invalid(original, root -> root.getAsJsonArray("states").get(0).getAsJsonArray().set(6, new JsonPrimitive("bogus")));
        invalid(original, root -> root.getAsJsonArray("states").get(30519).getAsJsonArray().set(7, new JsonPrimitive(true)));
        invalid(original, root -> root.getAsJsonArray("block_types").get(0).getAsJsonArray().set(2, new JsonPrimitive(-0.1)));
        invalid(original, root -> root.getAsJsonArray("block_types").get(0).getAsJsonArray().set(0, new JsonPrimitive("bad name")));
        invalid(original, root -> root.getAsJsonArray("entity_types").get(0).getAsJsonArray().set(0, new JsonPrimitive(1)));
        invalid(original, root -> root.getAsJsonArray("entity_types").get(0).getAsJsonArray().set(2, new JsonPrimitive("UNKNOWN")));
        invalid(original, root -> root.getAsJsonArray("entity_types").get(0).getAsJsonArray().set(2, new JsonPrimitive("PASSIVE")));
        invalid(original, root -> root.getAsJsonArray("states").get(0).getAsJsonArray().get(2).getAsJsonObject().addProperty("invalid field", "true"));
        invalid(original, root -> root.getAsJsonArray("states").get(0).getAsJsonArray().set(0, new JsonPrimitive(0.5)));
        reclassify(original, root -> row(root, 0).set(3, new JsonPrimitive(false)), 0, BlockCatalog.Kind.UNSUPPORTED);
        reclassify(original, root -> row(root, 1).set(6, new JsonPrimitive("partial")), 1, BlockCatalog.Kind.UNSUPPORTED);
        reclassify(original, root -> row(root, 1).set(7, new JsonPrimitive(false)), 1, BlockCatalog.Kind.UNSUPPORTED);
        reclassify(original, root -> row(root, 1).set(4, new JsonPrimitive(true)), 1, BlockCatalog.Kind.UNSUPPORTED);
        reclassify(original, root -> {
            row(root, 1).set(5, new JsonPrimitive(true)); row(root, 1).set(6, new JsonPrimitive("contextual")); row(root, 1).set(7, JsonNull.INSTANCE);
        }, 1, BlockCatalog.Kind.UNSUPPORTED);
        for (int column : new int[]{2, 3, 4}) {
            reclassify(original, root -> descriptor(root, 1).set(column, new JsonPrimitive(column == 2 ? 0.98 : 0.5)), 1, BlockCatalog.Kind.UNSUPPORTED);
        }
        reclassify(original, root -> descriptor(root, 1).set(1, new JsonPrimitive("net.minecraft.world.level.block.UnreviewedBlock")), 1, BlockCatalog.Kind.UNSUPPORTED);
        reclassify(original, root -> descriptor(root, 16826).set(1, new JsonPrimitive("net.minecraft.world.level.block.Block")), 16826, BlockCatalog.Kind.BLOCKED);
        String valid = new String(bytes, StandardCharsets.UTF_8);
        invalidText(valid + "{}");
        invalidText(valid.replaceFirst("\\{", "{\"schema\":1,"));
        geometryRejections(original);
        System.out.println("BLOCK_CATALOG_PASS " + checks + " checks");
    }

    private static void geometryOracles(BlockCatalog c) {
        var bottom=c.lookup(15252);
        check(bottom.geometrySupported() && bottom.kind()==BlockCatalog.Kind.UNSUPPORTED,"v4 geometry does not change v3 slab policy");
        check(bottom.collisionBoxes().equals(List.of(new BlockCatalog.Box(0,0,0,1,.5,1))),"manual bottom stone slab");
        check(c.lookup(15250).collisionBoxes().equals(List.of(new BlockCatalog.Box(0,.5,0,1,1,1))),"manual top stone slab");
        check(c.lookup(15254).collisionBoxes().equals(List.of(new BlockCatalog.Box(0,0,0,1,1,1))),"manual double slab");
        check((bottom.sturdyFaces()&BlockCatalog.DOWN)!=0 && (bottom.sturdyFaces()&BlockCatalog.UP)==0,"bottom slab sturdy bottom, not top face");
        check((c.lookup(15250).sturdyFaces()&BlockCatalog.UP)!=0 && (c.lookup(15250).sturdyFaces()&BlockCatalog.DOWN)==0,"top slab sturdy top, not bottom face");
        check(c.lookup(1).sturdyFaces()==63 && c.lookup(0).sturdyFaces()==0,"cube and air masks");
        check(c.lookup(5474).collisionBoxes().equals(List.of(new BlockCatalog.Box(0,0,0,1,.5,1),new BlockCatalog.Box(0,.5,0,1,1,.5))),"manual north bottom straight stair");
        check(c.lookup(5480).collisionBoxes().equals(List.of(new BlockCatalog.Box(0,0,0,1,.5,1),new BlockCatalog.Box(0,.5,0,.5,1,.5))),"manual north bottom outer-left stair");
        check(c.lookup(5476).collisionBoxes().equals(List.of(new BlockCatalog.Box(0,0,0,1,.5,1),new BlockCatalog.Box(0,.5,0,.5,1,1),new BlockCatalog.Box(.5,.5,0,1,1,.5))),"manual north bottom inner-left stair");
        int orientations=0;
        for(int id=5463;id<5543;id++) {
            var s=c.lookup(id); if(!s.properties().get("waterlogged").equals("false")) continue;
            boolean top=s.properties().get("half").equals("top"); String shape=s.properties().get("shape"),facing=s.properties().get("facing");
            int occupied=0;
            for(double x:new double[]{.25,.75}) for(double y:new double[]{.25,.75}) for(double z:new double[]{.25,.75}) {
                boolean front=switch(facing) {case "north"->z<.5;case "south"->z>.5;case "west"->x<.5;case "east"->x>.5;default->throw new AssertionError("facing");};
                boolean left=switch(facing) {case "north"->x<.5;case "south"->x>.5;case "west"->z>.5;case "east"->z<.5;default->false;};
                boolean expected=(top?y>.5:y<.5) || switch(shape) {
                    case "straight"->front;case "inner_left"->front||left;case "inner_right"->front||!left;
                    case "outer_left"->front&&left;case "outer_right"->front&&!left;default->throw new AssertionError("shape");
                };
                boolean actual=contains(s.collisionBoxes(),x,y,z);
                check(actual==expected,"independent octant occupancy "+id); if(actual)occupied++;
            }
            check(occupied==(shape.startsWith("inner")?7:shape.startsWith("outer")?5:6),"independent stair volume"); orientations++;
        }
        check(orientations==40,"all four facings, two halves, five shapes");
        for(int id=89;id<=104;id++) {
            var s=c.lookup(id);var f=s.fluid();int level=id-89;
            int amount=level==0||level>=8?8:8-level;
            check(s.geometrySupported() && s.collisionBoxes().isEmpty() && s.kind()==BlockCatalog.Kind.UNSUPPORTED,"water geometry preserves legacy policy");
            check(f.kind()==BlockCatalog.FluidKind.WATER && f.amount()==amount && f.ownHeight()==amount/9f && f.source()==(level==0) && f.falling()==(level>=8),"manual LiquidBlock fluid cache "+level);
        }
        check(c.lookup(15251).fluid().kind()==BlockCatalog.FluidKind.WATER && !c.lookup(15251).geometrySupported(),"waterlogged slab is not movement support");
        check(c.lookup(32877).fluid().source() && c.lookup(32877).fluid().falling() && !c.lookup(32877).geometrySupported(),"official special falling source remains unsupported");
        for(int id:new int[]{-1,35723,105,16826,30519,8597,121,5463})
            check(!c.lookup(id).geometrySupported() && c.lookup(id).collisionBoxes().isEmpty(),"unknown or special contact never inferred safe "+id);
        check(c.lookup(-1).fluid().kind()==BlockCatalog.FluidKind.UNSUPPORTED,"unknown fluid never empty");
        check(c.lookup(0).fluid().kind()==BlockCatalog.FluidKind.NONE && c.lookup(1).speedFactor()==1 && c.lookup(1).jumpFactor()==1,"air and material coefficients");
        check(c.defaultFlowBlockIds().size()==1026 && c.defaultFlowBlockIds().contains(1) && !c.defaultFlowBlockIds().contains(37),"official block IDs distinguish water state ID89 from block ID37");
        check(c.lookup(1).blocksFluidFlow() && bottom.blocksFluidFlow() && c.lookup(5474).blocksFluidFlow() && !c.lookup(89).blocksFluidFlow() && !c.lookup(0).blocksFluidFlow(),"official default flow tag facts");
        rejectMutation(()->bottom.collisionBoxes().clear()); rejectMutation(()->c.defaultFlowBlockIds().clear());
        var player=c.lookupEntityType(159);var creeper=c.lookupEntityType(32);var zombie=c.lookupEntityType(154);
        check(player.width()==.6f && player.height()==1.8f && player.eyeHeight()==1.62f && !player.fixedDimensions(),"official default standing player dimensions");
        check(creeper.width()==.6f && creeper.height()==1.7f && creeper.eyeHeight()==1.445f,"official default creeper dimensions");
        check(zombie.width()==.6f && zombie.height()==1.95f && zombie.eyeHeight()==1.74f,"official default adult zombie dimensions");
        check(c.lookupEntityType(-1).width()==0 && c.lookupEntityType(-1).height()==0,"unknown entity has no invented body");
    }
    private static boolean contains(List<BlockCatalog.Box> boxes,double x,double y,double z) {
        return boxes.stream().anyMatch(b->x>b.minX() && x<b.maxX() && y>b.minY() && y<b.maxY() && z>b.minZ() && z<b.maxZ());
    }
    private static void geometryRejections(JsonObject original) throws Exception {
        invalid(original,r->r.addProperty("schema",1));
        invalid(original,r->row(r,15252).get(8).getAsJsonArray().get(0).getAsJsonArray().set(0,new JsonPrimitive(-.01)));
        invalid(original,r->row(r,15252).get(8).getAsJsonArray().get(0).getAsJsonArray().set(3,new JsonPrimitive(1.01)));
        invalid(original,r->row(r,15252).get(8).getAsJsonArray().get(0).getAsJsonArray().set(0,new JsonPrimitive(1)));
        invalid(original,r->row(r,15252).get(8).getAsJsonArray().get(0).getAsJsonArray().set(0,new JsonPrimitive("0")));
        invalid(original,r->{var a=row(r,15252).get(8).getAsJsonArray();a.add(a.get(0).deepCopy());});
        invalid(original,r->{var a=row(r,5476).get(8).getAsJsonArray();a.add(new JsonArray());});
        invalid(original,r->row(r,15252).set(8,JsonNull.INSTANCE));
        invalid(original,r->row(r,15252).set(9,new JsonPrimitive(64)));
        invalid(original,r->row(r,89).get(10).getAsJsonArray().set(0,new JsonPrimitive("LAVA")));
        invalid(original,r->row(r,89).get(10).getAsJsonArray().set(1,new JsonPrimitive(9)));
        invalid(original,r->row(r,89).get(10).getAsJsonArray().set(3,new JsonPrimitive(1)));
        invalid(original,r->row(r,0).get(10).getAsJsonArray().set(2,new JsonPrimitive(true)));
        invalid(original,r->r.getAsJsonArray("default_flow_block_ids").remove(0));
        invalid(original,r->r.getAsJsonArray("default_flow_block_ids").set(0,new JsonPrimitive(1286)));
        invalid(original,r->r.getAsJsonArray("default_flow_block_ids").set(0,r.getAsJsonArray("default_flow_block_ids").get(1)));
        invalid(original,r->r.getAsJsonArray("entity_types").get(159).getAsJsonArray().set(3,new JsonPrimitive(-1)));
        invalid(original,r->r.getAsJsonArray("entity_types").get(159).getAsJsonArray().set(3,new JsonPrimitive(33)));
        invalid(original,r->r.getAsJsonArray("entity_types").get(159).getAsJsonArray().set(5,new JsonPrimitive(2)));
        invalid(original,r->r.getAsJsonArray("entity_types").get(159).getAsJsonArray().set(6,new JsonPrimitive("false")));
        invalidText(original.toString().replaceFirst("0.8888889","1e400"));
    }

    private static void state(BlockCatalog catalog, int id, String name, BlockCatalog.Kind kind) {
        check(catalog.lookup(id).id() == id && name.equals(catalog.lookup(id).name()) && catalog.lookup(id).kind() == kind, "fixed official state " + id);
    }
    private static void invalid(JsonObject original, java.util.function.Consumer<JsonObject> mutation) throws Exception {
        JsonObject changed = original.deepCopy(); mutation.accept(changed); invalidText(changed.toString());
    }
    private static JsonArray row(JsonObject root, int id) { return root.getAsJsonArray("states").get(id).getAsJsonArray(); }
    private static JsonArray descriptor(JsonObject root, int id) { return root.getAsJsonArray("block_types").get(row(root, id).get(1).getAsInt()).getAsJsonArray(); }
    private static void reclassify(JsonObject original, java.util.function.Consumer<JsonObject> mutation, int id, BlockCatalog.Kind expected) throws Exception {
        JsonObject changed = original.deepCopy(); mutation.accept(changed);
        byte[] bytes = changed.toString().getBytes(StandardCharsets.UTF_8);
        check(BlockCatalog.read(bytes, sha(bytes)).lookup(id).kind() == expected, "every floor condition must fail closed independently");
    }
    private static void invalidText(String text) throws Exception {
        byte[] bytes = text.getBytes(StandardCharsets.UTF_8); reject(() -> BlockCatalog.read(bytes, sha(bytes)));
    }
    private static String sha(byte[] bytes) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    }
    private static void check(boolean value, String message) { if (!value) throw new AssertionError(message); checks++; }
    private static void reject(Checked action) throws Exception {
        try { action.run(); } catch (IOException expected) { checks++; return; }
        throw new AssertionError("invalid catalog accepted");
    }
    private static void rejectMutation(Runnable action) {
        try { action.run(); } catch (UnsupportedOperationException expected) { checks++; return; }
        throw new AssertionError("catalog properties were mutable");
    }
    private interface Checked { void run() throws Exception; }
}
