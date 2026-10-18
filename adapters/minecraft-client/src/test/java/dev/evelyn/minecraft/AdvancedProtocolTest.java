package dev.evelyn.minecraft;

/** v4 is explicit; v3 remains an integer-height, two-action contract. */
public final class AdvancedProtocolTest {
    private static int checks;
    private static String command(String action,String fields) {
        return "{\"v\":4,\"type\":\"command\",\"session\":\"s\",\"world\":\"w\",\"request_id\":\"r\",\"seq\":1,\"action\":\""+action+"\",\"observation_seq\":1,\"max_age_ms\":500,\"run_ms\":30000"+fields+"}";
    }
    private static void good(String text) { Protocol.decode(text,4);checks++; }
    private static void bad(String text,int version) { try {Protocol.decode(text,version);}catch(IllegalArgumentException expected){checks++;return;}throw new AssertionError("accepted invalid v"+version+" command"); }
    public static void main(String[] args) {
        String jump=command("jump","");good(jump);
        String move=command("navigate",",\"target_x\":0,\"target_y\":64.5,\"target_z\":-2");good(move);
        good(move.replace("64.5","-0.5"));good(move.replace("64.5","64.0"));
        good(command("attack",",\"target_id\":0,\"target_epoch\":1"));
        good(command("attack",",\"target_id\":2147483647,\"target_epoch\":2147483647"));
        good(command("look",",\"yaw\":90.5,\"pitch\":-90"));
        good("{\"v\":4,\"type\":\"off\",\"session\":\"s\",\"world\":\"w\",\"request_id\":\"r\"}");
        for(String height:new String[]{"64.25","2048.5","-2048.5","NaN","1e999","null","true","\"64.5\""})bad(move.replace("64.5",height),4);
        for(String id:new String[]{"-1","2147483648","1.5","true","null","\"1\""})bad(command("attack",",\"target_id\":"+id+",\"target_epoch\":1"),4);
        for(String epoch:new String[]{"0","-1","1.5","2147483648"})bad(command("attack",",\"target_id\":1,\"target_epoch\":"+epoch),4);
        bad(command("attack",",\"target_id\":1"),4);bad(command("jump",",\"yaw\":0"),4);
        bad(command("swing","") ,4);bad(command("navigate",",\"target_x\":0.5,\"target_y\":64,\"target_z\":0"),4);
        for(String text:new String[]{jump,move,command("attack",",\"target_id\":1,\"target_epoch\":1")}) {
            bad(text,3);bad(text.replace("\"v\":4","\"v\":3"),3);
        }
        System.out.println("ADVANCED_PROTOCOL_PASS "+checks);
    }
}
