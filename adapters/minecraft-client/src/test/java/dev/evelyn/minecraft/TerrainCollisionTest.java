package dev.evelyn.minecraft;

import java.util.UUID;

public final class TerrainCollisionTest {
    private static int checks,event;private static ChunkWorld world;private static BlockCatalog catalog;
    public static void main(String[] args)throws Exception{
        catalog=BlockCatalog.loadPinned();world=new ChunkWorld(new UUID(0,10),0);world.bind(next(),"minecraft:overworld",0,16);
        int[][] data=new int[1][4096];for(int z=0;z<16;z++)for(int x=0;x<16;x++)data[0][z*16+x]=1;
        world.replaceChunk(next(),0,0,data);TerrainCollision terrain=new TerrainCollision(world,catalog,()->false);
        check(terrain.safe(8.5,1,8.5),"known dry support works before tags");
        check(!terrain.safe(16.5,1,8.5),"unloaded terrain never becomes air");
        int slab=find("minecraft:oak_slab","type","bottom");set(8,1,8,slab);
        check(terrain.safe(8.5,1.5,8.5)&&!terrain.safe(8.5,1,8.5),"half slab surface and embedded body differ");
        int stairs=find("minecraft:oak_stairs","facing","east");set(8,1,8,stairs);
        check(terrain.sample(new MovementController.Box(8,1,8,9,2,9)).collisionBoxes().size()>=2,"stair retains multi-box shape");
        set(8,1,8,89);MovementController.Box body=MovementController.body(new MovementController.Pose(8.5,1,8.5,0,0));
        check(!terrain.fluid(body).known(),"unconfirmed flow tag blocks water");
        terrain=new TerrainCollision(world,catalog,()->true);
        check(terrain.fluid(body).known()&&Math.abs(terrain.fluid(body).depth()-(double)(8/9f))<1e-8,"source height official float");
        set(8,2,8,89);check(terrain.fluid(body).eyeSubmerged(),"submerged eye observed from camera fluid height");
        set(9,1,8,93);set(8,2,8,0);
        check(terrain.fluid(body).flowX()>0,"shallower east neighbor produces east current");
        set(8,1,8,0);set(9,1,8,0);
        check(terrain.lineOfSight(new EntityTracker.Point(8.5,2.62,8.5),new EntityTracker.Point(10,2.62,8.5)),"known air ray");
        set(9,2,8,slab);check(!terrain.lineOfSight(new EntityTracker.Point(8.5,2.62,8.5),new EntityTracker.Point(10,2.62,8.5)),"partial shapes opaque until outline geometry exists");
        check(!terrain.lineOfSight(new EntityTracker.Point(15.5,2,8.5),new EntityTracker.Point(17,2,8.5)),"unknown ray rejected");
        System.out.println("TERRAIN_COLLISION_PASS "+checks);
    }
    private static int find(String name,String key,String value){for(int i=0;i<catalog.stateCount();i++){var s=catalog.lookup(i);if(name.equals(s.name())&&value.equals(s.properties().get(key))&&s.geometrySupported())return i;}throw new AssertionError("state");}
    private static ChunkWorld.Event next(){return new ChunkWorld.Event(1,++event,event*1000000L);}
    private static void set(int x,int y,int z,int state){world.updateBlock(next(),x,y,z,state);}
    private static void check(boolean good,String why){if(!good)throw new AssertionError(why);checks++;}
}
