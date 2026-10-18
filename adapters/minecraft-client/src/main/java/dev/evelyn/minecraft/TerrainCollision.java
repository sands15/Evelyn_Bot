package dev.evelyn.minecraft;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.function.BooleanSupplier;
import static dev.evelyn.minecraft.MovementController.*;

/** Host holds the connection lifetime lock across each query; unknown cells never become empty space. */
final class TerrainCollision implements World {
    private final ChunkWorld world;private final BlockCatalog catalog;private final BooleanSupplier flowTags;
    TerrainCollision(ChunkWorld world,BlockCatalog catalog,BooleanSupplier flowTags){this.world=world;this.catalog=catalog;this.flowTags=flowTags;}
    public boolean safe(double x,double y,double z){return MovementController.traversable(this,x,y,z);}
    public double friction(double x,double y,double z){BlockCatalog.State state=new Cells().at((int)Math.floor(x),(int)Math.floor(y-.001),(int)Math.floor(z));return state.geometrySupported()?state.friction():Double.NaN;}
    public Region sample(Box box){
        List<Box> result=new ArrayList<>();Cells cells=new Cells();
        try{for(int x=lo(box.minX());x<=hi(box.maxX());x++)for(int y=lo(box.minY());y<=hi(box.maxY());y++)for(int z=lo(box.minZ());z<=hi(box.maxZ());z++){
            BlockCatalog.State state=cells.known(x,y,z);
            for(BlockCatalog.Box b:state.collisionBoxes())result.add(new Box(x+b.minX(),y+b.minY(),z+b.minZ(),x+b.maxX(),y+b.maxY(),z+b.maxZ()));
        }return new Region(true,result);}catch(Unknown|IllegalArgumentException unsupported){return new Region(false,List.of());}
    }
    public FluidContact fluid(Box body){
        Cells cells=new Cells();double depth=0,sx=0,sy=0,sz=0;int count=0;boolean eyes=false;
        double eye=body.minY()+(double)1.62f;int eyeX=lo((body.minX()+body.maxX())/2),eyeZ=lo((body.minZ()+body.maxZ())/2);
        try{
            for(int x=lo(body.minX()+.001);x<=hi(body.maxX()-.001);x++)for(int y=lo(body.minY()+.001);y<=hi(body.maxY()-.001);y++)for(int z=lo(body.minZ()+.001);z<=hi(body.maxZ()-.001);z++){
                BlockCatalog.State state=cells.known(x,y,z);if(state.fluid().kind()==BlockCatalog.FluidKind.NONE)continue;
                if(!flowTags.getAsBoolean())throw new Unknown();
                BlockCatalog.State above=cells.known(x,y+1,z);
                double height=water(above)?1:state.fluid().ownHeight(),surface=y+height;
                if(surface<body.minY()+.001)continue;
                depth=Math.max(depth,surface-body.minY());
                double camera=state.fluid().source()&&(above.sturdyFaces()&BlockCatalog.DOWN)!=0?1:height;
                if(x==eyeX&&z==eyeZ&&eye>=y&&eye<=y+camera)eyes=true;
                double[] flow=flow(cells,x,y,z,state);double factor=depth<.4?depth:1;
                sx+=flow[0]*factor;sy+=flow[1]*factor;sz+=flow[2]*factor;count++;
            }
            if(sx*sx+sy*sy+sz*sz<9.999999747378752e-6){sx=0;sy=0;sz=0;}
            else if(count>0){sx/=count;sy/=count;sz/=count;}
            return new FluidContact(true,depth,eyes,sx,sy,sz);
        }catch(Unknown|IllegalArgumentException unsupported){return new FluidContact(false,0,false,0,0,0);}
    }
    // Official horizontal iteration is NORTH,EAST,SOUTH,WEST; float height subtraction precedes double accumulation.
    private static final int[][] DIRECTIONS={{0,-1,BlockCatalog.NORTH},{1,0,BlockCatalog.EAST},{0,1,BlockCatalog.SOUTH},{-1,0,BlockCatalog.WEST}};
    private double[] flow(Cells cells,int x,int y,int z,BlockCatalog.State state){
        double fx=0,fz=0,fy=0;boolean fallingFace=false;
        for(int[] direction:DIRECTIONS){
            int nx=x+direction[0],nz=z+direction[1];BlockCatalog.State neighbor=cells.known(nx,y,nz);
            float h=neighbor.fluid().ownHeight(),difference=0;
            if(h==0&&!neighbor.blocksFluidFlow()){
                BlockCatalog.State below=cells.known(nx,y-1,nz);float low=below.fluid().ownHeight();
                if(low>0)difference=state.fluid().ownHeight()-(low-.8888889f);
            }else if(h>0)difference=state.fluid().ownHeight()-h;
            fx+=(double)(direction[0]*difference);fz+=(double)(direction[1]*difference);
            if(state.fluid().falling()&&(solid(neighbor,direction[2])||solid(cells.known(nx,y+1,nz),direction[2])))fallingFace=true;
        }
        if(fallingFace){double length=Math.hypot(fx,fz);if(length<9.999999747378752e-6){fx=0;fz=0;}else{fx/=length;fz/=length;}fy=-6;}
        double length=Math.sqrt(fx*fx+fy*fy+fz*fz);return length<9.999999747378752e-6?new double[3]:new double[]{fx/length,fy/length,fz/length};
    }
    private static boolean water(BlockCatalog.State state){return state.fluid().kind()==BlockCatalog.FluidKind.WATER;}
    private static boolean solid(BlockCatalog.State state,int face){return !water(state)&&(state.sturdyFaces()&face)!=0;}
    boolean lineOfSight(EntityTracker.Point from,EntityTracker.Point to){
        if(from==null||to==null)return false;Cells cells=new Cells();
        try{for(int x=lo(Math.min(from.x(),to.x()))-1;x<=lo(Math.max(from.x(),to.x()));x++)
            for(int y=lo(Math.min(from.y(),to.y()))-1;y<=lo(Math.max(from.y(),to.y()));y++)
                for(int z=lo(Math.min(from.z(),to.z()))-1;z<=lo(Math.max(from.z(),to.z()));z++){
                    if(!intersectsRay(from,to,new Box(x,y,z,x+1,y+1,z+1)))continue;
                    BlockCatalog.State state=cells.known(x,y,z);
                    // shortcut: movement boxes are not outline boxes; reject partial and solid cells until exact outline support exists.
                    if(state.kind()!=BlockCatalog.Kind.AIR&&!water(state))return false;
                }return true;
        }catch(Unknown|IllegalArgumentException unsupported){return false;}
    }
    static boolean intersectsRay(EntityTracker.Point from,EntityTracker.Point to,Box box){
        double[] a={from.x(),from.y(),from.z()},b={to.x(),to.y(),to.z()},min={box.minX(),box.minY(),box.minZ()},max={box.maxX(),box.maxY(),box.maxZ()};
        double low=0,high=1;for(int i=0;i<3;i++){double d=b[i]-a[i];if(d==0){if(a[i]<min[i]||a[i]>max[i])return false;}else{
            double t1=(min[i]-a[i])/d,t2=(max[i]-a[i])/d;low=Math.max(low,Math.min(t1,t2));high=Math.min(high,Math.max(t1,t2));if(low>high)return false;
        }}return true;
    }
    private static int lo(double value){return (int)Math.floor(value);}
    private static int hi(double value){return (int)Math.floor(Math.nextDown(value));}
    private final class Cells {
        final Map<ChunkWorld.BlockPos,BlockCatalog.State> cache=new HashMap<>();
        BlockCatalog.State at(int x,int y,int z){
            ChunkWorld.BlockPos pos=new ChunkWorld.BlockPos(x,y,z);BlockCatalog.State old=cache.get(pos);if(old!=null)return old;
            if(cache.size()>=512)throw new Unknown();
            Integer id=world.sample(List.of(pos)).blocks().getFirst().stateId();BlockCatalog.State state=catalog.lookup(id==null?-1:id);cache.put(pos,state);return state;
        }
        BlockCatalog.State known(int x,int y,int z){BlockCatalog.State s=at(x,y,z);
            if(!s.geometrySupported()||s.speedFactor()!=1||s.jumpFactor()!=1||s.fluid().kind()==BlockCatalog.FluidKind.UNSUPPORTED)throw new Unknown();return s;}
    }
    private static final class Unknown extends RuntimeException {}
}
