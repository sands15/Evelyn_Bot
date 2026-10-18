package dev.evelyn.minecraft;

public final class MovementControllerTest {
    private static int checks;
    private static final long T = 50_000_000L;
    private static final MovementController.World FLAT = new MovementController.World() {
        public boolean safe(double x, double y, double z) { return y == 64; }
        public double friction(double x, double y, double z) { return .6f; }
    };
    public static void main(String[] args) {
        walkAndLook(); cancelAndCommitFence(); correctionAndLate(); blockedAndUnknown(); clockWrap(); expiredPreparation();extendedJumpAndTerrain();waterAndMaintenance();
        System.out.println("MOVEMENT_CONTROLLER_PASS " + checks);
    }
    private static MovementController model(long origin) {
        MovementController result = new MovementController(origin);
        result.correction(new MovementController.Pose(0, 64, 0, 0, 0), new MovementController.Velocity(0, 0, 0), origin);
        return result;
    }
    private static void walkAndLook() {
        MovementController m = model(0); m.beginWalk(1, 1, 0, 64, 2, 5_000_000_000L, 0);
        check(m.prepareTick(T-1, FLAT) == null, "no early tick");
        MovementController.PreparedStep first = m.prepareTick(T, FLAT);
        double a = (double) .98f * .1f;
        near(first.pose().z(), a, 1e-12, "first position integrates acceleration, never target teleport");
        near(first.velocity().z(), a * (.6f * .91f), 1e-12, "post-move friction preserves float operations");
        check(first.forward() && first.onGround() && !first.safetyStop(), "normal motion has action authority");
        check(m.snapshot().pose().z() == 0, "prepare does not mutate predicted position");
        check(m.prepareTick(T, FLAT) == null, "one pending candidate only");
        check(m.commit(first, T), "prepared step commits once");
        check(!m.commit(first, T), "same proposal cannot be committed twice");
        long now=T;
        while (m.snapshot().status() == MovementController.Status.RUNNING && now < 5_000_000_000L) {
            now+=T; var step=m.prepareTick(now,FLAT); if(step!=null)check(m.commit(step,now),"normal step");
        }
        check(m.snapshot().status() == MovementController.Status.SUCCEEDED, "short flat walk reaches a settled result");
        near(m.snapshot().pose().z(), 2, .15, "walk target tolerance");
        check(m.snapshot().velocity().x()==0 && m.snapshot().velocity().z()==0, "success is settled");
        check("client_simulated".equals(m.snapshot().evidenceLevel()), "no server ACK claim");
        var position=m.snapshot().pose(); m.beginLook(1,2,90,20,now+T*10,now);
        for(int i=0;i<4 && m.snapshot().status()==MovementController.Status.RUNNING;i++) {
            now+=T; var step=m.prepareTick(now,FLAT); if(step!=null)m.commit(step,now);
        }
        check(m.snapshot().status()==MovementController.Status.SUCCEEDED && m.snapshot().pose().yaw()==90 && m.snapshot().pose().pitch()==20,"look completes without translation");
        near(m.snapshot().pose().z(),position.z(),0,"look preserves position");
    }
    private static void cancelAndCommitFence() {
        MovementController m=model(0);m.beginWalk(1,1,0,64,4,T*80,0);
        var uncommitted=m.prepareTick(T,FLAT);check(m.cancel(1,1,T),"exact cancel accepted");
        check(!m.valid(uncommitted,T) && !m.commit(uncommitted,T),"cancel before commit fences old intent");
        near(m.snapshot().pose().z(),0,0,"uncommitted action has no simulated displacement");
        m.beginWalk(2,1,0,64,4,T*80,T);
        long now=T;for(int i=0;i<4;i++){now+=T;m.commit(m.prepareTick(now,FLAT),now);}
        double before=m.snapshot().velocity().z(), position=m.snapshot().pose().z();
        check(!m.cancel(1,1,now),"stale cancel cannot affect successor");
        check(m.cancel(2,1,now),"running exact cancel");
        near(m.snapshot().velocity().z(),before,0,"cancel preserves physical inertia");
        try{m.beginLook(3,1,0,0,now+T*20,now);throw new AssertionError("settling admitted action");}catch(IllegalStateException expected){checks++;}
        int neutral=0;while(m.snapshot().status()==MovementController.Status.SETTLING && neutral<20){
            now+=T;var step=m.prepareTick(now,FLAT);check(step.safetyStop()&&!step.forward()&&step.commandSeq()==0&&step.stepId()==0,"neutral stop uses separate safety authority");
            check(m.commit(step,now),"neutral commit");neutral++;
        }
        check(m.snapshot().status()==MovementController.Status.CANCELLED && m.snapshot().pose().z()>position,"cancel settles without claiming rollback");
        check(m.snapshot().velocity().z()==0,"cancel eventually settles under ground drag");
    }
    private static void correctionAndLate() {
        MovementController m=model(0);m.beginWalk(1,1,0,64,1,T*60,0);var step=m.prepareTick(T,FLAT);
        m.correction(new MovementController.Pose(2,64,2,10,0),new MovementController.Velocity(0,0,0),T);
        check(!m.commit(step,T) && m.snapshot().pose().x()==2,"correction dominates a prepared pose");
        check(m.snapshot().status()==MovementController.Status.FAILED,"correction ends active action without replay");
        m.beginWalk(2,1,2,64,3,T*80,T);
        check(m.prepareTick(T*6,FLAT)==null && m.snapshot().status()==MovementController.Status.FAILED,"late more than 150ms does not replay ticks");
        check(m.prepareTick(T*7,FLAT)==null,"late failure does not auto resume");
        MovementController deadline=model(0);deadline.beginWalk(1,1,0,64,1,T,0);
        check(deadline.prepareTick(T,FLAT)==null && deadline.snapshot().status()==MovementController.Status.FAILED,"action deadline precedes side effects");
    }
    private static void blockedAndUnknown() {
        MovementController m=model(0);m.beginWalk(1,1,0,64,2,T*80,0);
        MovementController.World wall=new MovementController.World(){public boolean safe(double x,double y,double z){return z<.05;}public double friction(double x,double y,double z){return .6f;}};
        check(m.prepareTick(T,wall)==null && m.snapshot().status()==MovementController.Status.FAILED,"unsafe swept endpoint emits no step");
        MovementController diagonal=model(0);diagonal.beginWalk(1,1,1,64,1,T*80,0);
        MovementController.World corner=new MovementController.World(){public boolean safe(double x,double y,double z){return !(x>0&&z==0);}public double friction(double x,double y,double z){return .6f;}};
        check(diagonal.prepareTick(T,corner)==null,"cross-corner probes cover conservative swept body area");
        MovementController unknown=model(0);unknown.beginLook(1,1,90,0,T*20,0);
        check(unknown.prepareTick(T,new MovementController.World(){public boolean safe(double x,double y,double z){return false;}public double friction(double x,double y,double z){return .6f;}})==null,"unknown support cannot grant onGround");
        try{model(0).beginWalk(1,1,5,64,0,T*20,0);throw new AssertionError("long walk");}catch(IllegalArgumentException expected){checks++;}
        try{model(0).beginWalk(1,1,0,65,0,T*20,0);throw new AssertionError("vertical walk");}catch(IllegalArgumentException expected){checks++;}
        var edge=model(0);edge.correction(new MovementController.Pose(30_000_000,64,0,0,0),new MovementController.Velocity(0,0,0),0);
        try{edge.beginWalk(1,1,30_000_001,64,0,T*20,0);throw new AssertionError("target outside pose bounds");}catch(IllegalArgumentException expected){checks++;}
    }
    private static void clockWrap(){long origin=Long.MAX_VALUE-T;MovementController m=model(origin);m.beginLook(1,1,90,0,origin+T*10,origin);var step=m.prepareTick(origin+T,FLAT);check(m.commit(step,origin+T),"signed nanoTime wrap remains valid");}
    private static void expiredPreparation(){var m=model(0);m.beginWalk(1,1,0,64,1,T*80,0);var step=m.prepareTick(T,FLAT);
        check(!m.commit(step,T*2+1),"delayed prepared frame cannot commit");
        check(m.snapshot().status()==MovementController.Status.FAILED && m.prepareTick(T*3,FLAT)==null,"expired preparation terminates instead of leaving a running action stranded");
    }
    static MovementController.World terrain(java.util.List<MovementController.Box> boxes){return new MovementController.World(){
        public boolean safe(double x,double y,double z){return true;}public double friction(double x,double y,double z){return .6f;}
        public MovementController.Region sample(MovementController.Box envelope){return new MovementController.Region(true,boxes);}
        public MovementController.FluidContact fluid(MovementController.Box body){return new MovementController.FluidContact(true,0,false,0,0,0);}
    };}
    static MovementController.Box floor(){return new MovementController.Box(-20,63,-20,20,64,20);}
    static MovementController extended(){var m=new MovementController(0,true);m.correction(new MovementController.Pose(.5,64,.5,0,0),new MovementController.Velocity(0,0,0),0);return m;}
    private static void extendedJumpAndTerrain(){
        var world=terrain(java.util.List.of(floor()));var jump=extended();jump.beginJump(1,1,T*100,0);
        var first=jump.prepareTick(T,world);check(first!=null&&first.jump()&&!first.onGround(),"jump pulse leaves support");
        near(first.pose().y(),64+(double).42f,1e-12,"official jump impulse integrates before gravity");
        near(first.velocity().y(),((double).42f-.08)*.98f,1e-12,"official jump postgravity float drag");
        check(jump.commit(first,T),"jump prepare commit");long now=T;double peak=first.pose().y();
        while(jump.snapshot().status()==MovementController.Status.RUNNING&&now<T*100){now+=T;var s=jump.prepareTick(now,world);if(s!=null){check(!s.jump(),"ground jump is one pulse");jump.commit(s,now);peak=Math.max(peak,s.pose().y());}}
        check(jump.snapshot().status()==MovementController.Status.SUCCEEDED,"jump lands then completes");near(jump.snapshot().pose().y(),64,1e-9,"exact supported landing");check(peak>65.2&&peak<65.3,"bounded vanilla jump apex");
        var cancel=extended();cancel.beginJump(1,1,T*100,0);var old=cancel.prepareTick(T,world);cancel.cancel(1,1,T);check(!cancel.commit(old,T),"cancel fences uncommitted jump");
        var flying=extended();flying.beginJump(1,1,T*100,0);flying.commit(flying.prepareTick(T,world),T);double vy=flying.snapshot().velocity().y();flying.cancel(1,1,T);
        near(flying.snapshot().velocity().y(),vy,0,"air cancellation preserves vertical inertia");now=T;
        while(flying.snapshot().status()==MovementController.Status.SETTLING&&now<T*100){now+=T;var s=flying.prepareTick(now,world);if(s!=null){check(s.safetyStop()&&!s.jump()&&!s.forward(),"cancelled airborne motion is neutral");flying.commit(s,now);}}
        check(flying.snapshot().status()==MovementController.Status.CANCELLED,"air cancel settles only after landing");
        var slab=extended();slab.beginWalk(1,1,1.5,64.5,.5,T*200,0);finishExtended(slab,terrain(java.util.List.of(floor(),new MovementController.Box(1,64,0,2,64.5,1))),200);
        check(slab.snapshot().status()==MovementController.Status.SUCCEEDED,"exact bottom slab climbs with automatic half step");near(slab.snapshot().pose().y(),64.5,1e-9,"slab foot surface");
        var block=extended();block.beginWalk(1,1,1.5,65,.5,T*200,0);finishExtended(block,terrain(java.util.List.of(floor(),new MovementController.Box(1,64,0,2,65,1))),200);
        check(block.snapshot().status()==MovementController.Status.SUCCEEDED,"one-block ascent uses jump instead of teleport");
        var stairs=extended();stairs.beginWalk(1,1,1.7,65,.5,T*200,0);finishExtended(stairs,terrain(java.util.List.of(floor(),new MovementController.Box(1,64,0,2,64.5,1),new MovementController.Box(1.5,64.5,0,2,65,1))),200);
        check(stairs.snapshot().status()==MovementController.Status.SUCCEEDED,"stair lower and upper collision pieces stay traversable");
        var lowStairs=extended();lowStairs.beginWalk(1,1,1.8,65,.5,T*200,0);var lowStairWorld=terrain(java.util.List.of(floor(),new MovementController.Box(1,64,0,2,64.5,1),new MovementController.Box(1.5,64.5,0,2,65,1),new MovementController.Box(-1,67,-1,3,68,2)));
        long stairNow=0;while(lowStairs.snapshot().status()==MovementController.Status.RUNNING&&stairNow<T*200){stairNow+=T;var offered=lowStairs.prepareTick(stairNow,lowStairWorld);if(offered!=null){check(!offered.jump(),"automatic half steps do not press jump under a low ceiling");lowStairs.commit(offered,stairNow);}}
        check(lowStairs.snapshot().status()==MovementController.Status.SUCCEEDED,"low ceiling allows two automatic stair rises without a jump");
        var down=extended();down.correction(new MovementController.Pose(1.5,64.5,.5,0,0),new MovementController.Velocity(0,0,0),0);down.beginWalk(1,1,.5,64,.5,T*200,0);finishExtended(down,terrain(java.util.List.of(floor(),new MovementController.Box(1,64,0,2,64.5,1))),200);
        check(down.snapshot().status()==MovementController.Status.SUCCEEDED&&down.snapshot().pose().y()==64,"descending slab integrates gravity then supported landing");
        var ceiling=extended();ceiling.beginJump(1,1,T*100,0);check(ceiling.prepareTick(T,terrain(java.util.List.of(floor(),new MovementController.Box(0,65.9,0,1,66.9,1))))==null,"low ceiling blocks jump before input");
        var unknown=extended();unknown.beginJump(1,1,T*100,0);check(unknown.prepareTick(T,FLAT)==null&&unknown.snapshot().status()==MovementController.Status.FAILED,"unknown geometry fails closed even if legacy safe says true");
        var knock=extended();knock.beginLook(1,1,0,0,T*100,0);var prior=knock.prepareTick(T,world);knock.externalVelocity(new MovementController.Velocity(.2,.3,0),T);check(!knock.commit(prior,T),"external velocity invalidates old preparation");near(knock.snapshot().velocity().y(),.3,0,"knockback vertical component preserved");finishExtended(knock,world,100,T);check(knock.snapshot().status()==MovementController.Status.CANCELLED,"knockback settles before fresh admission");
        var aim=extended();aim.beginWalk(1,1,1.5,64,.5,T*200,0);finishExtended(aim,world,150);var before=aim.snapshot();
        check(aim.ready()&&before.velocity().y()<0,"dry terminal retains vanilla downward gravity velocity");
        aim.face(45,-20,T*150);var faced=aim.snapshot();
        check(aim.ready()&&faced.velocity().equals(before.velocity())&&faced.pose().x()==before.pose().x()&&faced.pose().y()==before.pose().y()&&faced.pose().z()==before.pose().z(),"packet aim preserves grounded physical state after walking");
        check(faced.status()==before.status()&&faced.commandSeq()==before.commandSeq()&&faced.stepId()==before.stepId()&&faced.pose().yaw()==45&&faced.pose().pitch()==-20,"packet aim changes only orientation and pending fence");
        aim.face(-45,10,T*160);check(aim.ready(),"repeated stationary aim remains ready for next attack");
        var unready=extended();unready.beginJump(1,1,T*200,0);unready.commit(unready.prepareTick(T,world),T);
        try{unready.face(0,0,T);throw new AssertionError("airborne facing permitted");}catch(IllegalStateException expected){checks++;}
    }
    static void finishExtended(MovementController m,MovementController.World world,int max){finishExtended(m,world,max,0);}
    static void finishExtended(MovementController m,MovementController.World world,int max,long start){long now=start;for(int i=0;i<max&&(m.snapshot().status()==MovementController.Status.RUNNING||m.snapshot().status()==MovementController.Status.SETTLING);i++){now+=T;var s=m.prepareTick(now,world);if(s!=null)m.commit(s,now);}}
    static MovementController.World water(double flow){return new MovementController.World(){
        public boolean safe(double x,double y,double z){return true;}public double friction(double x,double y,double z){return .6f;}
        public MovementController.Region sample(MovementController.Box envelope){return new MovementController.Region(true,java.util.List.of(floor()));}
        public MovementController.FluidContact fluid(MovementController.Box body){double depth=Math.max(0,66-body.minY());return new MovementController.FluidContact(true,depth,body.minY()+(double)1.62f<=66,depth>0?flow:0,0,0);}
    };}
    private static void waterAndMaintenance(){
        var m=extended();var pool=water(0);m.beginWalk(1,1,1.5,65,.5,T*400,0);long now=0;boolean buoyancy=false;
        while(m.snapshot().status()==MovementController.Status.RUNNING&&now<T*400){now+=T;var s=m.prepareTick(now,pool);if(s!=null){buoyancy|=s.jump();m.commit(s,now);}}
        check(m.snapshot().status()==MovementController.Status.SUCCEEDED,"water horizontal and vertical target arrives");check(buoyancy,"water ascent uses jump buoyancy input");
        check(Math.abs(m.snapshot().pose().y()-65)<=.15&&Math.abs(m.snapshot().pose().x()-1.5)<=.15,"water arrival tolerance is local simulation");
        check(m.needsMaintenance(),"water arrival requires continued physical hold");
        for(int i=0;i<30;i++){now+=T;var s=m.prepareMaintenance(now,pool);check(s!=null&&s.safetyStop()&&s.commandSeq()==0&&s.stepId()==0,"water maintenance has separate safety authority");m.commit(s,now);}
        check(Math.abs(m.snapshot().pose().y()-65)<=.2,"water hold remains near admitted depth");
        m.beginWalk(2,1,2.5,65,.5,now+T*200,now);check(m.snapshot().status()==MovementController.Status.RUNNING,"new explicit goal may replace settled water hold without zeroing velocity");
        var chain=extended();chain.beginLook(1,1,0,0,T*100,0);for(int i=1;i<=3;i++){var offered=chain.prepareTick(T*i,pool);if(offered!=null)chain.commit(offered,T*i);}
        chain.beginWalk(1,2,1.5,65,.5,T*100,T*4);check(chain.prepareTick(T*4,pool)!=null,"water successor keeps the already due physical tick");
        chain.cancel(1,2,T*4);var neutral=chain.prepareTick(T*4,pool);check(neutral!=null&&neutral.safetyStop(),"water cancellation does not defer a due physical tick");
        var flow=extended();flow.beginLook(1,1,0,0,T*100,0);var s=flow.prepareTick(T,water(.5));
        near(s.pose().x(),.507,1e-12,"raw current mean is scaled by official .014 before movement");near(s.velocity().x(),.007*(double).8f,1e-12,"water current receives postmove .8 float drag");
        var minimum=extended();minimum.beginLook(1,1,0,0,T*100,0);s=minimum.prepareTick(T,water(.1));near(s.pose().x(),.5045,1e-12,"small flow from rest receives official minimum push");
        var tiny=extended();tiny.beginLook(1,1,0,0,T*100,0);s=tiny.prepareTick(T,water(.001));near(s.pose().x(),.5045,1e-12,"nonzero average already passed the world's accumulated-vector threshold");
        var normalizeZero=extended();normalizeZero.beginLook(1,1,0,0,T*100,0);s=normalizeZero.prepareTick(T,water(.0005));near(s.pose().x(),.5,0,"mean from eight contributing cells can normalize to zero after sum cutoff passed");
        var below=extended();below.beginLook(1,1,0,0,T*100,0);s=below.prepareTick(T,water(.00071428));near(s.pose().x(),.5,0,"scaled current below official normalize epsilon stays zero");
        var above=extended();above.beginLook(1,1,0,0,T*100,0);s=above.prepareTick(T,water(.00071429));near(s.pose().x(),.5045,1e-12,"scaled current above official normalize epsilon receives minimum push");
        var shallow=extended();shallow.beginWalk(1,1,.5,64.5,.5,T*100,0);var shallowWater=new MovementController.World(){
            public boolean safe(double x,double y,double z){return true;}public double friction(double x,double y,double z){return .6f;}
            public MovementController.Region sample(MovementController.Box envelope){return new MovementController.Region(true,java.util.List.of(floor()));}
            public MovementController.FluidContact fluid(MovementController.Box body){return new MovementController.FluidContact(true,Math.max(0,64.25-body.minY()),false,0,0,0);}
        };s=shallow.prepareTick(T,shallowWater);near(s.pose().y(),64+(double).42f,1e-12,"grounded shallow-water jump uses ground impulse, not deep-water buoyancy");
        var expired=extended();expired.beginLook(1,1,0,0,T*5,0);for(int i=1;i<=3;i++){s=expired.prepareTick(T*i,pool);if(s!=null)expired.commit(s,T*i);}check(expired.needsMaintenance(),"short water action creates hold");
        check(expired.prepareMaintenance(T*5,pool)==null&&expired.snapshot().status()==MovementController.Status.FAILED,"maintenance cannot outlive original action deadline");
        check(!expired.needsMaintenance()&&expired.prepareMaintenance(T*6,pool)==null,"expired maintenance does not reanimate after failure");
        var cancelled=extended();cancelled.beginWalk(1,1,2,65,.5,T*200,0);cancelled.commit(cancelled.prepareTick(T,pool),T);cancelled.cancel(1,1,T);now=T;
        while(cancelled.snapshot().status()==MovementController.Status.SETTLING&&now<T*200){now+=T;s=cancelled.prepareTick(now,pool);if(s!=null){check(s.safetyStop(),"water cancellation hold never reuses action identity");cancelled.commit(s,now);}}
        check(cancelled.snapshot().status()==MovementController.Status.CANCELLED&&cancelled.needsMaintenance(),"water cancel reaches hold while retaining maintenance");
    }
    private static void near(double actual,double expected,double tolerance,String message){check(Math.abs(actual-expected)<=tolerance,message+": "+actual);}
    private static void check(boolean condition,String message){checks++;if(!condition)throw new AssertionError(message);}
}
