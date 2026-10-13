package org.wesnoth.offline;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.LinkOption;

/** Host-JVM fixture entry point. Not packaged in the Android application. */
public final class OfflineDataInstallerHarness {
    public static void main(String[] args) throws Exception {
        if (args.length != 7) throw new IllegalArgumentException("manifest sha files bytes base obb mode");
        File manifestFile = new File(args[0]);
        File base = new File(args[4]).getCanonicalFile();
        String mode = args[6];
        OfflineDataInstaller.Manifest manifest;
        try (FileInputStream input = new FileInputStream(manifestFile)) {
            manifest = OfflineDataInstaller.readManifest(input, args[1], Integer.parseInt(args[2]), Long.parseLong(args[3]));
        }
        OfflineDataInstaller.FileGuard guard = new OfflineDataInstaller.FileGuard() {
            public boolean exists(File file) { return Files.exists(file.toPath(), LinkOption.NOFOLLOW_LINKS); }
            public void check(File file) throws IOException {
                if (Files.isSymbolicLink(file.toPath()) || !(Files.isRegularFile(file.toPath(), LinkOption.NOFOLLOW_LINKS) || Files.isDirectory(file.toPath(), LinkOption.NOFOLLOW_LINKS))) throw new IOException("Host fixture rejected link or special file: " + file);
                if (mode.equals("block-receipt") && file.getParentFile().equals(base) && file.getName().startsWith(".partial-")) throw new IOException("Injected interruption before receipt publication.");
            }
        };
        long began = System.nanoTime();
        final long[] progressTime = {0};
        try {
            File result = OfflineDataInstaller.install(base, new File(args[5]), manifest, guard, (phase, files, total, bytes, totalBytes) -> {
                if (mode.equals("interrupt") && phase.equals("Installing game data") && bytes >= 1048576L) Thread.currentThread().interrupt();
                long now = System.nanoTime();
                if (now - progressTime[0] > 5000000000L || files == total) {
                    progressTime[0] = now;
                    System.out.println("PROGRESS " + phase + " files=" + files + "/" + total + " bytes=" + bytes + "/" + totalBytes);
                }
            });
            System.out.println("HOST IMPORT PASS files=" + manifest.count() + " bytes=" + manifest.bytes + " seconds=" + ((System.nanoTime() - began) / 1000000000.0));
            System.out.println("DATA " + result);
        } catch (IOException error) {
            Thread.interrupted();
            System.err.println("HOST IMPORT BLOCKED: " + error.getMessage());
            System.exit(2);
        }
    }
}
