package dev.evelyn.minecraft;

/** v5 inventory paging remains typed, bounded and opt-in. */
public final class PlayerProtocolTest {
    private static int checks;
    private static String page(String start,String revision){return "{\"v\":5,\"type\":\"command\",\"session\":\"s\",\"world\":\"w\",\"request_id\":\"r\",\"seq\":1,\"action\":\"inventory_page\",\"observation_seq\":1,\"max_age_ms\":1000,\"run_ms\":1000,\"page_start\":"+start+",\"inventory_revision\":"+revision+"}";}
    private static void bad(String value,int version){try{Protocol.decode(value,version);}catch(IllegalArgumentException expected){checks++;return;}throw new AssertionError("invalid inventory command admitted");}
    public static void main(String[] args){
        Protocol.decode(page("0","0"),5);checks++;
        Protocol.decode(page("4080","2147483647"),5);checks++;
        for(String start:new String[]{"1","-16","4096","true","null","1.0","\"16\""})bad(page(start,"1"),5);
        for(String revision:new String[]{"-1","2147483648","true","null","1.5"})bad(page("0",revision),5);
        bad(page("0","1").replace(",\"inventory_revision\":1",""),5);
        bad(page("0","1").replace("\"page_start\":0","\"page_start\":0,\"page_start\":0"),5);
        bad(page("0","1").replace("\"action\":\"inventory_page\"","\"action\":\"raw_packet\""),5);
        bad(page("0","1"),4);bad(page("0","1").replace("\"v\":5","\"v\":4"),4);
        String transfer=page("0","1").replace("\"inventory_page\"","\"inventory_transfer\"").replace("\"page_start\":0","\"source_slot\":36,\"destination_slot\":37,\"amount\":3");
        Protocol.decode(transfer,5);checks++;
        for(String field:new String[]{"source_slot","destination_slot","amount"}){
            for(String invalid:new String[]{"-1","true","1.0","4096"})bad(transfer.replace("\""+field+"\":"+(field.equals("source_slot")?36:field.equals("destination_slot")?37:3),"\""+field+"\":"+invalid),5);
        }
        bad(transfer.replace("\"amount\":3","\"amount\":0"),5);
        bad(transfer.replace("\"v\":5","\"v\":4"),4);
        var options=VanillaBot.arguments(new String[]{"00000000-0000-4000-8000-000000000001","--player-control","ws://127.0.0.1:12345/minecraft"});
        if(!options.playerControlMode()||!options.controlMode()||!options.advancedControlMode())throw new AssertionError("explicit player mode missing");checks++;
        var legacy=VanillaBot.arguments(new String[]{"00000000-0000-4000-8000-000000000001","--advanced-control","ws://127.0.0.1:12345/minecraft"});
        if(legacy.playerControlMode()||!legacy.advancedControlMode())throw new AssertionError("implicit protocol upgrade");checks++;
        System.out.println("PLAYER_PROTOCOL_PASS "+checks);
    }
}
