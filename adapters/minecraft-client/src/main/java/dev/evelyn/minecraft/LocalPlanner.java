package dev.evelyn.minecraft;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** One bounded navigate goal. No model calls, threads, network, attacks or movement avoidance. */
public final class LocalPlanner {
    public enum Status { IDLE, NAVIGATING, SETTLING, PAUSED, SUCCEEDED, CANCELLED, FAILED, OFF }
    public record Snapshot(Status status,String code,long goalSeq,long stepId,boolean paused,int preemptions,int expandedNodes) {}
    private record Point(double x,double y,double z) {}
    private record Cell(int x,int z,Cell parent) {}
    private record Coordinate(int x,int halfY,int z) {}
    private record HeightCell(Coordinate position,HeightCell parent) {}
    private enum Phase { NONE, LOOK, WALK }
    private static final long MAX_DURATION=30_000_000_000L, SAFE_HOLD=1_000_000_000L;
    private static final int MAX_NODES=256, MAX_PATH=32;
    private final MovementController movement;
    private Status status=Status.IDLE;
    private String code="idle";
    private long goalSeq,stepId,deadline,lastNow,lastDamageEpoch,safeSince;
    private boolean hasClock,hasDamageEpoch,hasSafeSince;
    private double targetX,targetY,targetZ;
    private int preemptions,expandedNodes,index;
    private List<Point> path=List.of();
    private Phase phase=Phase.NONE;

    public LocalPlanner(MovementController movement){if(movement==null)throw new IllegalArgumentException("missing_movement");this.movement=movement;}
    public Snapshot snapshot(){return new Snapshot(status,code,goalSeq,stepId,status==Status.SETTLING||status==Status.PAUSED,preemptions,expandedNodes);}
    public void beginNavigate(long seq,double x,double y,double z,long absoluteDeadline,long now){
        checkNow(now);
        if(status==Status.OFF||active()||!movement.ready())
            throw new IllegalStateException("planner_not_idle");
        MovementController.Pose pose=movement.snapshot().pose();
        if(seq<1||seq<=goalSeq||seq<=movement.snapshot().commandSeq()||pose==null||!Double.isFinite(x)||!Double.isFinite(y)||!Double.isFinite(z)
                ||Math.abs(x)>30_000_000||Math.abs(z)>30_000_000
                ||(movement.extended()?(Math.abs(y)>2046||Math.abs(y-pose.y())>16||Math.abs(y*2-Math.rint(y*2))>1e-6):Math.abs(y-pose.y())>1e-6)
                ||Math.hypot(x-pose.x(),z-pose.z())>16||absoluteDeadline-now<=0)
            throw new IllegalArgumentException("invalid_navigate_target");
        goalSeq=seq;stepId=0;targetX=x;targetY=y;targetZ=z;
        deadline=now+Math.min(absoluteDeadline-now,MAX_DURATION);preemptions=0;expandedNodes=0;
        path=List.of();index=0;phase=Phase.NONE;hasSafeSince=false;hasDamageEpoch=false;
        status=Status.NAVIGATING;code="planning";
    }
    public MovementController.PreparedStep tick(long now,MovementController.World world,boolean hazard,long damageEpoch){
        checkNow(now);
        if(damageEpoch<0||(hasDamageEpoch&&damageEpoch<lastDamageEpoch)){finish(Status.FAILED,"invalid_damage_epoch",now);return null;}
        boolean newDamage=hasDamageEpoch&&damageEpoch>lastDamageEpoch;
        hasDamageEpoch=true;lastDamageEpoch=damageEpoch;
        if(!active())return status==Status.CANCELLED||status==Status.FAILED&&movement.snapshot().status()==MovementController.Status.SETTLING?movement.prepareTick(now,world):null;
        if(now-deadline>=0){finish(Status.FAILED,"goal_timeout",now);return null;}
        if(hazard||newDamage){
            hasSafeSince=false;
            if(status==Status.NAVIGATING){
                if(++preemptions>3){finish(Status.FAILED,"preemption_limit",now);return null;}
                movement.cancel(goalSeq,stepId,now);path=List.of();phase=Phase.NONE;
                status=Status.SETTLING;code="hazard_stop_avoidance_unsupported";
            }
        }else if(status==Status.SETTLING||status==Status.PAUSED){
            if(!hasSafeSince){safeSince=now;hasSafeSince=true;}
        }
        if(status==Status.SETTLING||status==Status.PAUSED){
            if(movement.snapshot().status()==MovementController.Status.SETTLING)return movement.prepareTick(now,world);
            if(movement.snapshot().status()==MovementController.Status.FAILED){finish(Status.FAILED,movement.snapshot().code(),now);return null;}
            status=Status.PAUSED;
            if(hazard||newDamage||!hasSafeSince||now-safeSince<SAFE_HOLD)return movement.needsMaintenance()?movement.prepareMaintenance(now,world):null;
            status=Status.NAVIGATING;code="replanning";path=List.of();phase=Phase.NONE;
        }
        MovementController.Snapshot motion=movement.snapshot();
        if(phase!=Phase.NONE){
            if(motion.status()==MovementController.Status.FAILED||motion.status()==MovementController.Status.CANCELLED){finish(Status.FAILED,motion.code(),now);return null;}
            if(motion.status()==MovementController.Status.SUCCEEDED){
                if(phase==Phase.LOOK){
                    Point target=path.get(index);phase=Phase.WALK;
                    movement.beginWalk(goalSeq,nextStep(),target.x,target.y,target.z,deadline,now);
                }else{index++;phase=Phase.NONE;}
            }
        }
        if(phase==Phase.NONE){
            if(path.isEmpty()){
                if(!plan(world)){finish(Status.FAILED,"no_safe_bounded_path",now);return null;}
                index=0;
            }
            if(index==path.size()){status=Status.SUCCEEDED;code="goal_reached_client_simulated";return null;}
            Point point=path.get(index);MovementController.Pose pose=movement.snapshot().pose();
            float yaw=(float)Math.toDegrees(Math.atan2(-(point.x-pose.x()),point.z-pose.z()));
            phase=Phase.LOOK;movement.beginLook(goalSeq,nextStep(),yaw,0,deadline,now);
        }
        MovementController.PreparedStep result=movement.prepareTick(now,world);
        if(movement.snapshot().status()==MovementController.Status.FAILED)finish(Status.FAILED,movement.snapshot().code(),now);
        return result;
    }
    public boolean valid(MovementController.PreparedStep step,long now){return status!=Status.OFF&&movement.valid(step,now);}
    public boolean commit(MovementController.PreparedStep step,long now){checkNow(now);return status!=Status.OFF&&movement.commit(step,now);}
    public boolean needsMaintenance(){return status!=Status.OFF&&movement.needsMaintenance();}
    public MovementController.PreparedStep prepareMaintenance(long now,MovementController.World world){checkNow(now);return status==Status.OFF?null:movement.prepareMaintenance(now,world);}
    public boolean cancel(long seq,long now){
        checkNow(now);if(seq!=goalSeq)return false;
        if(status==Status.CANCELLED)return true;if(!active())return false;
        finish(Status.CANCELLED,"cancelled",now);return true;
    }
    public void off(long now){checkNow(now);movement.invalidate("off",now);status=Status.OFF;code="off";path=List.of();phase=Phase.NONE;}
    public void correction(MovementController.Pose pose,MovementController.Velocity velocity,long now){
        checkNow(now);movement.correction(pose,velocity,now);
        if(active()){status=Status.FAILED;code="server_correction";path=List.of();phase=Phase.NONE;}
    }
    public void externalVelocity(MovementController.Velocity velocity,long now){
        checkNow(now);if(status==Status.OFF)throw new IllegalStateException("planner_off");
        movement.externalVelocity(velocity,now);status=Status.FAILED;code="knockback_preempted";path=List.of();phase=Phase.NONE;
    }
    private boolean active(){return status==Status.NAVIGATING||status==Status.SETTLING||status==Status.PAUSED;}
    private void finish(Status result,String reason,long now){movement.cancel(goalSeq,stepId,now);status=result;code=reason;path=List.of();phase=Phase.NONE;}
    private long nextStep(){if(stepId==Long.MAX_VALUE)throw new IllegalStateException("step_exhausted");return ++stepId;}
    private void checkNow(long now){if(hasClock&&now-lastNow<0)throw new IllegalArgumentException("clock_regression");lastNow=now;hasClock=true;}

    private boolean plan(MovementController.World world){
        if(movement.extended())return planExtended(world);
        expandedNodes=0;MovementController.Pose pose=movement.snapshot().pose();
        if(world==null||pose==null||!world.safe(pose.x(),targetY,pose.z())||!world.safe(targetX,targetY,targetZ))return false;
        int sx=(int)Math.floor(pose.x()),sz=(int)Math.floor(pose.z()),tx=(int)Math.floor(targetX),tz=(int)Math.floor(targetZ);
        ArrayDeque<Cell> frontier=new ArrayDeque<>();Map<Long,Cell> visited=new HashMap<>();
        Cell start=new Cell(sx,sz,null),found=null;frontier.add(start);visited.put(key(sx,sz),start);
        int[][] directions={{1,0},{0,1},{-1,0},{0,-1}};
        while(!frontier.isEmpty()){
            Cell current=frontier.removeFirst();expandedNodes++;
            if(current.x==tx&&current.z==tz){found=current;break;}
            for(int[]direction:directions){
                int x=current.x+direction[0],z=current.z+direction[1];long key=key(x,z);
                if(visited.containsKey(key)||Math.abs(x-sx)>16||Math.abs(z-sz)>16||!world.safe(x+.5,targetY,z+.5))continue;
                if(visited.size()==MAX_NODES)return false;
                Cell next=new Cell(x,z,current);visited.put(key,next);frontier.addLast(next);
            }
        }
        if(found==null)return false;
        ArrayList<Point> reverse=new ArrayList<>();
        for(Cell cell=found;cell.parent!=null;cell=cell.parent){if(reverse.size()==MAX_PATH)return false;reverse.add(new Point(cell.x+.5,targetY,cell.z+.5));}
        ArrayList<Point> route=new ArrayList<>();for(int i=reverse.size()-1;i>=0;i--)route.add(reverse.get(i));
        if(route.isEmpty()||Math.hypot(route.getLast().x-targetX,route.getLast().z-targetZ)>1e-9)route.add(new Point(targetX,targetY,targetZ));
        if(route.size()>MAX_PATH)return false;path=List.copyOf(route);return true;
    }
    private boolean planExtended(MovementController.World world){
        expandedNodes=0;MovementController.Pose pose=movement.snapshot().pose();
        if(world==null||pose==null||!MovementController.traversable(world,pose.x(),pose.y(),pose.z())||!MovementController.traversable(world,targetX,targetY,targetZ))return false;
        Coordinate startPosition=new Coordinate((int)Math.floor(pose.x()),(int)Math.rint(pose.y()*2),(int)Math.floor(pose.z()));
        Coordinate end=new Coordinate((int)Math.floor(targetX),(int)Math.rint(targetY*2),(int)Math.floor(targetZ));
        HeightCell start=new HeightCell(startPosition,null),found=null;
        ArrayDeque<HeightCell> frontier=new ArrayDeque<>();Map<Coordinate,HeightCell> visited=new HashMap<>();frontier.add(start);visited.put(startPosition,start);
        int[][] directions={{1,0},{0,1},{-1,0},{0,-1}};int[] heights={0,1,-1,2,-2};
        while(!frontier.isEmpty()){
            HeightCell current=frontier.removeFirst();expandedNodes++;Coordinate p=current.position;
            if(p.equals(end)){found=current;break;}
            ArrayList<Coordinate> neighbors=new ArrayList<>(22);
            for(int[] direction:directions)for(int dy:heights)neighbors.add(new Coordinate(p.x+direction[0],p.halfY+dy,p.z+direction[1]));
            // A same-column height change is swimming, never a dry vertical teleport.
            if(inWater(world,p)){neighbors.add(new Coordinate(p.x,p.halfY+1,p.z));neighbors.add(new Coordinate(p.x,p.halfY-1,p.z));}
            for(Coordinate candidate:neighbors){
                if(visited.containsKey(candidate)||Math.abs(candidate.x-startPosition.x)>16||Math.abs(candidate.z-startPosition.z)>16||Math.abs(candidate.halfY-startPosition.halfY)>32
                        ||!MovementController.traversable(world,candidate.x+.5,candidate.halfY*.5,candidate.z+.5))continue;
                if(candidate.x==p.x&&candidate.z==p.z&&!inWater(world,candidate))continue;
                if(visited.size()==MAX_NODES)return false;
                HeightCell next=new HeightCell(candidate,current);visited.put(candidate,next);frontier.addLast(next);
            }
        }
        if(found==null)return false;ArrayList<Point> reverse=new ArrayList<>();
        for(HeightCell cell=found;cell.parent!=null;cell=cell.parent){if(reverse.size()==MAX_PATH)return false;Coordinate p=cell.position;reverse.add(new Point(p.x+.5,p.halfY*.5,p.z+.5));}
        ArrayList<Point> route=new ArrayList<>();for(int i=reverse.size()-1;i>=0;i--)route.add(reverse.get(i));
        if(route.isEmpty()||Math.hypot(route.getLast().x-targetX,route.getLast().z-targetZ)>1e-9||Math.abs(route.getLast().y-targetY)>1e-9)route.add(new Point(targetX,targetY,targetZ));
        if(route.size()>MAX_PATH)return false;path=List.copyOf(route);return true;
    }
    private static boolean inWater(MovementController.World world,Coordinate p){
        try{var fluid=world.fluid(MovementController.body(new MovementController.Pose(p.x+.5,p.halfY*.5,p.z+.5,0,0)));return fluid!=null&&fluid.known()&&fluid.depth()>0;}
        catch(IllegalArgumentException invalid){return false;}
    }
    private static long key(int x,int z){return((long)x<<32)|(z&0xffffffffL);}
}
