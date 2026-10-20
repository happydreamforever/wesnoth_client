package org.wesnoth.offline;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.io.IOException;

/** Host-Java checks of the exact parser shipped in the APK; not Android execution. */
public final class AlliancePublicProfileHarness {
    private static int passed;
    private static void rejected(byte[] data) throws Exception {
        try { AlliancePublicProfile.parse(data); throw new AssertionError("Invalid profile accepted"); }
        catch (IOException expected) { passed++; }
    }
    private static void rejected(String text) throws Exception { rejected(text.getBytes(StandardCharsets.UTF_8)); }
    public static void main(String[] args) throws Exception {
        byte[] data = Files.readAllBytes(Paths.get(args[0]));
        String text = new String(data, StandardCharsets.UTF_8);
        AlliancePublicProfile profile = AlliancePublicProfile.parse(data);
        if (!profile.address.equals("100.99.99.14") || profile.port != 15001) throw new AssertionError("Wrong profile");
        data[0] = '!';
        if (profile.bytes[0] != '{') throw new AssertionError("Input bytes not copied");
        passed++;
        rejected(text.replaceFirst("\\{", "{\"format\":1,"));
        rejected(text.replaceFirst("\\{", "{\"private_key\":\"secret\","));
        rejected(text + "extra");
        rejected(text.replace("15001", "15001.0"));
        rejected(text.replace("15001", "015001"));
        rejected(text.replace("15001", "70000"));
        rejected(text.replace("100.99.99.14", "100.099.99.14"));
        rejected(text.replace("100.99.99.14", "example.test"));
        rejected(text.replace("100.99.99.14", "100.99.99.15"));
        rejected(text.replace("100.99.99.14", "255.255.255.255"));
        rejected(text.replace("100.99.99.14", "0.0.0.0"));
        rejected(text.replace(profile.fingerprint, "0".repeat(64)));
        rejected(text.replace("wesnoth-alliance-tls", "plaintext"));
        rejected(text.replace("wesnoth-alliance-tls", "\\ud800"));
        rejected(new byte[] {(byte) 0xc0, (byte) 0xaf});
        rejected(new byte[32769]);
        rejected(new byte[0]);
        System.out.println("PUBLIC PROFILE PARSER CHECKS PASSED: " + passed);
    }
}
