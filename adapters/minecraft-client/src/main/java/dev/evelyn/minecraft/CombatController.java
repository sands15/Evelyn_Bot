package dev.evelyn.minecraft;

/** Pure stationary bare-hand attack offers. The owner retains all socket, lease, world and send fences. */
public final class CombatController {
    public enum Status { IDLE, RUNNING, SUCCEEDED, CANCELLED, FAILED, OFF }
    /** alive=false is permitted only for a server-observed death of this exact spawn. */
    public record Target(int entityId,long spawnEpoch,MovementController.Box box,boolean present,boolean alive) {
        public Target {if(entityId<0||spawnEpoch<1||present&&box==null)throw new IllegalArgumentException("invalid_target");}
    }
    /** safe includes known supported target geometry, stationary self state and all caller-owned hazards. */
    public record Context(long environmentEpoch,MovementController.Pose pose,boolean bareHand,boolean lineOfSight,boolean safe,Target target) {
        public Context {if(environmentEpoch<0||pose==null)throw new IllegalArgumentException("invalid_combat_context");}
    }
    public record PreparedAttack(long token,long epoch,long commandSeq,int targetId,long spawnEpoch,long environmentEpoch,long preparedAtNanos) {}
    public record Snapshot(Status status,String code,long commandSeq,int attacks,String evidenceLevel) {}
    private static final long MAX_DURATION=30_000_000_000L,COOLDOWN=500_000_000L,MAX_PREPARED_AGE=50_000_000L;
    private static final int MAX_ATTACKS=128;
    private final long origin;
    private long lastNow,commandSeq,spawnEpoch,deadline,nextAttack,epoch=1,token;
    private int targetId,attacks;
    private Status status=Status.IDLE;
    private String code="idle";
    private PreparedAttack pending;
    private Context pendingContext;

    public CombatController(long originNanos){origin=originNanos;lastNow=originNanos;nextAttack=originNanos;}
    public Snapshot snapshot(){return new Snapshot(status,code,commandSeq,attacks,status==Status.SUCCEEDED?"server_observed":"none");}
    public void beginAttack(long seq,int entityId,long targetSpawnEpoch,long absoluteDeadline,long now){
        checkNow(now);if(status==Status.OFF||status==Status.RUNNING)throw new IllegalStateException("combat_not_idle");
        if(seq<1||seq<=commandSeq||entityId<0||targetSpawnEpoch<1||absoluteDeadline-now<=0)throw new IllegalArgumentException("invalid_attack_binding");
        fence();commandSeq=seq;targetId=entityId;spawnEpoch=targetSpawnEpoch;deadline=now+Math.min(MAX_DURATION,absoluteDeadline-now);
        attacks=0;status=Status.RUNNING;code="attacking";
    }
    public PreparedAttack prepare(long now,Context context){
        checkNow(now);if(status!=Status.RUNNING)return null;
        if(now-deadline>=0){finish(Status.FAILED,"attack_timeout");return null;}
        String rejection=rejection(context);
        if(rejection!=null){finish(Status.FAILED,rejection);return null;}
        if(!context.target.alive){finish(Status.SUCCEEDED,"target_defeated_observed");return null;}
        if(pending!=null){
            if(now-pending.preparedAtNanos>MAX_PREPARED_AGE)finish(Status.FAILED,"attack_offer_expired");
            else if(!pendingContext.equals(context))finish(Status.FAILED,"attack_preflight_changed");
            return null;
        }
        if(now-nextAttack<0)return null;
        if(attacks>=MAX_ATTACKS){finish(Status.FAILED,"attack_budget");return null;}
        if(token==Long.MAX_VALUE){finish(Status.FAILED,"attack_token_exhausted");return null;}
        pendingContext=context;pending=new PreparedAttack(++token,epoch,commandSeq,targetId,spawnEpoch,context.environmentEpoch,now);return pending;
    }
    /** Read-only recheck immediately before the owner commits a complete attack packet. */
    public boolean valid(PreparedAttack step,long now,Context context){
        return step!=null&&step==pending&&status==Status.RUNNING&&step.epoch==epoch&&now-step.preparedAtNanos>=0
                &&now-step.preparedAtNanos<=MAX_PREPARED_AGE&&now-deadline<0&&pendingContext.equals(context)
                &&rejection(context)==null&&context.target.alive;
    }
    /** Call only after the owned packet has fully committed; a count never asserts a hit. */
    public boolean commit(PreparedAttack step,long now,Context context){
        checkNow(now);if(!valid(step,now,context)){
            if(step!=null&&step==pending)finish(Status.FAILED,now-deadline>=0?"attack_timeout":"attack_preflight_changed");
            return false;
        }
        attacks++;pending=null;pendingContext=null;nextAttack=now+COOLDOWN;return true;
    }
    public boolean cancel(long seq,long now){checkNow(now);if(seq!=commandSeq)return false;if(status==Status.CANCELLED)return true;if(status!=Status.RUNNING)return false;finish(Status.CANCELLED,"cancelled");return true;}
    public void off(long now){checkNow(now);finish(Status.OFF,"off");}
    private String rejection(Context context){
        if(context==null)return "unknown_combat_context";
        Target target=context.target;if(target==null||!target.present||target.entityId!=targetId||target.spawnEpoch!=spawnEpoch)return "target_lost";
        // Death is a read-only terminal fact; it needs no further attack preconditions.
        if(!target.alive)return null;
        if(!context.bareHand)return "bare_hand_unconfirmed";
        if(!context.safe)return "unsafe_combat";
        if(!context.lineOfSight)return "line_of_sight_blocked";
        MovementController.Box box=target.box;MovementController.Pose pose=context.pose;
        double dx=distance(pose.x(),box.minX(),box.maxX()),dy=distance(pose.y()+(double)1.62f,box.minY(),box.maxY()),dz=distance(pose.z(),box.minZ(),box.maxZ());
        return dx*dx+dy*dy+dz*dz>9?"target_out_of_reach":null;
    }
    private static double distance(double value,double min,double max){return value<min?min-value:value>max?value-max:0;}
    private void finish(Status result,String reason){fence();status=result;code=reason;}
    private void fence(){if(epoch==Long.MAX_VALUE)throw new IllegalStateException("combat_epoch_exhausted");epoch++;pending=null;pendingContext=null;}
    private void checkNow(long now){if(now-origin<0||now-lastNow<0){if(status!=Status.OFF)finish(Status.FAILED,"clock_regression");throw new IllegalArgumentException("clock_regression");}lastNow=now;}
}
