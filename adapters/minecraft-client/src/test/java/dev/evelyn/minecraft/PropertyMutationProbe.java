package dev.evelyn.minecraft;

/** Owned JVM-only mutation: the product and its old expected table agree wrongly. */
public final class PropertyMutationProbe {
    public static void main(String[] args)throws Exception{
        var field=InventoryState.class.getDeclaredField("MENU_PROPERTIES");
        field.setAccessible(true);
        ((int[])field.get(null))[11]=3;
        MenuPropertiesTest.COUNT[11]=3;
        MenuPropertiesTest.main(new String[0]);
    }
}
