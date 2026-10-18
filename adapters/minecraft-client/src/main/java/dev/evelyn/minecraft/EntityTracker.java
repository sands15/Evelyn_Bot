package dev.evelyn.minecraft;

import java.io.IOException;
import java.util.HashMap;
import java.util.Map;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.Set;

/** Last-known server-tracked subset, never a claim of complete neighborhood safety. */
final class EntityTracker {
    record Point(double x, double y, double z) {}
    record Entity(int type, Point point, long epoch, Float health, boolean geometryKnown, boolean dead) {
        Entity move(Point p){return new Entity(type,p,epoch,health,geometryKnown,dead);}
    }
    private final BlockCatalog catalog;
    private Map<Integer, Entity> live = Map.of(), published = Map.of();
    private boolean bundle;
    private long event, publishedEvent, danger, publishedDanger, spawnEpoch;
    private Map<Integer,Entity> deaths=Map.of(),publishedDeaths=Map.of();
    private record Danger(long epoch,long sequence,long observedAt) {}
    private Danger latest,previous,publishedLatest,publishedPrevious;
    EntityTracker(BlockCatalog catalog) { this.catalog = catalog; }
    void beginBundle() { if (bundle) throw new IllegalStateException(); bundle = true; }
    void endBundle() { if (!bundle) throw new IllegalStateException(); bundle = false; publish(); }
    void clear() { live = Map.of(); deaths=Map.of();latest=null;previous=null; bundle = false; event++; publish(); }
    long event() { return publishedEvent; }
    long dangerEpoch() { return publishedDanger; }
    int count() { return published.size(); }
    Entity target(int id){return published.containsKey(id)?published.get(id):publishedDeaths.get(id);}
    Map<Integer,Entity> targets(){return published;}
    private Danger dangerOther(long exceptEpoch){return publishedLatest!=null&&publishedLatest.epoch==exceptEpoch?publishedPrevious:publishedLatest;}
    long dangerEpochExcept(long exceptEpoch){Danger d=dangerOther(exceptEpoch);return d==null?0:d.sequence;}
    boolean recentDangerExcept(long exceptEpoch,long now){Danger d=dangerOther(exceptEpoch);return d!=null&&now-d.observedAt<1_000_000_000L;}
    boolean nearExcept(Point self,double radius,int except,long epoch){
        return nearExcept(self,radius,except,epoch,true);
    }
    boolean nearExcept(Point self,double radius,int except,long epoch,boolean monstersOnly){
        if(self==null)return true;
        return published.entrySet().stream().anyMatch(e->!(e.getKey()==except&&e.getValue().epoch==epoch&&e.getValue().geometryKnown)
            &&(!monstersOnly||catalog.lookupEntityType(e.getValue().type).monster())&&distance(e.getValue().point,self)<=radius*radius);
    }
    boolean near(Point self, double radius, boolean monstersOnly) {
        if (self == null) return true;
        return published.values().stream().anyMatch(e -> (!monstersOnly || catalog.lookupEntityType(e.type()).monster())
                && distance(e.point(), self) <= radius * radius);
    }
    boolean nearSegment(Point from,Point to,double radius) {
        return from==null||to==null||published.values().stream().anyMatch(e->segmentDistance(from,to,e.point())<=radius*radius);
    }
    private void publish() { published = live; publishedDeaths=deaths; publishedEvent = event; publishedDanger = danger;publishedLatest=latest;publishedPrevious=previous; }
    private static double distance(Point a, Point b) {
        return Math.pow(a.x - b.x, 2) + Math.pow(a.y - b.y, 2) + Math.pow(a.z - b.z, 2);
    }
    private void approach(Entity old, Entity next, Point self) {
        if (self == null || !catalog.lookupEntityType(next.type()).monster()) return;
        Point a = old == null ? next.point() : old.point(), b = next.point();
        if(segmentDistance(a,b,self)<=64){danger++;if(latest!=null&&latest.epoch!=next.epoch)previous=latest;latest=new Danger(next.epoch,danger,System.nanoTime());}
    }
    private static double segmentDistance(Point a,Point b,Point self) {
        double vx = b.x-a.x, vy=b.y-a.y, vz=b.z-a.z, length=vx*vx+vy*vy+vz*vz;
        double t = length == 0 ? 0 : Math.max(0, Math.min(1, ((self.x-a.x)*vx+(self.y-a.y)*vy+(self.z-a.z)*vz)/length));
        return distance(new Point(a.x+t*vx, a.y+t*vy, a.z+t*vz), self);
    }
    void accept(int packet, VanillaConnection.Cursor c, Point self) throws IOException {
        Map<Integer, Entity> next = new HashMap<>(live);
        long beforeDanger = danger;
        Danger beforeLatest=latest,beforePrevious=previous;
        long nextEpoch=spawnEpoch;
        Map<Integer,Entity> nextDeaths=new LinkedHashMap<>(deaths);
        try {
            if (packet == 0x4e) {
                int count = c.count(1024); for (int i=0;i<count;i++) next.remove(c.count(Integer.MAX_VALUE));
            } else {
                int id = c.count(Integer.MAX_VALUE); Entity old = next.get(id), changed = old;
                switch (packet) {
                    case 0x01 -> {
                        c.data.readLong(); c.data.readLong(); int type = c.count(catalog.entityTypeCount()-1);
                        Point point = point(c); lpVector(c); c.skip(3); c.vi();
                        if (old != null || next.size() >= 1024) throw invalid();
                        if(++nextEpoch>Protocol.MAX_SEQUENCE)throw invalid();
                        changed = new Entity(type, point,nextEpoch,null,supported(type),false);nextDeaths.remove(id); approach(null, changed, self);
                    }
                    case 0x23 -> {
                        int type = c.count(1), count = type == 0 ? 1 : c.count(256); if (count == 0) throw invalid();
                        for (int i=0;i<count;i++) {
                            Point point = point(c); if (type == 1 && c.count(200) == 0) throw invalid();
                            if (changed != null) { Entity update = changed.move(point); approach(changed, update, self); changed=update; }
                        }
                        c.finiteFloat(); c.finiteFloat(); c.bool();
                    }
                    case 0x36, 0x37 -> {
                        int packed = c.count(513), steps = packed >>> 1;
                        for (int i=0;i<Math.max(1,steps);i++) {
                            if (steps != 0 && c.count(200) == 0) throw invalid();
                            short dx=c.data.readShort(),dy=c.data.readShort(),dz=c.data.readShort();
                            if (changed != null) {
                                Point p=changed.point; Entity update = changed.move(checked(delta(p.x,dx),delta(p.y,dy),delta(p.z,dz)));
                                approach(changed, update, self); changed=update;
                            }
                        }
                        if (packet == 0x37) c.skip(2);
                    }
                    case 0x39 -> { c.bool(); c.skip(2); }
                    case 0x80 -> {
                        Point p = point(c); c.finiteDouble(); c.finiteDouble(); c.finiteDouble();
                        c.finiteFloat(); c.finiteFloat(); int flags=c.data.readInt(); c.bool();
                        if ((flags & ~0x1ff) != 0) throw invalid();
                        if (changed != null) {
                            Point base=changed.point; Entity update = changed.move(checked(p.x+((flags&1)!=0?base.x:0),p.y+((flags&2)!=0?base.y:0),p.z+((flags&4)!=0?base.z:0)));
                            approach(changed,update,self); changed=update;
                        }
                    }
                    default -> throw invalid();
                }
                if (changed != null) next.put(id, changed);
            }
            c.end(); live=Map.copyOf(next);spawnEpoch=nextEpoch;deaths=Map.copyOf(nextDeaths); event++; if (!bundle) publish();
        } catch (IOException | RuntimeException failure) { danger=beforeDanger;latest=beforeLatest;previous=beforePrevious; throw failure; }
    }
    private boolean supported(int type){return Set.of("minecraft:zombie","minecraft:skeleton","minecraft:creeper","minecraft:spider").contains(catalog.lookupEntityType(type).name());}
    void invalidate(int id){Entity old=live.get(id);if(old!=null)replace(id,new Entity(old.type,old.point,old.epoch,old.health,false,old.dead));}
    private void replace(int id,Entity value){
        Map<Integer,Entity> next=new HashMap<>(live);next.put(id,value);live=Map.copyOf(next);
        if(value.dead){Map<Integer,Entity> d=new LinkedHashMap<>(deaths);d.remove(id);d.put(id,value);if(d.size()>32)d.remove(d.keySet().iterator().next());deaths=java.util.Collections.unmodifiableMap(d);}
        event++;if(!bundle)publish();
    }
    void entityEvent(VanillaConnection.Cursor c)throws IOException{
        int id=c.data.readInt();int event=c.data.readByte();if(id<0)throw invalid();c.end();Entity old=live.get(id);
        if(event==3&&old!=null&&supported(old.type))replace(id,new Entity(old.type,old.point,old.epoch,0f,old.geometryKnown,true));
    }
    void metadata(VanillaConnection.Cursor c)throws IOException {
        metadata(c.count(Integer.MAX_VALUE),c);
    }
    void metadata(int id,VanillaConnection.Cursor c)throws IOException {
        Entity old=live.get(id);
        if(old==null||!supported(old.type)){c.discard(1048576);return;}
        String name=catalog.lookupEntityType(old.type).name();Set<Integer> seen=new HashSet<>();boolean valid=old.geometryKnown;Float health=old.health;
        try {
            while(true){int key=c.data.readUnsignedByte();if(key==255)break;if(!seen.add(key))throw invalid();int serializer=c.count(43);
                if(key>18||key>=16&&!specific(name,key,serializer))throw new UnsupportedMetadata();
                int expected=key>=16?serializer:switch(key){case 0,8,15->0;case 1,7,12,13->1;case 2->6;case 3,4,5,11->8;case 6->20;case 9->3;case 10->17;default->11;};
                if(serializer!=expected)throw new UnsupportedMetadata();
                switch(key){
                    case 0,8->valid&=c.data.readUnsignedByte()==0;
                    case 1,12,13->c.count(Integer.MAX_VALUE);
                    case 2->{if(c.bool())throw new UnsupportedMetadata();}
                    case 3,4->c.bool();
                    case 5,11->valid&=!c.bool();
                    case 6->valid&=c.count(17)==0;
                    case 7->valid&=c.count(Integer.MAX_VALUE)==0;
                    case 9->{float hp=c.finiteFloat();if(hp<0||hp>1024)throw invalid();health=hp;}
                    case 10->{if(c.count(1024)!=0)throw new UnsupportedMetadata();}
                    case 14->{if(c.bool())throw new UnsupportedMetadata();}
                    case 15->valid&=(c.data.readUnsignedByte()&~7)==0;
                    default->{if(serializer==8)valid&=!c.bool();else if(serializer==0)valid&=c.data.readByte()==0;
                        else {int value=c.vi();valid&=name.equals("minecraft:creeper")?value==-1||value==0:value==0;}}
                }
            }
            c.end();replace(id,new Entity(old.type,old.point,old.epoch,old.dead?Float.valueOf(0f):health,valid,old.dead||health!=null&&health<=0));
        }catch(UnsupportedMetadata unknown){c.discard(1048576);invalidate(id);}
        catch(IOException|RuntimeException malformed){invalidate(id);throw malformed;}
    }
    private static boolean specific(String name,int key,int serializer){
        return switch(name){
            case "minecraft:zombie"->key==17?serializer==1:(key==16||key==18)&&serializer==8;
            case "minecraft:creeper"->key==16?serializer==1:(key==17||key==18)&&serializer==8;
            case "minecraft:skeleton"->key==16&&serializer==8;
            case "minecraft:spider"->key==16&&serializer==0;
            default->false;
        };
    }
    private static final class UnsupportedMetadata extends IOException {}
    static double delta(double base, short value) { return value == 0 ? base : (Math.round(base*4096)+(long)value)/4096.0; }
    static Point point(VanillaConnection.Cursor c) throws IOException { return checked(c.finiteDouble(),c.finiteDouble(),c.finiteDouble()); }
    private static Point checked(double x,double y,double z) throws IOException {
        if (!Double.isFinite(x)||!Double.isFinite(y)||!Double.isFinite(z)||Math.abs(x)>30000000||Math.abs(z)>30000000||Math.abs(y)>2048) throw invalid();
        return new Point(x,y,z);
    }
    static Point lpVector(VanillaConnection.Cursor c) throws IOException {
        int b=c.data.readUnsignedByte(); if (b==0) return new Point(0,0,0);
        int second=c.data.readUnsignedByte(); long packed=(Integer.toUnsignedLong(c.data.readInt())<<16)|((long)second<<8)|b;
        long scale=b&3; if ((b&4)!=0) scale|=Integer.toUnsignedLong(c.vi())<<2;
        double x=unpack(packed,3,scale), y=unpack(packed,18,scale), z=unpack(packed,33,scale);
        return new Point(x,y,z);
    }
    private static double unpack(long packed,int shift,long scale) { return (Math.min((packed>>>shift)&32767,32766)*2.0/32766-1)*scale; }
    private static IOException invalid() { return new VanillaConnection.Failure("invalid_entity_packet"); }
}
