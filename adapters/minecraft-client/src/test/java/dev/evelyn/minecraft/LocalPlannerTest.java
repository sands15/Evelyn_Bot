package dev.evelyn.minecraft;

public final class LocalPlannerTest {
    private static int checks;
    private static final long T=50_000_000L;
    private static final MovementController.World FLAT=new MovementController.World(){public boolean safe(double x,double y,double z){return y==64;}public double friction(double x,double y,double z){return .6f;}};
    private record Fixture(MovementController motion,LocalPlanner planner){}
    private static Fixture fixture(){var m=new MovementController(0);m.correction(new MovementController.Pose(.5,64,.5,0,0),new MovementController.Velocity(0,0,0),0);return new Fixture(m,new LocalPlanner(m));}
    public static void main(String[]args){navigate();hazardAndFreshPath();budgetsAndFences();preemptionLimit();admission();extendedRoutes();extendedPreemption();System.out.println("LOCAL_PLANNER_PASS "+checks);}
    private static Fixture extended(){var m=MovementControllerTest.extended();return new Fixture(m,new LocalPlanner(m));}
    private static long finish(Fixture f,MovementController.World world,long now,int frames){
        for(int i=0;i<frames;i++){now+=T;var s=f.planner.tick(now,world,false,0);if(s!=null)check(f.planner.commit(s,now),"extended planner commit");
            if(f.planner.snapshot().status()==LocalPlanner.Status.SUCCEEDED||f.planner.snapshot().status()==LocalPlanner.Status.FAILED)return now;}
        return now;
    }
    private static void extendedRoutes(){
        var slab=extended();var half=MovementControllerTest.terrain(java.util.List.of(MovementControllerTest.floor(),new MovementController.Box(1,64,0,2,64.5,1)));
        slab.planner.beginNavigate(1,1.5,64.5,.5,T*600,0);finish(slab,half,0,250);
        check(slab.planner.snapshot().status()==LocalPlanner.Status.SUCCEEDED&&Math.abs(slab.motion.snapshot().pose().y()-64.5)<1e-7,"half-height route reaches supported slab");
        var stair=extended();var full=MovementControllerTest.terrain(java.util.List.of(MovementControllerTest.floor(),new MovementController.Box(1,64,0,2,65,1)));
        stair.planner.beginNavigate(1,1.5,65,.5,T*600,0);finish(stair,full,0,250);
        check(stair.planner.snapshot().status()==LocalPlanner.Status.SUCCEEDED,"full-height route uses a real jump");
        var low=extended();var lowStairs=MovementControllerTest.terrain(java.util.List.of(MovementControllerTest.floor(),new MovementController.Box(1,64,0,2,64.5,1),new MovementController.Box(1.5,64.5,0,2,65,1),new MovementController.Box(-1,67,-1,3,68,2)));
        low.planner.beginNavigate(1,1.8,65,.5,T*600,0);finish(low,lowStairs,0,250);
        check(low.planner.snapshot().status()==LocalPlanner.Status.SUCCEEDED,"planner reaches stair goal under jump-blocking ceiling");
        var swim=extended();var water=MovementControllerTest.water(0);swim.planner.beginNavigate(1,1.5,65,.5,T*600,0);long now=finish(swim,water,0,300);
        check(swim.planner.snapshot().status()==LocalPlanner.Status.SUCCEEDED,"water route changes horizontal position and depth");
        check(swim.planner.needsMaintenance(),"water goal keeps original-authority maintenance requirement");
        now+=T;var hold=swim.planner.prepareMaintenance(now,water);check(hold!=null&&hold.safetyStop()&&hold.commandSeq()==0,"completed planner offers maintenance without goal replay");check(swim.planner.commit(hold,now),"maintenance commits through planner");
        var vertical=extended();vertical.planner.beginNavigate(1,.5,65,.5,T*600,0);finish(vertical,water,0,300);
        check(vertical.planner.snapshot().status()==LocalPlanner.Status.SUCCEEDED,"same-column vertical swimming route");
        var unknown=extended();unknown.planner.beginNavigate(1,1.5,64.5,.5,T*600,0);unknown.planner.tick(T,FLAT,false,0);
        check(unknown.planner.snapshot().status()==LocalPlanner.Status.FAILED,"legacy boolean safety cannot certify extended geometry");
    }
    private static void extendedPreemption(){
        var f=extended();var terrain=MovementControllerTest.terrain(java.util.List.of(MovementControllerTest.floor()));f.planner.beginNavigate(1,2.5,64,.5,T*600,0);
        var old=f.planner.tick(T,terrain,false,0);f.planner.externalVelocity(new MovementController.Velocity(.12,.2,0),T);
        check(old==null||!f.planner.commit(old,T),"knockback fences already prepared goal frame");
        check(f.planner.snapshot().status()==LocalPlanner.Status.FAILED&&f.planner.snapshot().code().equals("knockback_preempted"),"knockback never resumes prior goal");
        var neutral=f.planner.tick(T*2,terrain,false,0);check(neutral!=null&&neutral.safetyStop()&&!neutral.forward(),"failed goal still settles external inertia");f.planner.commit(neutral,T*2);
        for(long now=T*3;now<T*80&&f.motion.snapshot().status()==MovementController.Status.SETTLING;now+=T){var s=f.planner.tick(now,terrain,false,0);if(s!=null)f.planner.commit(s,now);}
        check(f.motion.ready(),"knockback settles before explicit new goal");
        f.planner.off(T*80);check(!f.planner.needsMaintenance()&&f.planner.prepareMaintenance(T*81,terrain)==null,"OFF closes maintenance authority");
    }
    private static void navigate(){var f=fixture();f.planner.beginNavigate(1,3.5,64,.5,T*600,0);long now=0;
        while(f.planner.snapshot().status()!=LocalPlanner.Status.SUCCEEDED && now<T*400){now+=T;var s=f.planner.tick(now,FLAT,false,0);if(s!=null)check(f.planner.commit(s,now),"planner commit");}
        check(f.planner.snapshot().status()==LocalPlanner.Status.SUCCEEDED,"bounded cardinal navigation reaches target");
        check(Math.abs(f.motion.snapshot().pose().x()-3.5)<=.15,"navigation target tolerance");
        check(f.planner.snapshot().stepId()>=6 && f.planner.snapshot().expandedNodes()<=256,"fresh look/walk step IDs and bounded search");
    }
    private static void hazardAndFreshPath(){var f=fixture();f.planner.beginNavigate(2,4.5,64,.5,T*600,0);long now=0;
        for(int i=0;i<12;i++){now+=T;var s=f.planner.tick(now,FLAT,false,0);if(s!=null)f.planner.commit(s,now);}
        long oldStep=f.planner.snapshot().stepId();now+=T;var queued=f.planner.tick(now,FLAT,false,0);
        now+=T;var stop=f.planner.tick(now,FLAT,false,1);check(queued==null||!f.planner.commit(queued,now),"damage alone invalidates prepared prior step");
        if(stop!=null){check(stop.safetyStop()&&!stop.forward(),"hazard permits neutral stop only");f.planner.commit(stop,now);}
        for(int i=0;i<20;i++){now+=T;var s=f.planner.tick(now,FLAT,true,1);if(s!=null){check(s.safetyStop()&&!s.forward(),"continuous hazard remains neutral");f.planner.commit(s,now);}}
        check(f.planner.snapshot().paused() && f.planner.snapshot().preemptions()==1,"continuous hazard counts one interruption");
        for(int i=0;i<19;i++){now+=T;var s=f.planner.tick(now,FLAT,false,1);check(s==null||s.safetyStop(),"one second safe hold before new intent");if(s!=null)f.planner.commit(s,now);}
        now+=T;f.planner.tick(now,FLAT,false,1);now+=T;var resumed=f.planner.tick(now,FLAT,false,1);
        check(f.planner.snapshot().stepId()>oldStep && !f.planner.snapshot().paused(),"safe hold replans with a fresh step identity");
        if(resumed!=null)f.planner.commit(resumed,now);
        var blocked=new MovementController.World(){public boolean safe(double x,double y,double z){return Math.abs(x-4.5)>.1||Math.abs(z-.5)>.1;}public double friction(double x,double y,double z){return .6f;}};
        now+=T;var secondStop=f.planner.tick(now,blocked,true,2);if(secondStop!=null)f.planner.commit(secondStop,now);
        for(int i=0;i<30;i++){now+=T;var s=f.planner.tick(now,blocked,false,2);if(s!=null)f.planner.commit(s,now);}
        check(f.planner.snapshot().status()==LocalPlanner.Status.FAILED && f.planner.snapshot().code().equals("no_safe_bounded_path"),"resume rebuilds route against changed world instead of replaying old path");
    }
    private static void budgetsAndFences(){var f=fixture();f.planner.beginNavigate(1,2.5,64,.5,T*1000,0);
        var s=f.planner.tick(T,FLAT,false,0);if(s!=null)f.planner.commit(s,T);
        check(f.planner.snapshot().status()==LocalPlanner.Status.NAVIGATING,"deadline fixture starts from an active reachable route");
        f.planner.tick(T*601,FLAT,false,0);check(f.planner.snapshot().status()==LocalPlanner.Status.FAILED && f.planner.snapshot().code().equals("goal_timeout"),"goal total budget never exceeds thirty seconds");
        var wall=fixture();wall.planner.beginNavigate(1,3.5,64,.5,T*600,0);
        wall.planner.tick(T,new MovementController.World(){public boolean safe(double x,double y,double z){return x<1;}public double friction(double x,double y,double z){return .6f;}},false,0);
        check(wall.planner.snapshot().status()==LocalPlanner.Status.FAILED && wall.planner.snapshot().expandedNodes()<=256,"unreachable route search is bounded");
        var off=fixture();off.planner.beginNavigate(1,2.5,64,.5,T*600,0);var old=off.planner.tick(T,FLAT,false,0);off.planner.off(T);
        check(old==null||!off.planner.commit(old,T),"OFF fences pending movement");check(off.planner.tick(T*2,FLAT,false,0)==null,"OFF does not resume when safe");
        try{off.planner.beginNavigate(2,2.5,64,.5,T*600,T*2);throw new AssertionError("OFF reopened");}catch(IllegalStateException expected){checks++;}
        var corrected=fixture();corrected.planner.beginNavigate(1,2.5,64,.5,T*600,0);corrected.planner.tick(T,FLAT,false,0);
        corrected.planner.correction(new MovementController.Pose(.5,64,.5,0,0),new MovementController.Velocity(0,0,0),T);
        check(corrected.planner.snapshot().status()==LocalPlanner.Status.FAILED && corrected.planner.tick(T*2,FLAT,false,0)==null,"server correction ends the old goal");
        try{fixture().planner.beginNavigate(1,17.5,64,.5,T*600,0);throw new AssertionError("oversized navigate");}catch(IllegalArgumentException expected){checks++;}
    }
    private static void preemptionLimit(){var f=fixture();f.planner.beginNavigate(1,10.5,64,.5,T*600,0);long now=0;
        for(int interruption=1;interruption<=4;interruption++){
            now+=T;var s=f.planner.tick(now,FLAT,false,interruption-1);if(s!=null)f.planner.commit(s,now);
            now+=T;s=f.planner.tick(now,FLAT,true,interruption);if(s!=null)f.planner.commit(s,now);
            if(interruption==4)break;
            for(int i=0;i<22;i++){now+=T;s=f.planner.tick(now,FLAT,false,interruption);if(s!=null)f.planner.commit(s,now);}
            check(!f.planner.snapshot().paused() && f.planner.snapshot().preemptions()==interruption,"safe recovery after each permitted interruption");
        }
        check(f.planner.snapshot().status()==LocalPlanner.Status.FAILED && f.planner.snapshot().code().equals("preemption_limit"),"fourth interruption terminates the goal");
    }
    private static void admission(){var f=fixture();f.motion.beginWalk(1,1,.5,64,2.5,T*100,0);
        try{f.planner.beginNavigate(2,3.5,64,.5,T*600,0);throw new AssertionError("overlapping direct action");}catch(IllegalStateException expected){checks++;}
        var invalid=fixture();invalid.motion.invalidate("invalid_binding",0);
        try{invalid.planner.beginNavigate(1,3.5,64,.5,T*600,0);throw new AssertionError("invalidated movement admitted");}catch(IllegalStateException expected){checks++;}
    }
    private static void check(boolean condition,String message){checks++;if(!condition)throw new AssertionError(message);}
}
