package dev.evelyn.minecraft;

import java.util.List;
import java.util.TreeSet;

/** Pure, single-owner simulation. Extended terrain/fluids are opt-in; the host owns commit and authority fences. */
public final class MovementController {
    public static final long TICK_NANOS = 50_000_000L;
    public static final double TARGET_TOLERANCE = .15;
    private static final long MAX_LATENESS = 150_000_000L;
    private static final double CUTOFF_SQUARED = 9e-6;
    private static final float SPEED = .1f, INPUT = .98f;
    public enum Status { IDLE, RUNNING, SETTLING, SUCCEEDED, CANCELLED, FAILED }
    public record Pose(double x, double y, double z, float yaw, float pitch) {
        public Pose {
            if (!Double.isFinite(x) || !Double.isFinite(y) || !Double.isFinite(z)
                    || Math.abs(x)>30_000_000 || Math.abs(z)>30_000_000 || Math.abs(y)>2048
                    || !Float.isFinite(yaw) || !Float.isFinite(pitch) || pitch < -90 || pitch > 90)
                throw new IllegalArgumentException("invalid_pose");
        }
    }
    public record Velocity(double x, double y, double z) {
        public Velocity { if (!Double.isFinite(x)||!Double.isFinite(y)||!Double.isFinite(z)) throw new IllegalArgumentException("invalid_velocity"); }
    }
    public record Box(double minX,double minY,double minZ,double maxX,double maxY,double maxZ) {
        public Box {if(!Double.isFinite(minX)||!Double.isFinite(minY)||!Double.isFinite(minZ)||!Double.isFinite(maxX)||!Double.isFinite(maxY)||!Double.isFinite(maxZ)
                ||minX>=maxX||minY>=maxY||minZ>=maxZ||minX< -30_000_000||maxX>30_000_000||minZ< -30_000_000||maxZ>30_000_000||minY< -2048||maxY>2048)throw new IllegalArgumentException("invalid_box");}
        Box move(double x,double y,double z){return new Box(minX+x,minY+y,minZ+z,maxX+x,maxY+y,maxZ+z);}
        Box expand(double x,double y,double z){return new Box(minX+Math.min(0,x),minY+Math.min(0,y),minZ+Math.min(0,z),maxX+Math.max(0,x),maxY+Math.max(0,y),maxZ+Math.max(0,z));}
    }
    public record Region(boolean known,List<Box> collisionBoxes) {
        public Region {collisionBoxes=List.copyOf(collisionBoxes);if(collisionBoxes.size()>4096)throw new IllegalArgumentException("geometry_budget");}
    }
    public record FluidContact(boolean known,double depth,boolean eyeSubmerged,double flowX,double flowY,double flowZ) {
        public FluidContact {if(!Double.isFinite(depth)||depth<0||depth>4||!Double.isFinite(flowX)||!Double.isFinite(flowY)||!Double.isFinite(flowZ)
                ||Math.abs(flowX)>1||Math.abs(flowY)>1||Math.abs(flowZ)>1||depth==0&&(eyeSubmerged||flowX!=0||flowY!=0||flowZ!=0))throw new IllegalArgumentException("invalid_fluid_contact");}
    }
    /** safe must check the entire standing body and full supporting footprint; unknown is false. */
    public interface World {
        boolean safe(double x, double y, double z);
        double friction(double x, double y, double z);
        default Region sample(Box envelope){return new Region(false,List.of());}
        /** Raw depth-weighted mean after the world's official sum-vector cutoff, before .014/minimum push. */
        default FluidContact fluid(Box body){return new FluidContact(false,0,false,0,0,0);}
    }
    public record Snapshot(Pose pose, Velocity velocity, long epoch, long commandSeq, long stepId,
                           Status status, String code, String evidenceLevel) {}
    /** safetyStop uses commandSeq/stepId zero: it is neutral cleanup, not the cancelled action's authority. */
    public record PreparedStep(long token, long epoch, long commandSeq, long stepId, long preparedAtNanos,
                               Pose pose, Velocity velocity, boolean onGround, boolean horizontalCollision,
                               boolean forward, boolean safetyStop,boolean jump,boolean sneak) {
        public PreparedStep(long token,long epoch,long commandSeq,long stepId,long preparedAtNanos,Pose pose,Velocity velocity,
                boolean onGround,boolean horizontalCollision,boolean forward,boolean safetyStop){this(token,epoch,commandSeq,stepId,preparedAtNanos,pose,velocity,onGround,horizontalCollision,forward,safetyStop,false,false);}
    }
    private enum Intent { NONE, WALK, LOOK, JUMP }
    private final long origin;
    private final boolean extended;
    private long lastNow, nextTick, epoch=1, token, commandSeq, stepId, deadline;
    private Pose pose;
    private Velocity velocity=new Velocity(0,0,0);
    private Status status=Status.IDLE;
    private String code="uninitialized";
    private Intent intent=Intent.NONE;
    private double targetX,targetY,targetZ;
    private float targetYaw,targetPitch;
    private boolean initialized,lastForward;
    private int stableTicks,pendingStable;
    private PreparedStep pending;
    private Status pendingStatus;
    private String pendingCode;
    private boolean grounded=true,lastWater,jumpIssued,pendingJumpIssued,pendingWater,pendingGrounded;
    private static final double EPS=1e-7,HALF=(double).6f/2,HEIGHT=(double)1.8f;

    public MovementController(long originNanos) { this(originNanos,false); }
    public MovementController(long originNanos,boolean extended) { origin=originNanos;lastNow=originNanos;nextTick=originNanos+TICK_NANOS;this.extended=extended; }
    public boolean extended(){return extended;}
    public Snapshot snapshot() { return new Snapshot(pose,velocity,epoch,commandSeq,stepId,status,code,status==Status.SUCCEEDED?"client_simulated":"none"); }
    public boolean ready() { return initialized&&pending==null&&status!=Status.RUNNING&&status!=Status.SETTLING&&(extended&&lastWater||!moving()&&!lastForward); }

    public void correction(Pose corrected, Velocity receivedVelocity, long now) {
        checkNow(now);
        if(corrected==null||receivedVelocity==null)throw new IllegalArgumentException("invalid_correction");
        boolean interrupted=status==Status.RUNNING||status==Status.SETTLING;
        fence();pose=corrected;velocity=receivedVelocity;initialized=true;lastForward=false;
        status=interrupted?Status.FAILED:Status.IDLE;code=interrupted?"server_correction":"ready";
        nextTick=now+TICK_NANOS;
        if(extended){grounded=receivedVelocity.y()==0;lastWater=false;targetX=pose.x;targetY=pose.y;targetZ=pose.z;jumpIssued=false;}
    }
    public void beginWalk(long seq,long step,double x,double y,double z,long absoluteDeadline,long now) {
        checkNow(now);
        if(pose==null||!Double.isFinite(x)||!Double.isFinite(y)||!Double.isFinite(z)
                || Math.abs(x)>30_000_000 || Math.abs(z)>30_000_000
                || (extended?(Math.abs(y-pose.y)>4||Math.abs(y*2-Math.rint(y*2))>1e-6):Math.abs(y-pose.y)>1e-6) || Math.hypot(x-pose.x,z-pose.z)>4)
            throw new IllegalArgumentException("invalid_walk_target");
        admit(seq,step,absoluteDeadline,now);targetX=x;targetY=y;targetZ=z;intent=Intent.WALK;
    }
    public void beginLook(long seq,long step,float yaw,float pitch,long absoluteDeadline,long now) {
        checkNow(now);
        if(!Float.isFinite(yaw)||!Float.isFinite(pitch)||pitch< -90||pitch>90)throw new IllegalArgumentException("invalid_look");
        admit(seq,step,absoluteDeadline,now);targetYaw=wrapYaw(yaw);targetPitch=pitch;intent=Intent.LOOK;
        if(extended){targetX=pose.x;targetY=pose.y;targetZ=pose.z;}
    }
    public void beginJump(long seq,long step,long absoluteDeadline,long now){checkNow(now);if(!extended)throw new IllegalStateException("extended_disabled");admit(seq,step,absoluteDeadline,now);targetX=pose.x;targetY=pose.y;targetZ=pose.z;intent=Intent.JUMP;}
    /** After an owned stationary aim packet commits, retain all physics and action identity. */
    public void face(float yaw,float pitch,long now){
        checkNow(now);if(!extended||!ready()||!grounded||lastWater)throw new IllegalStateException("stationary_aim_required");
        if(!Float.isFinite(yaw)||!Float.isFinite(pitch)||pitch< -90||pitch>90)throw new IllegalArgumentException("invalid_look");
        fence();pose=new Pose(pose.x,pose.y,pose.z,wrapYaw(yaw),pitch);
    }
    public void externalVelocity(Velocity received,long now){checkNow(now);if(!extended||!initialized||received==null||Math.max(Math.max(Math.abs(received.x),Math.abs(received.y)),Math.abs(received.z))>4)throw new IllegalArgumentException("unsupported_velocity");
        fence();velocity=received;status=Status.SETTLING;code="external_velocity";grounded=false;targetX=pose.x;targetY=pose.y;targetZ=pose.z;nextTick=now+TICK_NANOS;}
    private void admit(long seq,long step,long absoluteDeadline,long now) {
        if(!ready())
            throw new IllegalStateException("movement_not_settled");
        if(seq<1||step<1||seq<commandSeq||(seq==commandSeq&&step<=stepId)||absoluteDeadline-now<=0)
            throw new IllegalArgumentException("invalid_action_binding");
        fence();commandSeq=seq;stepId=step;deadline=absoluteDeadline;status=Status.RUNNING;code="running";
        jumpIssued=false;
        if(!extended||!lastWater)nextTick=now+TICK_NANOS;
    }
    public boolean cancel(long seq,long step,long now) {
        checkNow(now);
        if(seq!=commandSeq||step!=stepId)return false;
        if(status==Status.SETTLING||status==Status.CANCELLED)return true;
        if(status!=Status.RUNNING)return false;
        fence();intent=Intent.NONE;code="cancelled";status=moving()||lastForward||extended&&lastWater?Status.SETTLING:Status.CANCELLED;
        if(extended){targetX=pose.x;targetY=pose.y;targetZ=pose.z;}
        if(!extended)nextTick=now+TICK_NANOS;return true;
    }
    public void invalidate(String reason,long now) {
        checkNow(now);fail(reason==null?"invalid_binding":reason);
    }

    public PreparedStep prepareTick(long now,World world) {
        checkNow(now);
        if(!initialized||(status!=Status.RUNNING&&status!=Status.SETTLING))return null;
        if((status==Status.RUNNING||extended)&&now-deadline>=0){fail("action_timeout");return null;}
        if(pending!=null){if(now-pending.preparedAtNanos>TICK_NANOS)fail("prepared_step_expired");return null;}
        if(now-nextTick<0)return null;
        if(now-nextTick>MAX_LATENESS){fail("tick_late");return null;}
        if(extended)return prepareExtended(now,world,false);
        if(world==null||!world.safe(pose.x,pose.y,pose.z)){fail("unsafe_support");return null;}
        double rawFriction=world.friction(pose.x,pose.y,pose.z);
        if(!Double.isFinite(rawFriction)||rawFriction<0||rawFriction>1||velocity.y>0){fail("unsupported_physics");return null;}
        float friction=(float)rawFriction,drag=friction*.91f;
        float acceleration=(double)friction>.6?SPEED*(.21600002f/(friction*friction*friction)):SPEED;
        double vx=velocity.x,vz=velocity.z;
        if(vx*vx+vz*vz<CUTOFF_SQUARED){vx=0;vz=0;}
        float yaw=pose.yaw,pitch=pose.pitch;
        boolean forward=false,safety=status==Status.SETTLING;
        if(intent==Intent.LOOK){yaw=targetYaw;pitch=targetPitch;}
        if(intent==Intent.WALK){
            double dx=targetX-pose.x,dz=targetZ-pose.z;
            if(Math.hypot(dx,dz)>TARGET_TOLERANCE){
                yaw=wrapYaw((float)Math.toDegrees(Math.atan2(-dx,dz)));
                double radians=(double)(yaw*.017453292f);
                double ax=-(double)INPUT*acceleration*tableSin(radians,false);
                double az=(double)INPUT*acceleration*tableSin(radians,true);
                double neutral=stopDistanceSquared(vx,vz,drag),accelerated=stopDistanceSquared(vx+ax,vz+az,drag);
                if(accelerated<neutral){vx+=ax;vz+=az;forward=true;}
            }
        }
        // Four overlapping .6-wide standing footprints cover the entire conservative swept rectangle.
        double x=pose.x+vx,z=pose.z+vz;
        if(Math.abs(vx)>=.6||Math.abs(vz)>=.6||!world.safe(x,pose.y,pose.z)
                ||!world.safe(pose.x,pose.y,z)||!world.safe(x,pose.y,z)){fail("blocked_or_unknown");return null;}
        Pose proposed=new Pose(x,pose.y,z,yaw,pitch);
        Velocity after=new Velocity(vx*drag,-.08*.98f,vz*drag);
        boolean still=after.x*after.x+after.z*after.z<CUTOFF_SQUARED;
        boolean goal=intent==Intent.LOOK||(intent==Intent.WALK&&Math.hypot(targetX-x,targetZ-z)<=TARGET_TOLERANCE);
        pendingStable=(!forward&&still&&(safety||goal))?stableTicks+1:0;
        pendingStatus=status;pendingCode=code;
        if(pendingStable>=3){
            after=new Velocity(0,after.y,0);
            pendingStatus=safety?Status.CANCELLED:Status.SUCCEEDED;
            pendingCode=safety?"cancelled":"goal_reached";
        }
        if(token==Long.MAX_VALUE){fail("token_exhausted");return null;}
        pending=new PreparedStep(++token,epoch,safety?0:commandSeq,safety?0:stepId,now,proposed,after,true,false,forward,safety);
        return pending;
    }
    public boolean needsMaintenance(){return extended&&initialized&&status!=Status.RUNNING&&status!=Status.SETTLING&&(lastWater||!grounded||moving());}
    /** The host must retain the original session/world/lease/deadline authority for these safety frames. */
    public PreparedStep prepareMaintenance(long now,World world){checkNow(now);if(!needsMaintenance())return null;
        if(now-deadline>=0){fail("maintenance_expired");return null;}if(pending!=null){if(now-pending.preparedAtNanos>TICK_NANOS)fail("prepared_step_expired");return null;}
        if(now-nextTick<0)return null;if(now-nextTick>MAX_LATENESS){fail("tick_late");return null;}return prepareExtended(now,world,true);}
    private PreparedStep prepareExtended(long now,World world,boolean maintenance){
        try{return extendedStep(now,world,maintenance);}catch(IllegalArgumentException|NullPointerException invalid){fail("invalid_physics_region");return null;}
    }
    private PreparedStep extendedStep(long now,World world,boolean maintenance){
        if(world==null){fail("unknown_geometry");return null;}
        Box body=body(pose);FluidContact fluid=world.fluid(body);if(fluid==null||!fluid.known){fail("unknown_fluid");return null;}
        boolean water=fluid.depth>0,safety=maintenance||status==Status.SETTLING;
        double vx=velocity.x,vy=velocity.y,vz=velocity.z;
        if(Math.max(Math.max(Math.abs(vx),Math.abs(vy)),Math.abs(vz))>4){fail("unsupported_velocity");return null;}
        if(water){double length2=fluid.flowX*fluid.flowX+fluid.flowY*fluid.flowY+fluid.flowZ*fluid.flowZ;
            if(length2>0){double px=fluid.flowX*.014,py=fluid.flowY*.014,pz=fluid.flowZ*.014,len=Math.sqrt(px*px+py*py+pz*pz);
                if(Math.abs(vx)<.003&&Math.abs(vz)<.003&&len<.0045000000000000005){
                    if(len<9.999999747378752e-6){px=0;py=0;pz=0;}
                    else{double factor=.0045000000000000005/len;px*=factor;py*=factor;pz*=factor;}}
                vx+=px;vy+=py;vz+=pz;}}
        if(vx*vx+vz*vz<CUTOFF_SQUARED){vx=0;vz=0;}if(Math.abs(vy)<.003)vy=0;
        Region initial=world.sample(body.expand(0,-.001,0));if(initial==null||!initial.known||intersects(body,initial.collisionBoxes)){fail("blocked_or_unknown");return null;}
        boolean onFloor=supported(body,initial.collisionBoxes),ground=grounded&&onFloor;
        double rawFriction=ground?world.friction(pose.x,pose.y,pose.z):1;
        if(!Double.isFinite(rawFriction)||rawFriction<0||rawFriction>1){fail("unsupported_friction");return null;}
        float friction=(float)rawFriction,drag=water?.8f:friction*.91f;
        float acceleration=water||!ground?.02f:(double)friction>.6?SPEED*(.21600002f/(friction*friction*friction)):SPEED;
        float yaw=pose.yaw,pitch=pose.pitch;boolean forward=false,jump=false,sneak=false;
        if(intent==Intent.LOOK&&!safety){yaw=targetYaw;pitch=targetPitch;}
        boolean translate=intent==Intent.WALK&&!safety||water&&safety;
        double dx=targetX-pose.x,dz=targetZ-pose.z;
        if(translate&&Math.hypot(dx,dz)>.03){
            float moveYaw=wrapYaw((float)Math.toDegrees(Math.atan2(-dx,dz)));double radians=(double)(moveYaw*.017453292f);
            double ax=-(double)INPUT*acceleration*tableSin(radians,false),az=(double)INPUT*acceleration*tableSin(radians,true);
            if(stopDistanceSquared(vx+ax,vz+az,drag)<stopDistanceSquared(vx,vz,drag)){vx+=ax;vz+=az;forward=true;yaw=moveYaw;}}
        boolean wantJump=!safety&&!jumpIssued&&intent==Intent.JUMP;
        if(!safety&&!jumpIssued&&intent==Intent.WALK&&targetY-pose.y>.6&&!water&&ground){
            // A high destination can be reached by automatic half steps. Try the next
            // swept step first; only a blocking face that cannot step up needs a jump.
            Region approach=world.sample(body.expand(vx,vy,vz).expand(0,.6f,0).expand(0,-.001,0));
            if(approach==null||!approach.known){fail("unknown_geometry");return null;}
            Delta step=collide(body,approach.collisionBoxes,vx,vy,vz,ground);
            wantJump=(Math.abs(step.x-vx)>EPS||Math.abs(step.z-vz)>EPS)&&step.y<=Math.max(0,vy)+EPS;
        }
        if(wantJump&&!water&&ground){
            Region head=world.sample(body.expand(0,1.3,0));if(head==null||!head.known||clip(body,head.collisionBoxes,1,1.3)<1.25){fail("jump_headroom");return null;}
            vy=Math.max(vy,(double).42f);jump=true;
        }else if(intent==Intent.JUMP&&!jumpIssued&&!safety){fail("jump_requires_dry_support");return null;}
        if(water){
            // Bang-bang buoyancy uses vanilla jump/sneak impulses; terminal arrival is not a zero-velocity claim.
            double coast=pose.y+vy/(1-.8f)-.005*4;
            if(targetY-coast>.015){vy=ground&&fluid.depth<=.4?Math.max(vy,(double).42f):vy+(double).04f;jump=true;}
            else if(coast-targetY>.08){vy-=(double).04f;sneak=true;}
        }
        Box envelope=body.expand(vx,vy,vz).expand(0,.6f,0).expand(0,-.001,0);
        Region region=world.sample(envelope);if(region==null||!region.known){fail("unknown_geometry");return null;}
        Delta moved=collide(body,region.collisionBoxes,vx,vy,vz,ground);
        boolean cx=Math.abs(moved.x-vx)>EPS,cy=Math.abs(moved.y-vy)>EPS,cz=Math.abs(moved.z-vz)>EPS;
        boolean nextGround=cy&&vy<0||vy==0&&ground&&supported(body.move(moved.x,moved.y,moved.z),region.collisionBoxes);
        Pose next=new Pose(pose.x+moved.x,pose.y+moved.y,pose.z+moved.z,yaw,pitch);
        double nextX=cx?0:vx,nextY=cy?0:vy,nextZ=cz?0:vz;
        if(water){nextX*=.8f;nextY*=.8f;nextZ*=.8f;
            // With pinned gravity .08, the official falling adjustment's two near-zero
            // predicates are contradictory; the supported baseline always subtracts gravity/16.
            nextY-=.005;
            if(cx||cz){Box exit=body(next).move(nextX,nextY+(double).6f-next.y+pose.y,nextZ);Region free=world.sample(exit);FluidContact exitFluid=world.fluid(exit);
                if(free!=null&&free.known&&exitFluid!=null&&exitFluid.known&&exitFluid.depth==0&&!intersects(exit,free.collisionBoxes))nextY=(double).3f;}
        }else{nextX*=drag;nextY=(nextY-.08)*.98f;nextZ*=drag;}
        FluidContact after=world.fluid(body(next));if(after==null||!after.known){fail("unknown_fluid");return null;}
        boolean goal=intent==Intent.LOOK||intent==Intent.JUMP&&jumpIssued&&nextGround
                ||intent==Intent.WALK&&Math.hypot(targetX-next.x,targetZ-next.z)<=TARGET_TOLERANCE&&Math.abs(targetY-next.y)<=TARGET_TOLERANCE;
        boolean still=nextX*nextX+nextZ*nextZ<(water?.0025:CUTOFF_SQUARED)&&(nextGround||water&&Math.abs(nextY)<.04);
        boolean settled=nextGround||water&&Math.abs(targetY-next.y)<=TARGET_TOLERANCE&&Math.hypot(targetX-next.x,targetZ-next.z)<=TARGET_TOLERANCE;
        pendingStable=still&&settled&&(safety||goal)?stableTicks+1:0;pendingStatus=status;pendingCode=code;
        if(!maintenance&&pendingStable>=3){pendingStatus=safety?Status.CANCELLED:Status.SUCCEEDED;pendingCode=safety?"cancelled":"goal_reached";if(nextGround){nextX=0;nextZ=0;}}
        pendingGrounded=nextGround;pendingWater=after.depth>0;pendingJumpIssued=jumpIssued||jump&&!water;
        if(token==Long.MAX_VALUE){fail("token_exhausted");return null;}
        pending=new PreparedStep(++token,epoch,safety?0:commandSeq,safety?0:stepId,now,next,new Velocity(nextX,nextY,nextZ),nextGround,cx||cz,forward,safety,jump,sneak);return pending;
    }
    public static Box body(Pose pose){return new Box(pose.x-HALF,pose.y,pose.z-HALF,pose.x+HALF,pose.y+HEIGHT,pose.z+HALF);}
    /** Bounded planner eligibility for half-block standing surfaces or known water volumes. */
    public static boolean traversable(World world,double x,double y,double z){try{Box b=body(new Pose(x,y,z,0,0));Region r=world.sample(b.expand(0,-.001,0));FluidContact f=world.fluid(b);return r!=null&&r.known&&f!=null&&f.known&&!intersects(b,r.collisionBoxes)&&(f.depth>0||supported(b,r.collisionBoxes));}catch(IllegalArgumentException|NullPointerException e){return false;}}
    private record Delta(double x,double y,double z){}
    private static Delta collide(Box body,List<Box> boxes,double x,double y,double z,boolean grounded){
        Delta normal=axes(body,boxes,x,y,z);boolean down=normal.y!=y&&y<0;
        if((grounded||down)&&(normal.x!=x||normal.z!=z)){
            Box base=down?body.move(0,normal.y,0):body;TreeSet<Float> heights=new TreeSet<>();
            for(Box b:boxes)for(double h:new double[]{b.minY,b.maxY}){float rise=(float)(h-base.minY);if(rise>=0&&rise<=.6f&&rise!=(float)normal.y)heights.add(rise);}
            for(float rise:heights){Delta stepped=axes(base,boxes,x,rise,z);if(stepped.x*stepped.x+stepped.z*stepped.z>normal.x*normal.x+normal.z*normal.z)return new Delta(stepped.x,stepped.y+base.minY-body.minY,stepped.z);}
        }return normal;
    }
    private static Delta axes(Box body,List<Box> boxes,double x,double y,double z){double dy=clip(body,boxes,1,y);Box after=body.move(0,dy,0);double dx,dz;
        if(Math.abs(x)<Math.abs(z)){dz=clip(after,boxes,2,z);dx=clip(after.move(0,0,dz),boxes,0,x);}else{dx=clip(after,boxes,0,x);dz=clip(after.move(dx,0,0),boxes,2,z);}return new Delta(dx,dy,dz);}
    private static double clip(Box a,List<Box> boxes,int axis,double movement){if(Math.abs(movement)<EPS)return 0;double result=movement;
        for(Box b:boxes){boolean overlap=axis==0?a.maxY>b.minY+EPS&&a.minY<b.maxY-EPS&&a.maxZ>b.minZ+EPS&&a.minZ<b.maxZ-EPS
                :axis==1?a.maxX>b.minX+EPS&&a.minX<b.maxX-EPS&&a.maxZ>b.minZ+EPS&&a.minZ<b.maxZ-EPS
                :a.maxX>b.minX+EPS&&a.minX<b.maxX-EPS&&a.maxY>b.minY+EPS&&a.minY<b.maxY-EPS;
            if(!overlap)continue;double minA=axis==0?a.minX:axis==1?a.minY:a.minZ,maxA=axis==0?a.maxX:axis==1?a.maxY:a.maxZ;
            double minB=axis==0?b.minX:axis==1?b.minY:b.minZ,maxB=axis==0?b.maxX:axis==1?b.maxY:b.maxZ;
            if(result>0&&maxA<=minB+EPS)result=Math.min(result,Math.max(0,minB-maxA));else if(result<0&&minA>=maxB-EPS)result=Math.max(result,Math.min(0,maxB-minA));}
        return result;
    }
    private static boolean supported(Box b,List<Box> boxes){return clip(b,boxes,1,-.001)>-.001;}
    private static boolean intersects(Box a,List<Box> boxes){for(Box b:boxes)if(a.maxX>b.minX+EPS&&a.minX<b.maxX-EPS&&a.maxY>b.minY+EPS&&a.minY<b.maxY-EPS&&a.maxZ>b.minZ+EPS&&a.minZ<b.maxZ-EPS)return true;return false;}
    private double stopDistanceSquared(double vx,double vz,float drag) {
        double x=pose.x,z=pose.z;
        // drag <= .91: at most 128 bounded recurrence steps to the vanilla player cutoff.
        for(int i=0;i<128&&vx*vx+vz*vz>=CUTOFF_SQUARED;i++){x+=vx;z+=vz;vx*=drag;vz*=drag;}
        double dx=targetX-x,dz=targetZ-z;return dx*dx+dz*dz;
    }
    public boolean valid(PreparedStep step,long now) {
        return step!=null&&step==pending&&initialized&&step.epoch==epoch&&now-step.preparedAtNanos>=0
                &&now-step.preparedAtNanos<=TICK_NANOS&&(!extended&&status!=Status.RUNNING||now-deadline<0);
    }
    public boolean commit(PreparedStep step,long now) {
        checkNow(now);
        if(!valid(step,now)){
            if(step!=null&&step==pending)fail(status==Status.RUNNING&&now-deadline>=0?"action_timeout":"prepared_step_expired");
            return false;
        }
        pose=step.pose;velocity=step.velocity;lastForward=step.forward;stableTicks=pendingStable;
        if(extended){grounded=pendingGrounded;lastWater=pendingWater;jumpIssued=pendingJumpIssued;}
        status=pendingStatus;code=pendingCode;pending=null;nextTick=now+TICK_NANOS;
        if(status==Status.SUCCEEDED||status==Status.CANCELLED)intent=Intent.NONE;
        return true;
    }
    private boolean moving(){return velocity.x*velocity.x+velocity.z*velocity.z>=CUTOFF_SQUARED||velocity.y>0||extended&&!grounded;}
    private void fence(){if(epoch==Long.MAX_VALUE)throw new IllegalStateException("epoch_exhausted");epoch++;pending=null;stableTicks=0;intent=Intent.NONE;}
    private void fail(String reason){fence();status=Status.FAILED;code=reason;initialized=false;}
    private void checkNow(long now){if(now-origin<0||now-lastNow<0){fail("clock_regression");throw new IllegalArgumentException("clock_regression");}lastNow=now;}
    private static float wrapYaw(float yaw){float value=yaw%360;return value>=180?value-360:value< -180?value+360:value;}
    // Exact Mth lookup index and table-entry formula, calculated on demand without a second table.
    private static float tableSin(double radians,boolean cosine){long index=(long)(radians*10430.378350470453+(cosine?16384:0));return(float)Math.sin((index&65535L)/10430.378350470453);}
}
