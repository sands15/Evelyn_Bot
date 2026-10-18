package dev.evelyn.minecraft;

/** Typed exact-revision hand swap is opt-in v5; no raw action or extra slot override. */
public final class HandSwapProtocolTest {
    static int checks;
    static String command(String revision){return "{\"v\":5,\"type\":\"command\",\"session\":\"s\",\"world\":\"w\",\"request_id\":\"r\",\"seq\":1,\"action\":\"swap_hands\",\"observation_seq\":1,\"max_age_ms\":1000,\"run_ms\":1000,\"inventory_revision\":"+revision+"}";}
    static void bad(String value,int version){try{Protocol.decode(value,version);}catch(IllegalArgumentException expected){checks++;return;}throw new AssertionError("invalid hand swap admitted");}
    public static void main(String[] args){
        Protocol.decode(command("0"),5);checks++;Protocol.decode(command("2147483647"),5);checks++;
        for(String value:new String[]{"-1","2147483648","1.0","true","null","\"1\""})bad(command(value),5);
        for(String extra:new String[]{"slot","hand","payload","amount","page_start"})bad(command("1").replace("\"inventory_revision\":1","\"inventory_revision\":1,\""+extra+"\":1"),5);
        bad(command("1").replace(",\"inventory_revision\":1",""),5);
        bad(command("1").replace("\"inventory_revision\":1","\"inventory_revision\":1,\"inventory_revision\":1"),5);
        bad(command("1").replace("\"v\":5","\"v\":4"),4);
        System.out.println("HandSwapProtocolTest PASS "+checks);
    }
}
