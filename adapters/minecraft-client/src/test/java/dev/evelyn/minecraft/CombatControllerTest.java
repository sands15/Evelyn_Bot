package dev.evelyn.minecraft;

/** Synthetic owner inputs only: no packet, socket, game or account access. */
public final class CombatControllerTest {
    private static int checks;
    private static final long T=50_000_000L;
    private static CombatController.Target target(int id,long spawn,boolean alive){return new CombatController.Target(id,spawn,new MovementController.Box(1.7,64,-.3,2.3,65.8,.3),true,alive);}
    private static CombatController.Context context(long epoch,boolean hand,boolean los,boolean safe,CombatController.Target target){return new CombatController.Context(epoch,new MovementController.Pose(0,64,0,0,0),hand,los,safe,target);}
    private static CombatController model(){var c=new CombatController(0);c.beginAttack(1,12,3,T*600,0);return c;}
    public static void main(String[]args){attackAndDeath();gates();fences();bounded();System.out.println("COMBAT_CONTROLLER_PASS "+checks);}
    private static void attackAndDeath(){
        var c=model();var ctx=context(1,true,true,true,target(12,3,true));var first=c.prepare(0,ctx);
        check(first!=null&&first.targetId()==12&&first.spawnEpoch()==3&&first.commandSeq()==1,"only explicit bound target is offered");
        check(c.snapshot().attacks()==0&&c.prepare(0,ctx)==null,"prepare has no effect and only one pending offer");
        check(c.valid(first,0,ctx)&&c.commit(first,0,ctx),"stable preflight and send commit count once");
        check(!c.commit(first,0,ctx)&&c.snapshot().attacks()==1,"duplicate commit cannot duplicate attack");
        check(c.prepare(T*10-1,ctx)==null,"cooldown at least 500ms after commit");var second=c.prepare(T*10,ctx);check(second!=null,"fresh attack after cooldown");c.commit(second,T*10,ctx);
        check(c.snapshot().status()==CombatController.Status.RUNNING&&c.snapshot().evidenceLevel().equals("none"),"emitted attack never proves hit or death");
        check(c.prepare(T*11,context(2,true,true,true,target(12,3,false)))==null,"observed death sends no additional attack");
        check(c.snapshot().status()==CombatController.Status.SUCCEEDED&&c.snapshot().code().equals("target_defeated_observed")&&c.snapshot().evidenceLevel().equals("server_observed"),"same-spawn server death is terminal evidence");
    }
    private static void gates(){
        for(int which=0;which<3;which++){var c=model();var ctx=context(1,which!=0,which!=1,which!=2,target(12,3,true));check(c.prepare(0,ctx)==null&&c.snapshot().status()==CombatController.Status.FAILED,"weapon LOS or safety denial has no attack");}
        var far=model();var outside=new CombatController.Target(12,3,new MovementController.Box(3.000001,64,-.3,3.6,66,.3),true,true);
        check(far.prepare(0,context(1,true,true,true,outside))==null,"eye to nearest AABB point outside 3m is rejected");
        var edge=model();var exact=new CombatController.Target(12,3,new MovementController.Box(3,64,-.3,3.6,66,.3),true,true);check(edge.prepare(0,context(1,true,true,true,exact))!=null,"exact three-metre nearest surface is admitted");
        var lost=model();check(lost.prepare(0,context(1,true,true,true,new CombatController.Target(12,3,null,false,true)))==null&&lost.snapshot().code().equals("target_lost"),"despawn is not a death claim");
        var reused=model();check(reused.prepare(0,context(1,true,true,true,target(12,4,false)))==null&&reused.snapshot().status()==CombatController.Status.FAILED,"reused entity ID does not inherit old target or death evidence");
        var missing=model();check(missing.prepare(0,context(1,true,true,true,null))==null,"unknown target cannot be guessed");
    }
    private static void fences(){
        var ctx=context(1,true,true,true,target(12,3,true));var c=model();var step=c.prepare(0,ctx);check(!c.valid(step,0,context(2,true,true,true,target(12,3,true))),"environment change fences prepared attack");
        check(!c.commit(step,0,context(2,true,true,true,target(12,3,true)))&&c.snapshot().attacks()==0,"changed preflight cannot commit");
        var cancelled=model();step=cancelled.prepare(0,ctx);check(!cancelled.cancel(2,0)&&cancelled.cancel(1,0),"only exact command may cancel");check(!cancelled.valid(step,0,ctx)&&cancelled.prepare(T,ctx)==null,"cancel fences offer and never resumes");
        var off=model();step=off.prepare(0,ctx);off.off(0);check(!off.commit(step,0,ctx)&&off.prepare(T,ctx)==null,"OFF has no later effects");
        try{off.beginAttack(2,12,3,T*100,T);throw new AssertionError("OFF reopened");}catch(IllegalStateException expected){checks++;}
        var timed=model();step=timed.prepare(0,ctx);check(!timed.commit(step,T+1,ctx)&&timed.snapshot().attacks()==0,"offer age is bounded to one tick");
        var changed=model();step=changed.prepare(0,ctx);check(!changed.commit(step,0,context(1,false,true,true,target(12,3,true))),"same epoch still rechecks concrete hand state");
        var successor=model();step=successor.prepare(0,ctx);successor.commit(step,0,ctx);successor.cancel(1,0);successor.beginAttack(2,12,3,T*100,0);
        check(successor.prepare(T,ctx)==null,"new command does not reset physical attack cooldown");
        check(successor.prepare(T*10,ctx)!=null,"new command resumes only after inherited cooldown");
        try{off.prepare(-1,ctx);throw new AssertionError("regressed clock");}catch(IllegalArgumentException expected){checks++;}
        check(off.snapshot().status()==CombatController.Status.OFF,"clock failure cannot clear permanent OFF latch");
    }
    private static void bounded(){
        var c=new CombatController(0);var ctx=context(1,true,true,true,target(12,3,true));c.beginAttack(1,12,3,T*2000,0);
        for(long now=0;now<T*600;now+=T*10){var s=c.prepare(now,ctx);if(s!=null)c.commit(s,now,ctx);}
        check(c.snapshot().attacks()==60,"30-second total run plus cooldown bounds attack count");check(c.prepare(T*600,ctx)==null&&c.snapshot().code().equals("attack_timeout"),"long caller deadline cannot expand the goal budget");
        var overdue=new CombatController(0);overdue.beginAttack(1,12,3,T,0);var s=overdue.prepare(0,ctx);check(!overdue.commit(s,T,ctx),"absolute deadline dominates otherwise fresh offer");
        long origin=Long.MAX_VALUE-T;var wrap=new CombatController(origin);wrap.beginAttack(1,12,3,origin+T*20,origin);s=wrap.prepare(origin+T,ctx);check(wrap.commit(s,origin+T,ctx),"signed monotonic clock wrap remains supported");
        try{model().beginAttack(2,12,3,T*100,0);throw new AssertionError("overlap");}catch(IllegalStateException expected){checks++;}
        try{new CombatController(0).beginAttack(1,12,0,T*100,0);throw new AssertionError("zero spawn");}catch(IllegalArgumentException expected){checks++;}
    }
    private static void check(boolean value,String message){checks++;if(!value)throw new AssertionError(message);}
}
