package dev.evelyn.minecraft;

import java.util.Arrays;
import java.util.HexFormat;

public final class PlayerHandSwapServerTest {
    static int checks;
    static void check(boolean value,String name){checks++;if(!value)throw new AssertionError(name);}
    public static void main(String[] args)throws Exception{
        byte[] swap=HexFormat.of().parseHex("0c290700000000000000000000");
        for(boolean paused:new boolean[]{false,true}){
            PlayerHandSwapServer server=new PlayerHandSwapServer();if(paused)server.pause();
            var replies=server.receive(swap);
            check(replies.size()==(paused?0:2),"only actual slot deltas or no response");
            check(server.report().equals("PLAYER_SWAP_SERVER 5 2 3 1 8 1 "+paused),"actual swap even when no response");
            if(!paused){
                check(Arrays.equals(replies.get(0),HexFormat.of().parseHex("140004002403020000")),"independent main delta literal");
                check(Arrays.equals(replies.get(1),HexFormat.of().parseHex("140005002d08010000")),"independent offhand delta literal");
            }
            try{server.receive(swap);throw new AssertionError("second swap accepted");}catch(java.io.IOException expected){checks++;}
            check(server.report().equals("PLAYER_SWAP_SERVER 5 2 3 1 8 1 "+paused),"duplicate cannot mutate");
        }
        for(int n=0;n<swap.length;n++){
            PlayerHandSwapServer server=new PlayerHandSwapServer();byte[] bad=swap.clone();bad[n]^=1;
            try{server.receive(bad);throw new AssertionError("malformed frame accepted "+n);}catch(java.io.IOException expected){checks++;}
            check(server.report().equals("PLAYER_SWAP_SERVER 3 1 8 2 3 0 false"),"invalid frame cannot mutate");
        }
        System.out.println("PlayerHandSwapServerTest PASS "+checks);
    }
}
