package dev.evelyn.minecraft;

import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import com.google.gson.JsonArray;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.function.BooleanSupplier;

/** Explicit one-shot controller. The connection lifetime lock serializes observations and simulation. */
final class VanillaControl implements DirectClient.ControlExecutor {
    final BlockCatalog catalog;
    private final VanillaConnection connection;
    private final MovementController movement;
    private final LocalPlanner planner;
    private final EntityTracker entities;
    private final boolean advanced;
    private final TerrainCollision terrain;
    private final MovementController.World world;
    private final CombatController combat;
    private final BareHandInventory inventory=new BareHandInventory();
    private final InventoryState playerInventory;
    private final BlockInteractionState blockInteractions;
    private final PlayerAttributes playerAttributes;
    private RecipeState playerRecipes;
    private int inventoryPage;
    private InventoryTransfer inventoryTransfer;
    private HandSwap handSwap;
    private boolean flowTagsConfirmed,attacking;
    private int targetId=-1;
    private long targetEpoch,damageHoldUntil;
    private Integer airSupply;
    private BooleanSupplier authority=()->false;
    private long sequence, environment, damageEpoch, projectionSeq, startDamage, deliveredRisk, riskUntil;
    private int selfId=-1;
    private float lastHealth=Float.NaN, lastMetadataHealth=Float.NaN;
    private boolean off, cancelled, navigating, active, abilities, attributes, hasRiskHold;
    private String result, code="off";
    private JsonObject bundleView;
    VanillaControl(VanillaConnection connection,BlockCatalog catalog) {
        this(connection,catalog,false);
    }
    VanillaControl(VanillaConnection connection,BlockCatalog catalog,boolean advanced) {
        this(connection,catalog,advanced,false);
    }
    VanillaControl(VanillaConnection connection,BlockCatalog catalog,boolean advanced,boolean player) {
        this.connection=connection; this.catalog=catalog;
        if(player&&!advanced)throw new IllegalArgumentException("player_mode_requires_advanced");
        playerInventory=player?new InventoryState():null;
        blockInteractions=player?new BlockInteractionState():null;
        playerAttributes=player?new PlayerAttributes():null;
        this.advanced=advanced;movement=new MovementController(connection.observationOriginNanos(),advanced); planner=new LocalPlanner(movement); entities=new EntityTracker(catalog);
        combat=new CombatController(connection.observationOriginNanos());terrain=new TerrainCollision(connection.world,catalog,()->flowTagsConfirmed);
        world=advanced?new MovementController.World(){
            public boolean safe(double x,double y,double z){return !entities.near(new EntityTracker.Point(x,y,z),2,false)&&terrain.safe(x,y,z);}
            public double friction(double x,double y,double z){return terrain.friction(x,y,z);}
            public MovementController.Region sample(MovementController.Box box){return terrain.sample(box);}
            public MovementController.FluidContact fluid(MovementController.Box box){return terrain.fluid(box);}
        }:legacyWorld;
    }
    void player(int id,int mode) throws IOException {
        if(id<0 || mode!=0) throw failure("unsupported_game_mode");
        if(playerAttributes!=null){
            playerAttributes.bind(id);discardRecipes();
            playerRecipes=new RecipeState(playerAttributes.snapshot().generation(),connection.componentRegistries());
        }
        selfId=id; attributes=true;airSupply=300; environment++;
    }
    void flowTags(Set<Integer> blocks,Set<Integer> water){if(advanced){flowTagsConfirmed=blocks!=null&&blocks.equals(catalog.defaultFlowBlockIds())&&Set.of(1,2).equals(water);environment++;}}
    boolean advanced(){return advanced;}
    void beginBundle() { bundleView=observation();entities.beginBundle(); }
    void endBundle() { long old=entities.dangerEpoch(); entities.endBundle(); if(entities.dangerEpoch()!=old)holdRisk();bundleView=null; }
    void environmentChanged() { environment++; }
    void gameEvent(int type,float value) throws IOException {
        if(type!=3)return;
        if(value<0||value>3||value!=Math.rint(value))throw failure("invalid_game_mode");
        if(value!=0)throw failure("unsupported_game_mode");
    }
    void health(float value) {
        damage(lastHealth,value);
        lastHealth=value; environment++; if(value<=0) off();
    }
    private void metadataHealth(float value) {
        damage(Float.isFinite(lastMetadataHealth)?lastMetadataHealth:lastHealth,value);
        lastMetadataHealth=value; if(value<=0)off();
    }
    private void damage(float before,float after) { if(Float.isFinite(before)&&after<before) {damageEpoch++;holdRisk();damageHoldUntil=System.nanoTime()+1_000_000_000L;} }
    void closed(long now) { off=true; active=false; authority=()->false; bundleView=null;entities.clear();inventory.clear();if(playerInventory!=null)playerInventory.clear();if(blockInteractions!=null)blockInteractions.clear();if(playerAttributes!=null)playerAttributes.clear();discardRecipes(); planner.off(now);combat.off(now); if(result==null)code="off"; }
    private void discardRecipes(){
        if(playerRecipes!=null){try{playerRecipes.clear(playerRecipes.snapshot().generation());}catch(IOException exhausted){/* Closing this owner permanently drops the context. */}playerRecipes=null;}
    }
    @Override public void off() { synchronized(connection.lifetime) { off=true; authority=()->false; connection.close(); } }
    @Override public void fence(int seq) {
        synchronized(connection.lifetime) {
            if(seq==sequence&&(active||advanced&&movement.needsMaintenance())) { cancelled=true; environment++; connection.fenceControlWrite(); }
        }
    }
    @Override public void stop() {
        // A remote cancellation ends this one-shot connection; it never reports an instantaneous physical rollback.
        off();
    }
    @Override public void start(JsonObject command,BooleanSupplier permit) {
        synchronized(connection.lifetime) {
            long now=System.nanoTime(); sequence=command.get("seq").getAsInt(); authority=permit; cancelled=false;
            result=null; code="running"; active=false; inventoryTransfer=null;handSwap=null; startDamage=damageEpoch;
            if(playerInventory!=null&&command.get("action").getAsString().equals("inventory_page")) {
                attacking=false;navigating=false;
                if(off||!connection.controlReady()||!permit.getAsBoolean()){failResult("control_invalidated");return;}
                if(command.get("inventory_revision").getAsLong()!=playerInventory.snapshot(0).get("revision").getAsLong()){failResult("inventory_changed");return;}
                int requested=command.get("page_start").getAsInt();
                try{playerInventory.snapshot(requested);}catch(IllegalArgumentException invalid){failResult("inventory_page_invalid");return;}
                inventoryPage=requested;result="succeeded";code="inventory_page_selected";return;
            }
            if(off||!connection.controlReady()||!abilities||!attributes||!Float.isFinite(lastHealth)||lastHealth<=0||movement.snapshot().pose()==null) { failResult("control_not_ready"); return; }
            MovementController.Pose pose=movement.snapshot().pose();
            String action=command.get("action").getAsString();attacking=advanced&&action.equals("attack");
            targetId=attacking?command.get("target_id").getAsInt():-1;targetEpoch=attacking?command.get("target_epoch").getAsLong():0;
            if(playerInventory!=null&&Set.of("inventory_transfer","swap_hands").contains(action)&&!inventoryStable()){
                failResult("inventory_requires_stable_ground");connection.close();return;
            }
            if(!(attacking?terrain:world).safe(pose.x(),pose.y(),pose.z())||attacking&&entities.nearExcept(point(),2,targetId,targetEpoch,false)) { failResult("unsafe_support"); return; }
            if(attacking?entities.nearExcept(point(),8,targetId,targetEpoch)||entities.recentDangerExcept(targetEpoch,now)||damageHoldUntil!=0&&now-damageHoldUntil<0
                    :entities.near(point(),8,true)||recentRisk(now)) { failResult("recent_risk"); return; }
            if(playerInventory!=null&&action.equals("inventory_transfer")){
                navigating=false;
                try{inventoryTransfer=new InventoryTransfer(playerInventory.view(),command.get("source_slot").getAsInt(),command.get("destination_slot").getAsInt(),
                        command.get("amount").getAsInt(),command.get("inventory_revision").getAsLong());}
                catch(IllegalArgumentException invalid){failResult("inventory_transfer_hash_unavailable".equals(invalid.getMessage())?invalid.getMessage():"inventory_transfer_invalid");return;}
                active=true;deliveredRisk=damageEpoch+entities.dangerEpoch();return;
            }
            if(playerInventory!=null&&action.equals("swap_hands")){
                navigating=false;InventoryState.Selection selected=playerInventory.selection();
                try{handSwap=new HandSwap(playerInventory.view(),selected.index(),selected.revision(),command.get("inventory_revision").getAsLong());}
                catch(IllegalArgumentException invalid){failResult("hand_swap_unobservable".equals(invalid.getMessage())?invalid.getMessage():"hand_swap_invalid");return;}
                active=true;deliveredRisk=damageEpoch+entities.dangerEpoch();return;
            }
            long deadline=now+command.get("run_ms").getAsLong()*1_000_000L;
            navigating=action.equals("navigate");
            if(navigating) planner.beginNavigate(sequence,command.get("target_x").getAsDouble()+.5,
                    command.get("target_y").getAsDouble(),command.get("target_z").getAsDouble()+.5,deadline,now);
            else if(attacking)combat.beginAttack(sequence,targetId,targetEpoch,deadline,now);
            else if(advanced&&action.equals("jump"))movement.beginJump(sequence,1,deadline,now);
            else movement.beginLook(sequence,1,command.get("yaw").getAsFloat(),command.get("pitch").getAsFloat(),deadline,now);
            active=true;
            deliveredRisk=damageEpoch+(attacking?entities.dangerEpochExcept(targetEpoch):entities.dangerEpoch());
        }
    }
    @Override public String poll() throws IOException {
        if(inventoryTransfer!=null)return pollInventoryTransfer();
        if(handSwap!=null)return pollHandSwap();
        if(advanced&&attacking)return pollAttack();
        MovementController.PreparedStep prepared; long revision;
        synchronized(connection.lifetime) {
            if(result!=null)return result;
            if(connection.controlSuspended())return null;
            if(!active||off||cancelled||!authority.getAsBoolean()||!connection.controlReady()) { failResult("control_invalidated"); return result; }
            long now=System.nanoTime(), risk=damageEpoch+entities.dangerEpoch();
            boolean hazard=entities.near(point(),8,true)||risk!=deliveredRisk||recentRisk(now);
            if(!navigating&&(hazard||damageEpoch!=startDamage)) { failResult("risk_during_look"); connection.close(); return result; }
            prepared=navigating?planner.tick(now,world,hazard,damageEpoch+entities.dangerEpoch()):movement.prepareTick(now,world);
            deliveredRisk=risk;
            revision=environment;
            collectResult(); if(result!=null || prepared==null)return result;
        }
        boolean written=connection.sendControl(prepared,()->!off&&!cancelled&&active&&authority.getAsBoolean()
                &&environment==revision&&!entities.nearSegment(point(),new EntityTracker.Point(prepared.pose().x(),prepared.pose().y(),prepared.pose().z()),2)
                &&(navigating?planner.valid(prepared,System.nanoTime()):movement.valid(prepared,System.nanoTime())),()->{
            if(!(navigating?planner.commit(prepared,System.nanoTime()):movement.commit(prepared,System.nanoTime()))) throw new IllegalStateException("movement_commit_failed");
        });
        synchronized(connection.lifetime) {
            if(!written) { failResult("control_commit_invalidated"); connection.close(); }
            else collectResult();
            return result;
        }
    }
    private boolean inventoryStable(){
        MovementController.Pose p=pose();if(p==null||!movement.ready()||movement.needsMaintenance())return false;
        MovementController.FluidContact fluid=terrain.fluid(MovementController.body(p));
        return fluid!=null&&fluid.known()&&fluid.depth()==0&&world.safe(p.x(),p.y(),p.z());
    }
    private boolean inventoryAuthority(){return !off&&!cancelled&&active&&authority.getAsBoolean()&&(connection.controlReady()||connection.controlSuspended())
            &&damageEpoch==startDamage&&!entities.near(point(),8,true)&&!recentRisk(System.nanoTime())
            &&inventoryStable()&&inventoryTransfer!=null&&inventoryTransfer.sameLifetime(playerInventory.view());}
    private String pollInventoryTransfer()throws IOException {
        InventoryTransfer.Click step;long revision;
        synchronized(connection.lifetime){
            if(result!=null)return result;
            if(connection.controlSuspended())return null;
            if(!inventoryAuthority()){failResult("control_invalidated");connection.close();return result;}
            step=inventoryTransfer.prepare(playerInventory.view());
            if(!inventoryTransfer.status().equals("running")){result=inventoryTransfer.status();code=inventoryTransfer.code();active=false;return result;}
            if(step==null)return null;revision=environment;
        }
        boolean sent=connection.sendInventoryClick(step,()->inventoryAuthority()&&environment==revision&&inventoryTransfer.valid(step,playerInventory.view()),
                this::inventoryAuthority,()->inventoryTransfer.commit(step,playerInventory.view()));
        synchronized(connection.lifetime){if(!sent){failResult("inventory_commit_invalidated");connection.close();}return result;}
    }
    private boolean handSwapAuthority(){
        InventoryState.Selection selected=playerInventory.selection();
        return !off&&!cancelled&&active&&authority.getAsBoolean()&&(connection.controlReady()||connection.controlSuspended())
                &&damageEpoch==startDamage&&!entities.near(point(),8,true)&&!recentRisk(System.nanoTime())&&inventoryStable()
                &&handSwap!=null&&handSwap.sameLifetime(playerInventory.view(),selected.index(),selected.revision());
    }
    private String pollHandSwap()throws IOException {
        HandSwap.Step step;long revision;
        synchronized(connection.lifetime){
            if(result!=null)return result;if(connection.controlSuspended())return null;
            if(!handSwapAuthority()){failResult("control_invalidated");connection.close();return result;}
            InventoryState.Selection selected=playerInventory.selection();step=handSwap.prepare(playerInventory.view(),selected.index(),selected.revision());
            if(!handSwap.status().equals("running")){result=handSwap.status();code=handSwap.code();active=false;return result;}
            if(step==null)return null;revision=environment;
        }
        boolean sent=connection.sendHandSwap(()->{
            InventoryState.Selection selected=playerInventory.selection();
            return handSwapAuthority()&&environment==revision&&handSwap.valid(step,playerInventory.view(),selected.index(),selected.revision());
        },this::handSwapAuthority,()->{
            InventoryState.Selection selected=playerInventory.selection();handSwap.commit(step,playerInventory.view(),selected.index(),selected.revision());
        });
        synchronized(connection.lifetime){if(!sent){failResult("hand_swap_commit_invalidated");connection.close();}return result;}
    }
    @Override public void idle(BooleanSupplier lease)throws IOException {
        if(!advanced)return;MovementController.PreparedStep prepared;long revision;
        synchronized(connection.lifetime){
            if(off)return;
            if(!movement.needsMaintenance()){
                if(sequence>0&&result!=null){MovementController.Pose p=pose();MovementController.FluidContact fluid=p==null?null:terrain.fluid(MovementController.body(p));
                    if(result.equals("failed")||fluid==null||!fluid.known()||fluid.depth()>0||!terrain.safe(p.x(),p.y(),p.z()))connection.close();}
                return;
            }
            if(!lease.getAsBoolean()||airSupply!=null&&airSupply<60){connection.close();return;}
            if(connection.controlSuspended())return;
            if(!connection.controlReady()){connection.close();return;}
            if(entities.near(point(),8,true)||recentRisk(System.nanoTime())){connection.close();return;}
            prepared=movement.prepareMaintenance(System.nanoTime(),world);revision=environment;
            if(prepared==null){if(movement.snapshot().status()==MovementController.Status.FAILED)connection.close();return;}
        }
        boolean written=connection.sendControl(prepared,()->!off&&lease.getAsBoolean()&&environment==revision
            &&!entities.nearSegment(point(),new EntityTracker.Point(prepared.pose().x(),prepared.pose().y(),prepared.pose().z()),2)
            &&movement.valid(prepared,System.nanoTime()),()->{if(!movement.commit(prepared,System.nanoTime()))throw new IllegalStateException("maintenance_commit_failed");});
        if(!written)connection.close();
    }
    private String pollAttack()throws IOException {
        CombatController.PreparedAttack step;MovementController.Pose aimed;
        synchronized(connection.lifetime){
            if(result!=null)return result;if(connection.controlSuspended())return null;
            if(!active||off||cancelled||!authority.getAsBoolean()||!connection.controlReady()){failResult("control_invalidated");connection.close();return result;}
            step=combat.prepare(System.nanoTime(),combatContext());collectCombat();if(step==null||result!=null)return result;
            EntityTracker.Point aim=aimPoint(entities.target(targetId));MovementController.Pose p=pose();double dx=aim.x()-p.x(),dy=aim.y()-p.y()-(double)1.62f,dz=aim.z()-p.z();
            aimed=new MovementController.Pose(p.x(),p.y(),p.z(),(float)Math.toDegrees(Math.atan2(-dx,dz)),(float)-Math.toDegrees(Math.atan2(dy,Math.hypot(dx,dz))));
        }
        boolean written=connection.sendAttack(step.targetId(),aimed,()->!off&&!cancelled&&active&&authority.getAsBoolean()
            &&combat.valid(step,System.nanoTime(),combatContext()),()->{
                long now=System.nanoTime();if(!combat.commit(step,now,combatContext()))throw new IllegalStateException("attack_commit_failed");
                movement.face(aimed.yaw(),aimed.pitch(),now);
            });
        synchronized(connection.lifetime){if(!written){failResult("attack_commit_invalidated");connection.close();}else collectCombat();return result;}
    }
    private void collectCombat(){var state=combat.snapshot();if(state.status()==CombatController.Status.SUCCEEDED){result="succeeded";code=state.code();active=false;}
        else if(state.status()==CombatController.Status.FAILED||state.status()==CombatController.Status.OFF||state.status()==CombatController.Status.CANCELLED){failResult(state.code());connection.close();}}
    private CombatController.Context combatContext(){
        EntityTracker.Entity target=entities.target(targetId);MovementController.Pose p=pose();MovementController.Velocity v=velocity();
        MovementController.Box box=targetBox(target);CombatController.Target selected=target==null||box==null?null:new CombatController.Target(targetId,target.epoch(),box,true,!target.dead());
        boolean safe=target!=null&&target.geometryKnown()&&movement.ready()&&Math.abs(v.x())<.003&&Math.abs(v.z())<.003
            &&terrain.safe(p.x(),p.y(),p.z())&&terrain.fluid(MovementController.body(p)).depth()==0
            &&!entities.nearExcept(point(),2,targetId,targetEpoch,false)&&!entities.nearExcept(point(),8,targetId,targetEpoch)&&damageEpoch+entities.dangerEpochExcept(targetEpoch)==deliveredRisk;
        EntityTracker.Point eye=new EntityTracker.Point(p.x(),p.y()+(double)1.62f,p.z()),aim=aimPoint(target);
        boolean visible=aim!=null&&terrain.lineOfSight(eye,aim);
        if(visible)for(var entry:entities.targets().entrySet()){if(entry.getKey()==targetId)continue;EntityTracker.Entity entity=entry.getValue();MovementController.Box other=targetBox(entity);
            // Unsupported shapes protect the local area without vetoing the entire tracked world.
            if(other==null||TerrainCollision.intersectsRay(eye,aim,other)||!entity.geometryKnown()
                &&Math.hypot(Math.hypot(entity.point().x()-p.x(),entity.point().z()-p.z()),entity.point().y()-p.y())<=8){visible=false;break;}}
        return new CombatController.Context(environment,p,combatHand().equals("empty"),visible,safe,selected);
    }
    private MovementController.Box targetBox(EntityTracker.Entity target){
        if(target==null)return null;BlockCatalog.EntityType type=catalog.lookupEntityType(target.type());EntityTracker.Point p=target.point();double half=(double)(type.width()/2f);
        try{return new MovementController.Box(p.x()-half,p.y(),p.z()-half,p.x()+half,p.y()+type.height(),p.z()+half);}catch(IllegalArgumentException unknown){return null;}
    }
    private EntityTracker.Point aimPoint(EntityTracker.Entity target){MovementController.Box b=targetBox(target);if(b==null||pose()==null)return null;MovementController.Pose p=pose();
        return new EntityTracker.Point(Math.clamp(p.x(),b.minX(),b.maxX()),Math.clamp(p.y()+(double)1.62f,b.minY(),b.maxY()),Math.clamp(p.z(),b.minZ(),b.maxZ()));}
    private void collectResult() {
        String status=navigating?planner.snapshot().status().name():movement.snapshot().status().name();
        if(status.equals("SUCCEEDED")) { result="succeeded"; active=false; code="goal_reached_client_simulated"; }
        else if(status.equals("FAILED")||status.equals("OFF")||status.equals("CANCELLED")) {
            failResult(navigating?planner.snapshot().code():movement.snapshot().code()); connection.close();
        }
    }
    private void failResult(String reason) { result="failed";code=reason;active=false; }
    @Override public String resultCode() { synchronized(connection.lifetime) { return code; } }
    MovementController.Pose pose() { return movement.snapshot().pose(); }
    MovementController.Velocity velocity() { return movement.snapshot().velocity(); }
    void correction(MovementController.Pose pose,MovementController.Velocity velocity,long now) throws IOException {
        environment++;
        if(!advanced&&(velocity.x()!=0||velocity.z()!=0||velocity.y()>0||velocity.y()<-.1)) throw failure("unsupported_external_velocity");
        planner.correction(pose,velocity,now);
        if(active) { failResult("server_correction"); }
    }
    void rotation(float yaw,boolean relativeYaw,float pitch,boolean relativePitch) throws IOException {
        MovementController.Pose old=pose(); if(old==null)throw failure("rotation_before_position");
        correction(new MovementController.Pose(old.x(),old.y(),old.z(),yaw+(relativeYaw?old.yaw():0),
                Math.max(-90,Math.min(90,pitch+(relativePitch?old.pitch():0)))),velocity(),System.nanoTime());
    }
    private EntityTracker.Point point() {
        MovementController.Pose p=pose(); return p==null?null:new EntityTracker.Point(p.x(),p.y(),p.z());
    }
    private String combatHand() {
        if(playerInventory!=null) {
            JsonObject state=playerInventory.snapshot(0);
            if(!state.get("known").getAsBoolean()||state.get("container_id").getAsInt()!=0)return "unknown";
        }
        return inventory.hand();
    }
    boolean playerControl(){return playerInventory!=null;}
    PlayerAttributes.Snapshot attributeSnapshot(){synchronized(connection.lifetime){return playerAttributes==null?null:playerAttributes.snapshot();}}
    RecipeState.Snapshot recipeSnapshot(){synchronized(connection.lifetime){return playerRecipes==null?null:playerRecipes.snapshot();}}
    int acknowledgedBlocksThrough(){return blockInteractions==null?-1:blockInteractions.acknowledgedThrough();}
    BlockInteractionState.Observation blockPacket(int id,VanillaConnection.Cursor c)throws IOException{
        int size=c.data.available();if(blockInteractions==null||size>BlockInteractionState.MAX_PAYLOAD_BYTES)throw failure("block_observation_budget");
        byte[] payload=new byte[size];c.data.readFully(payload);c.end();return blockInteractions.receive(id,payload);
    }
    boolean packet(int id,VanillaConnection.Cursor c) throws IOException {
        if(playerInventory!=null&&RecipeState.supports(id)){
            if(playerRecipes==null)throw failure("recipe_context_unavailable");
            int size=c.data.available();if(size>1048576)throw failure("recipe_payload_budget");
            byte[] payload=new byte[size];c.data.readFully(payload);c.end();
            long generation=playerRecipes.snapshot().generation();playerRecipes.accept(generation,id,payload);
            if(id==0x40){var menu=playerInventory.view();var ghost=playerRecipes.snapshot().ghost();
                if(!menu.known()||ghost.containerId()!=menu.containerId())playerRecipes.clearGhost(generation);
            }
            environment++;return true;
        }
        if(blockInteractions!=null&&(id==0x04||id==0x05)){blockPacket(id,c);return true;}
        if(playerInventory!=null&&Set.of(0x11,0x12,0x13,0x14,0x2a,0x3c,0x62,0x6b,0x6e).contains(id)) {
            int remaining=c.data.available();if(remaining>1048576)throw failure("inventory_payload_budget");
            byte[] payload=new byte[remaining];c.data.readFully(payload);c.end();
            long previousEpoch=playerInventory.view().epoch(),previousSelection=playerInventory.selection().revision();playerInventory.accept(id,payload,connection.componentRegistries(),connection.componentRegistryKeys());
            if(playerRecipes!=null&&(previousEpoch!=playerInventory.view().epoch()||Set.of(0x12,0x14,0x62,0x6e).contains(id)))
                playerRecipes.clearGhost(playerRecipes.snapshot().generation());
            if(inventoryTransfer!=null&&active&&previousEpoch!=playerInventory.view().epoch()){
                inventoryTransfer.invalidate("inventory_changed");failResult("inventory_changed");connection.close();return true;
            }
            if(handSwap!=null&&active&&(previousEpoch!=playerInventory.view().epoch()||previousSelection!=playerInventory.selection().revision())){
                handSwap.invalidate("inventory_changed");failResult("inventory_changed");connection.close();return true;
            }
            if(Set.of(0x12,0x14,0x6b,0x6e).contains(id))inventory.accept(id,new VanillaConnection.Cursor(payload));
            try{playerInventory.snapshot(inventoryPage);}catch(IllegalArgumentException changed){inventoryPage=0;}
            environment++;return true;
        }
        switch(id) {
            case 0x01,0x23,0x36,0x37,0x39,0x4e,0x80 -> {
                long old=entities.dangerEpoch();entities.accept(id,c,point());if(entities.dangerEpoch()!=old)holdRisk();
                environment++;return true;
            }
            case 0x41 -> {
                int flags=c.data.readUnsignedByte();float flying=c.finiteFloat(),walking=c.finiteFloat();c.end();
                if(flags!=0||flying!=.05f||walking!=.1f)throw failure("unsupported_abilities"); abilities=true;environment++;return true;
            }
            case 0x67 -> {
                int entity=c.count(Integer.MAX_VALUE); EntityTracker.Point v=EntityTracker.lpVector(c);c.end();
                if(entity==selfId) {
                    if(advanced){planner.externalVelocity(new MovementController.Velocity(v.x(),v.y(),v.z()),System.nanoTime());environment++;if(active){failResult("knockback_preempted");connection.close();}return true;}
                    if(v.x()!=0||v.y()!=0||v.z()!=0)throw failure("unsupported_external_velocity");
                    if(pose()!=null)correction(pose(),new MovementController.Velocity(0,0,0),System.nanoTime());
                }
                return true;
            }
            case 0x86 -> { attributes(c);return true; }
            case 0x65 -> { metadata(c);return true; }
            case 0x12,0x14,0x6b,0x6e -> {if(!advanced)return false;inventory.accept(id,c);environment++;return true;}
            case 0x22 -> {if(!advanced)return false;entities.entityEvent(c);environment++;return true;}
            case 0x24 -> throw failure("explosion_unsupported");
            case 0x87 -> {
                int entity=c.count(Integer.MAX_VALUE);
                if(entity==selfId)throw failure("self_effect_unsupported");c.discard(1024);return true;
            }
            case 0x4f -> {c.count(Integer.MAX_VALUE);c.count(65535);c.end();return true;}
            case 0x6d -> {
                int vehicle=c.count(Integer.MAX_VALUE),count=c.count(1024);boolean mounted=vehicle==selfId&&count!=0;
                for(int i=0;i<count;i++)mounted|=c.count(Integer.MAX_VALUE)==selfId;c.end();
                if(mounted)throw failure("mount_unsupported");return true;
            }
            case 0x82 -> {float rate=c.finiteFloat();boolean frozen=c.bool();c.end();if(rate!=20||frozen)throw failure("unsupported_tick_rate");return true;}
            case 0x83 -> {int steps=c.count(Integer.MAX_VALUE);c.end();if(steps!=0)throw failure("unsupported_tick_step");return true;}
            default -> {return false;}
        }
    }
    private static final Map<Integer,Double> PHYSICS=Map.ofEntries(Map.entry(0,1d),Map.entry(9,0d),Map.entry(17,1d),
        Map.entry(18,.08),Map.entry(19,(double).42f),Map.entry(25,0d),Map.entry(26,(double).1f),Map.entry(30,1d),
        Map.entry(31,.3),Map.entry(33,.6),Map.entry(37,0d));
    private static final Map<Integer,Double> COMBAT=Map.of(3,1d,4,0d,5,4d,13,3d,20,0d);
    private boolean unsupportedAttribute(int id,double base,boolean modified){
        return PHYSICS.containsKey(id)&&(base!=PHYSICS.get(id)||modified)
            ||advanced&&COMBAT.containsKey(id)&&(base!=COMBAT.get(id)||modified);
    }
    private void attributes(VanillaConnection.Cursor c) throws IOException {
        if(playerAttributes!=null){
            boolean self=playerAttributes.accept(playerAttributes.snapshot().generation(),c,packet->{
                boolean unsupported=false,targetUnsupported=false;
                for(var observed:packet.entries().entrySet()){
                    int id=observed.getKey();var value=observed.getValue();
                    boolean modified=value.modifiers().stream().anyMatch(modifier->modifier.amount()!=0);
                    unsupported|=unsupportedAttribute(id,value.base(),modified);
                    targetUnsupported|=id==30&&(value.base()!=1||modified);
                }
                if(packet.entityId()==selfId){if(unsupported)throw failure("unsupported_attributes");}
                else if(advanced&&targetUnsupported){entities.invalidate(packet.entityId());environment++;}
            });
            if(self){attributes=true;environment++;}return;
        }
        int entity=c.count(Integer.MAX_VALUE),count=c.count(40);Set<Integer> seen=new HashSet<>();boolean unsupported=false,targetUnsupported=false;
        for(int i=0;i<count;i++) {
            int id=c.count(39);if(!seen.add(id))throw failure("duplicate_attribute");double base=c.finiteDouble();int modifiers=c.count(128);
            boolean modified=false;Set<String> names=new HashSet<>();
            for(int j=0;j<modifiers;j++) {if(!names.add(c.identifier()))throw failure("duplicate_modifier");modified|=c.finiteDouble()!=0;c.count(2);}
            unsupported|=unsupportedAttribute(id,base,modified);
            if(id==30&&(base!=1||modified))targetUnsupported=true;
        }
        c.end();if(entity==selfId) {if(unsupported)throw failure("unsupported_attributes");attributes=true;environment++;}
        else if(advanced&&targetUnsupported){entities.invalidate(entity);environment++;}
    }
    private void metadata(VanillaConnection.Cursor c) throws IOException {
        int entity=c.count(Integer.MAX_VALUE);if(entity!=selfId){if(advanced){entities.metadata(entity,c);environment++;}else c.discard(1048576);return;}
        Set<Integer> seen=new HashSet<>();boolean unsupported=false;Float pendingHealth=null;Integer pendingAir=null;
        while(true) {
            int key=c.data.readUnsignedByte();if(key==255)break;if(key>20||!seen.add(key))throw failure("unsupported_self_metadata");
            int serializer=c.count(43);
            int expected=switch(key){case 0,8,16->0;case 1,7,12,13,18->1;case 2->6;case 3,4,5,11->8;case 6->20;case 9,17->3;case 10->17;case 14->11;case 15->42;default->19;};
            if(serializer!=expected)throw failure("invalid_self_metadata");
            switch(key) {
                case 0 -> unsupported|=(c.data.readUnsignedByte()&0x9f)!=0;
                case 8 -> unsupported|=c.data.readUnsignedByte()!=0;
                case 16 -> c.data.readByte();
                case 1 -> {int air=c.vi();if(advanced){if(air< -20||air>300)throw failure("invalid_air_supply");pendingAir=Math.max(0,air);}else if(air<0)throw failure("invalid_self_metadata");}
                case 12,13,18,19,20 -> c.count(Integer.MAX_VALUE);
                case 7 -> unsupported|=c.count(Integer.MAX_VALUE)!=0;
                case 2 -> {if(c.bool())throw failure("custom_name_unsupported");}
                case 3,4 -> c.bool();
                case 5,11 -> unsupported|=c.bool();
                case 6 -> unsupported|=c.count(17)!=0;
                case 9,17 -> {float health=c.finiteFloat();if(health<0)throw failure("invalid_self_metadata");if(key==9)pendingHealth=health;}
                case 10 -> {if(c.count(1024)!=0)throw failure("effect_particles_unsupported");}
                case 14 -> {if(c.bool())throw failure("sleeping_unsupported");}
                case 15 -> c.count(1);
            }
        }
        c.end();if(unsupported)throw failure("unsupported_player_state");environment++;
        if(pendingHealth!=null)metadataHealth(pendingHealth);
        if(pendingAir!=null)airSupply=pendingAir;
        if(advanced&&airSupply!=null&&airSupply<60)throw failure("low_air_supply");
    }
    JsonObject observation() {
        synchronized(connection.lifetime) {
            long now=System.nanoTime(); PlayerObservation.Snapshot self=connection.observation();
            if(++projectionSeq>Protocol.MAX_SEQUENCE)throw new IllegalStateException("observation_exhausted");
            if(bundleView!=null) {
                JsonObject frozen=bundleView.deepCopy();frozen.addProperty("seq",projectionSeq);
                frozen.addProperty("sampled_at_ms",Math.max(0,(now-connection.observationOriginNanos())/1_000_000));return frozen;
            }
            JsonObject value=new JsonObject();value.addProperty("seq",projectionSeq);
            value.addProperty("sampled_at_ms",Math.max(0,(now-connection.observationOriginNanos())/1_000_000));value.addProperty("source","local_control");
            value.add("self",Protocol.observation(self,connection.observationOriginNanos()));
            JsonObject motion=null; MovementController.Snapshot m=movement.snapshot();
            if(self.available()&&m.pose()!=null) {
                motion=new JsonObject();MovementController.Pose p=m.pose();MovementController.Velocity v=m.velocity();
                motion.addProperty("x",p.x());motion.addProperty("y",p.y());motion.addProperty("z",p.z());motion.addProperty("yaw",p.yaw());motion.addProperty("pitch",p.pitch());
                motion.addProperty("vx",v.x());motion.addProperty("vy",v.y());motion.addProperty("vz",v.z());motion.addProperty("epoch",m.epoch());motion.addProperty("source","client_simulated");
            }
            value.add("motion",motion==null?JsonNull.INSTANCE:motion); JsonObject plan=new JsonObject();LocalPlanner.Snapshot p=planner.snapshot();
            plan.addProperty("status",navigating?p.status().name().toLowerCase(Locale.ROOT):m.status().name().toLowerCase(Locale.ROOT));
            plan.addProperty("code",code);plan.addProperty("goal_seq",sequence);plan.addProperty("step_id",navigating?p.stepId():m.stepId());
            plan.addProperty("paused",navigating&&p.paused());plan.addProperty("preemptions",navigating?p.preemptions():0);value.add("planner",plan);
            JsonObject risk=new JsonObject();risk.addProperty("damage_epoch",damageEpoch);risk.addProperty("monster_epoch",entities.dangerEpoch());
            risk.addProperty("near_monster",entities.near(point(),8,true));risk.addProperty("coverage","server_tracked_partial");risk.addProperty("tracked_count",entities.count());value.add("risk",risk);
            value.addProperty("enabled",self.available()&&!off&&active);
            if(advanced){
                JsonObject locomotion=new JsonObject();boolean available=self.available()&&!off;locomotion.addProperty("maintenance",available&&movement.needsMaintenance());
                MovementController.FluidContact fluid=available&&pose()!=null?terrain.fluid(MovementController.body(pose())):null;
                locomotion.addProperty("medium",fluid==null||!fluid.known()?"unknown":fluid.depth()>0?"water":terrain.safe(pose().x(),pose().y(),pose().z())?"ground":"air");
                locomotion.add("air_supply",airSupply==null?JsonNull.INSTANCE:new com.google.gson.JsonPrimitive(airSupply));value.add("locomotion",locomotion);
                JsonObject fight=new JsonObject();CombatController.Snapshot state=combat.snapshot();fight.addProperty("status",state.status().name().toLowerCase(Locale.ROOT));fight.addProperty("code",state.code());
                fight.add("target_id",targetId<0?JsonNull.INSTANCE:new com.google.gson.JsonPrimitive(targetId));fight.add("target_epoch",targetId<0?JsonNull.INSTANCE:new com.google.gson.JsonPrimitive(targetEpoch));
                fight.addProperty("attacks_sent",state.attacks());fight.addProperty("hand",combatHand());fight.addProperty("evidence",state.evidenceLevel());value.add("combat",fight);
                JsonArray targets=new JsonArray();if(available)entities.targets().entrySet().stream().sorted(Map.Entry.comparingByKey()).filter(e->catalog.lookupEntityType(e.getValue().type()).monster()).limit(32).forEach(e->{
                    EntityTracker.Entity entity=e.getValue();BlockCatalog.EntityType type=catalog.lookupEntityType(entity.type());JsonObject item=new JsonObject();item.addProperty("entity_id",e.getKey());item.addProperty("spawn_epoch",entity.epoch());item.addProperty("type",type.name());
                    item.addProperty("x",entity.point().x());item.addProperty("y",entity.point().y());item.addProperty("z",entity.point().z());item.addProperty("width",type.width());item.addProperty("height",type.height());
                    item.add("health",entity.health()==null?JsonNull.INSTANCE:new com.google.gson.JsonPrimitive(entity.health()));item.addProperty("geometry_known",entity.geometryKnown());item.addProperty("dead",entity.dead());targets.add(item);
                });value.add("targets",targets);
            }
            if(playerInventory!=null)value.add("inventory",playerInventory.snapshot(inventoryPage));
            return value;
        }
    }
    private final MovementController.World legacyWorld=new MovementController.World() {
        public boolean safe(double x,double y,double z) {
            if(Math.abs(y-Math.rint(y))>1e-6||entities.near(new EntityTracker.Point(x,y,z),2,false))return false;
            double half=(double).6f/2;int minX=(int)Math.floor(x-half),maxX=(int)Math.floor(Math.nextDown(x+half));
            int minZ=(int)Math.floor(z-half),maxZ=(int)Math.floor(Math.nextDown(z+half)),feet=(int)Math.rint(y);
            List<ChunkWorld.BlockPos> points=new ArrayList<>();
            for(int bx=minX;bx<=maxX;bx++)for(int bz=minZ;bz<=maxZ;bz++)for(int by=feet-1;by<feet+2;by++)points.add(new ChunkWorld.BlockPos(bx,by,bz));
            for(ChunkWorld.Block b:connection.world.sample(points).blocks()) {
                BlockCatalog.Kind kind=b.stateId()==null?BlockCatalog.Kind.UNKNOWN:catalog.lookup(b.stateId()).kind();
                if(kind!=(b.position().y()<feet?BlockCatalog.Kind.SAFE_SUPPORT:BlockCatalog.Kind.AIR))return false;
            }
            return true;
        }
        public double friction(double x,double y,double z) {
            ChunkWorld.Block b=connection.world.sample(List.of(new ChunkWorld.BlockPos((int)Math.floor(x),(int)Math.floor(y)-1,(int)Math.floor(z)))).blocks().getFirst();
            return b.stateId()==null?Double.NaN:catalog.lookup(b.stateId()).friction();
        }
    };
    private IOException failure(String code) { failResult(code);connection.close();return new VanillaConnection.Failure(code); }
    private void holdRisk() { hasRiskHold=true;riskUntil=System.nanoTime()+1_000_000_000L; }
    private boolean recentRisk(long now) { return hasRiskHold&&now-riskUntil<0; }
}
