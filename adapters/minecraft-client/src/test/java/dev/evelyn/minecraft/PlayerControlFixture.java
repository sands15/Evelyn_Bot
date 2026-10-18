package dev.evelyn.minecraft;

/** Actual v5 Java/Python transport around the owned in-memory server receiver. */
public final class PlayerControlFixture {
    public static void main(String[] args){
        try{ControlFixture.runLoopback(true,true);System.out.println("PLAYER_PASS");}
        catch(Exception|AssertionError failure){System.out.println("PLAYER_FAILED");System.exit(2);}
    }
}
