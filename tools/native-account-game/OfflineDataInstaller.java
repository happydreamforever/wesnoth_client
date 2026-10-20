package org.wesnoth.offline;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InterruptedIOException;
import java.io.RandomAccessFile;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Enumeration;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

/** Platform-independent importer. The APK supplies the trusted manifest pin. */
public final class OfflineDataInstaller {
    public static final String MANIFEST_SHA256 = "d805fd055b263a5b09cd19576dc9df83c64e47bbcf7821cbb6550b686a74e53c";
    public static final int FILE_COUNT = 26748;
    public static final long DATA_BYTES = 726207542L;
    public static final String OBB_NAME = "main.1192600.org.wesnoth.offline.obb";
    private static final String STAGING = ".wesnoth-gamedata-import";
    private static final String RECEIPT = ".wesnoth-gamedata-ready";
    private static final Pattern RECORD = Pattern.compile("\\[\"([^\"\\\\]+)\",(0|[1-9][0-9]*),\"([0-9a-f]{64})\"\\]");
    private static final Pattern PARTIAL = Pattern.compile("\\.partial-[0-9a-f]{32}");
    private static final int BUFFER_SIZE = 65536;

    public interface Progress {
        void update(String phase, int files, int totalFiles, long bytes, long totalBytes);
    }
    /** Must use lstat/NOFOLLOW to reject links, junctions and special files. */
    public interface FileGuard {
        void check(File file) throws IOException;
        boolean exists(File file) throws IOException;
    }
    public static final class Record {
        public final String path;
        public final long size;
        public final String sha256;
        Record(String path, long size, String sha256) {
            this.path = path; this.size = size; this.sha256 = sha256;
        }
    }
    public static final class Manifest {
        public final String sha256;
        public final long bytes;
        private final LinkedHashMap<String, Record> records;
        private final Set<String> directories;
        Manifest(String sha256, long bytes, LinkedHashMap<String, Record> records, Set<String> directories) {
            this.sha256 = sha256; this.bytes = bytes; this.records = records; this.directories = directories;
        }
        public int count() { return records.size(); }
    }

    private OfflineDataInstaller() {}

    public static Manifest readManifest(InputStream input, String expectedHash, int expectedFiles, long expectedBytes) throws IOException {
        if (!expectedHash.matches("[0-9a-f]{64}") || expectedFiles < 1 || expectedFiles > 100000 || expectedBytes < 0 || expectedBytes > 2147483647L) {
            throw new IOException("Invalid trusted game-data manifest parameters.");
        }
        ByteArrayOutputStream contents = new ByteArrayOutputStream();
        byte[] buffer = new byte[BUFFER_SIZE];
        int count;
        while ((count = input.read(buffer)) != -1) {
            cancelled();
            if (contents.size() + count > 8 * 1024 * 1024) throw new IOException("Game-data manifest is too large.");
            contents.write(buffer, 0, count);
        }
        byte[] data = contents.toByteArray();
        if (!hex(digest().digest(data)).equals(expectedHash)) throw new IOException("The APK game-data manifest does not match its trusted hash.");
        for (byte value : data) {
            int c = value & 255;
            if (c != 10 && (c < 32 || c > 126)) throw new IOException("Game-data manifest is not canonical ASCII JSON lines.");
        }
        if (data.length == 0 || data[data.length - 1] != 10) throw new IOException("Game-data manifest lacks its final newline.");
        String[] lines = new String(data, StandardCharsets.US_ASCII).split("\n", -1);
        if (lines.length != expectedFiles + 1 || !lines[lines.length - 1].isEmpty()) throw new IOException("Game-data manifest file count differs.");
        LinkedHashMap<String, Record> records = new LinkedHashMap<>();
        Set<String> folded = new HashSet<>();
        Set<String> directories = new HashSet<>();
        String previous = null;
        long total = 0;
        for (int index = 0; index < lines.length - 1; index++) {
            Matcher match = RECORD.matcher(lines[index]);
            if (!match.matches()) throw new IOException("Invalid canonical game-data manifest record.");
            String name = match.group(1);
            safeName(name);
            if (previous != null && previous.compareTo(name) >= 0) throw new IOException("Game-data manifest paths are duplicated or not sorted.");
            previous = name;
            if (!folded.add(name.toLowerCase(Locale.ROOT))) throw new IOException("Game-data manifest paths collide by case.");
            long size;
            try { size = Long.parseLong(match.group(2)); }
            catch (NumberFormatException error) { throw new IOException("Invalid game-data file size.", error); }
            if (size > expectedBytes || total > expectedBytes - size) throw new IOException("Game-data manifest size differs.");
            total += size;
            records.put(name, new Record(name, size, match.group(3)));
            for (int slash = name.lastIndexOf('/'); slash >= 0; slash = name.lastIndexOf('/', slash - 1)) {
                directories.add(name.substring(0, slash));
            }
        }
        for (String name : records.keySet()) {
            if (directories.contains(name)) throw new IOException("A game-data file is also used as a directory.");
        }
        if (total != expectedBytes) throw new IOException("Game-data manifest total size differs.");
        return new Manifest(expectedHash, total, records, directories);
    }

    /** Serialized in the application's single process; never deletes user files. */
    public static synchronized File install(File suppliedBase, File obb, Manifest manifest, FileGuard guard, Progress progress) throws IOException {
        cancelled();
        guard.check(suppliedBase);
        File base = suppliedBase.getCanonicalFile();
        if (!base.isDirectory()) throw new IOException("Application storage is not available.");
        File target = child(base, "gamedata");
        File staging = child(base, STAGING);
        File receipt = child(base, RECEIPT);
        byte[] ownership = ownership(manifest);
        if (present(target, guard)) {
            checked(target, base, guard);
            boolean completed = present(receipt, guard);
            if (completed) requireBytes(receipt, base, ownership, guard);
            else {
                // Recover only our own completed rename interrupted before receipt publication.
                checked(staging, base, guard);
                requireBytes(child(staging, "owner"), base, ownership, guard);
                if (present(child(staging, "payload"), guard)) throw new IOException("Both game data and its import payload exist. Preserve them and report the log.");
            }
            verifyTree(target, manifest, false, guard, progress, "Checking game data");
            if (!completed) writeExclusive(receipt, ownership, guard, base);
            return target;
        }
        if (present(receipt, guard)) throw new IOException("The game-data receipt exists but its files are missing. Preserve application storage and report the log.");
        obb = new File(obb.getParentFile().getCanonicalFile(), obb.getName());
        checked(obb, obb.getParentFile(), guard);
        if (!obb.isFile()) throw new IOException("The supplied offline game-data file is missing: " + OBB_NAME);
        progress.update("Checking offline game data", 0, manifest.count(), 0, manifest.bytes);
        inspectZipDirectory(obb, manifest);
        try (ZipFile zip = new ZipFile(obb)) {
            Map<String, ZipEntry> entries = zipEntries(zip, manifest);
            if (present(staging, guard)) {
                checked(staging, base, guard);
                requireBytes(child(staging, "owner"), base, ownership, guard);
                File[] children = list(staging);
                for (File file : children) {
                    checked(file, base, guard);
                    String name = file.getName();
                    if (name.equals("owner")) continue;
                    if (name.equals("payload") && file.isDirectory()) continue;
                    // Retain interrupted temporary files; never erase or reuse their bytes.
                    if (PARTIAL.matcher(name).matches() && file.isFile() && file.length() <= manifest.bytes) continue;
                    throw new IOException("Unexpected file in the owned import directory: " + name);
                }
            } else {
                // Publish the ownership record and its directory together. A killed
                // first attempt can only leave a uniquely named, retained sibling.
                File fresh = child(base, STAGING + "-" + UUID.randomUUID().toString().replace("-", ""));
                mkdir(fresh, base, guard);
                writeExclusive(child(fresh, "owner"), ownership, guard, base);
                if (present(staging, guard) || !fresh.renameTo(staging)) throw new IOException("Could not publish the owned import directory.");
            }
            File payload = child(staging, "payload");
            if (present(payload, guard)) verifyTree(payload, manifest, true, guard, progress, "Checking interrupted import");
            else mkdir(payload, base, guard);
            int done = 0;
            long bytes = 0;
            for (Record record : manifest.records.values()) {
                cancelled();
                File destination = child(payload, record.path);
                if (!present(destination, guard)) {
                    ensureDirectories(destination.getParentFile(), payload, guard);
                    File temporary = child(staging, ".partial-" + UUID.randomUUID().toString().replace("-", ""));
                    if (!temporary.createNewFile()) throw new IOException("Could not create a unique import file.");
                    MessageDigest hash = digest();
                    long written = 0;
                    byte[] buffer = new byte[BUFFER_SIZE];
                    try (InputStream input = new BufferedInputStream(zip.getInputStream(entries.get(record.path)));
                         BufferedOutputStream output = new BufferedOutputStream(new FileOutputStream(temporary))) {
                        int count;
                        while ((count = input.read(buffer)) != -1) {
                            cancelled();
                            if (written > record.size - count) throw new IOException("Game-data entry exceeds its expected size: " + record.path);
                            output.write(buffer, 0, count);
                            hash.update(buffer, 0, count);
                            written += count;
                            if ((written & ((1024 * 1024) - 1)) == 0) progress.update("Installing game data", done, manifest.count(), bytes + written, manifest.bytes);
                        }
                    }
                    if (written != record.size || !hex(hash.digest()).equals(record.sha256)) throw new IOException("Game-data contents do not match: " + record.path);
                    checked(temporary, base, guard);
                    if (present(destination, guard) || !temporary.renameTo(destination)) throw new IOException("Could not publish a verified game-data file: " + record.path);
                }
                done++;
                bytes += record.size;
                progress.update("Installing game data", done, manifest.count(), bytes, manifest.bytes);
            }
            // The resumed files were checked before copying and every new file was
            // hashed while copying. Recheck the final exact file/directory inventory.
            inspectTree(payload, manifest, false, guard);
            cancelled();
            if (present(target, guard) || !payload.renameTo(target)) throw new IOException("Could not complete the game-data installation. Existing files were preserved.");
            writeExclusive(receipt, ownership, guard, base);
            return target;
        }
    }

    private static byte[] ownership(Manifest manifest) {
        return ("WESNOTH_OFFLINE_DATA_V1\n" + manifest.sha256 + "\n" + manifest.count() + "\n" + manifest.bytes + "\n").getBytes(StandardCharsets.US_ASCII);
    }
    private static void verifyTree(File directory, Manifest manifest, boolean partial, FileGuard guard, Progress progress, String phase) throws IOException {
        Set<String> found = inspectTree(directory, manifest, partial, guard);
        int done = 0;
        long bytes = 0;
        for (Record record : manifest.records.values()) {
            if (!found.contains(record.path)) continue;
            cancelled();
            File file = child(directory, record.path);
            if (file.length() != record.size || !hashFile(file).equals(record.sha256)) throw new IOException("Existing game-data file differs; it was preserved: " + record.path);
            done++;
            bytes += record.size;
            progress.update(phase, done, manifest.count(), bytes, manifest.bytes);
        }
    }
    private static Set<String> inspectTree(File directory, Manifest manifest, boolean partial, FileGuard guard) throws IOException {
        checked(directory, directory.getParentFile(), guard);
        if (!directory.isDirectory()) throw new IOException("Expected a game-data directory.");
        Set<String> found = new HashSet<>();
        List<File> pending = new ArrayList<>();
        pending.add(directory);
        while (!pending.isEmpty()) {
            cancelled();
            File current = pending.remove(pending.size() - 1);
            for (File file : list(current)) {
                checked(file, directory, guard);
                String name = file.getAbsolutePath().substring(directory.getAbsolutePath().length() + 1).replace(File.separatorChar, '/');
                if (file.isDirectory()) {
                    if (!manifest.directories.contains(name)) throw new IOException("Unexpected game-data directory: " + name);
                    pending.add(file);
                } else if (file.isFile()) {
                    if (!manifest.records.containsKey(name) || !found.add(name)) throw new IOException("Unexpected game-data file: " + name);
                } else throw new IOException("Game data contains a special file: " + name);
            }
        }
        if (!partial && found.size() != manifest.count()) throw new IOException("Installed game data is incomplete.");
        return found;
    }
    private static Map<String, ZipEntry> zipEntries(ZipFile zip, Manifest manifest) throws IOException {
        Map<String, ZipEntry> entries = new LinkedHashMap<>();
        Enumeration<? extends ZipEntry> input = zip.entries();
        while (input.hasMoreElements()) {
            ZipEntry entry = input.nextElement();
            Record expected = manifest.records.get(entry.getName());
            if (entry.isDirectory() || expected == null || entries.put(entry.getName(), entry) != null || entry.getMethod() != ZipEntry.STORED || entry.getSize() != expected.size || entry.getCompressedSize() != expected.size) {
                throw new IOException("Offline archive contains an unexpected or duplicate entry: " + entry.getName());
            }
        }
        if (entries.size() != manifest.count()) throw new IOException("Offline archive file count differs.");
        return entries;
    }

    /** Read Unix file modes unavailable through Android's ZipEntry API. ZIP64 is unnecessary for this fixed dataset. */
    private static void inspectZipDirectory(File obb, Manifest manifest) throws IOException {
        try (RandomAccessFile input = new RandomAccessFile(obb, "r")) {
            long length = input.length();
            if (length < 22 || length > manifest.bytes + manifest.count() * 2048L + 65557L) throw new IOException("Offline archive size is outside the expected bounds.");
            byte[] end = new byte[(int) Math.min(length, 65557L)];
            input.seek(length - end.length);
            input.readFully(end);
            int offset = -1;
            for (int index = end.length - 22; index >= 0; index--) {
                if (u32(end, index) == 0x06054b50L && index + 22 + u16(end, index + 20) == end.length) { offset = index; break; }
            }
            if (offset < 0 || u16(end, offset + 4) != 0 || u16(end, offset + 6) != 0 || u16(end, offset + 20) != 0 ||
                u16(end, offset + 8) != manifest.count() || u16(end, offset + 10) != manifest.count()) throw new IOException("Unsupported offline archive directory.");
            long size = u32(end, offset + 12);
            long start = u32(end, offset + 16);
            long finish = length - end.length + offset;
            if (start == 0xffffffffL || size == 0xffffffffL || start + size != finish) throw new IOException("Offline archive central directory is invalid.");
            input.seek(start);
            Set<String> names = new HashSet<>();
            byte[] header = new byte[46];
            for (int index = 0; index < manifest.count(); index++) {
                cancelled();
                if (input.getFilePointer() + 46 > finish) throw new IOException("Truncated offline archive directory.");
                input.readFully(header);
                int nameSize = u16(header, 28), extraSize = u16(header, 30), commentSize = u16(header, 32);
                long mode = u32(header, 38) >>> 16;
                int flags = u16(header, 8);
                if (u32(header, 0) != 0x02014b50L || (u16(header, 4) >>> 8) != 3 || u16(header, 6) > 20 || (mode & 0170000) != 0100000 || (u32(header, 38) & 0x10) != 0 ||
                    (flags & ~0x800) != 0 || u16(header, 10) != 0 || u16(header, 34) != 0 || nameSize < 1 || nameSize > 512 || extraSize != 0 || commentSize != 0 ||
                    input.getFilePointer() + nameSize + extraSize + commentSize > finish) throw new IOException("Offline archive contains a link, special file or unsupported entry.");
                byte[] nameBytes = new byte[nameSize];
                input.readFully(nameBytes);
                for (byte value : nameBytes) if ((value & 255) < 32 || (value & 255) > 126) throw new IOException("Offline archive path is not canonical ASCII.");
                String name = new String(nameBytes, StandardCharsets.US_ASCII);
                safeName(name);
                Record record = manifest.records.get(name);
                if (record == null || !names.add(name) || u32(header, 20) != record.size || u32(header, 24) != record.size || u32(header, 42) >= start) throw new IOException("Offline archive entry does not match the APK manifest: " + name);
                input.seek(input.getFilePointer() + extraSize + commentSize);
            }
            if (input.getFilePointer() != finish || names.size() != manifest.count()) throw new IOException("Offline archive directory contains unexpected data.");
        }
    }
    private static int u16(byte[] data, int offset) { return (data[offset] & 255) | ((data[offset + 1] & 255) << 8); }
    private static long u32(byte[] data, int offset) { return (u16(data, offset) & 0xffffL) | ((long) u16(data, offset + 2) << 16); }
    private static void safeName(String name) throws IOException {
        if (name.length() == 0 || name.length() > 512 || name.startsWith("/") || name.endsWith("/") || name.indexOf('\\') >= 0) throw new IOException("Unsafe game-data path.");
        for (String part : name.split("/", -1)) {
            if (part.isEmpty() || part.equals(".") || part.equals("..") || part.endsWith(".") || part.endsWith(" ") ||
                part.matches("(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\\..*)?")) throw new IOException("Unsafe game-data path component.");
            for (int index = 0; index < part.length(); index++) {
                char value = part.charAt(index);
                if (value < 32 || value > 126 || "<>:\"|?*".indexOf(value) >= 0) throw new IOException("Unsupported game-data filename.");
            }
        }
    }
    private static File child(File directory, String name) { return new File(directory, name); }
    private static boolean present(File file, FileGuard guard) throws IOException {
        if (!guard.exists(file)) return false;
        guard.check(file);
        return true;
    }
    private static void checked(File file, File root, FileGuard guard) throws IOException {
        guard.check(file);
        String absolute = file.getAbsolutePath();
        String canonical = file.getCanonicalPath();
        String base = root.getCanonicalPath();
        if (!canonical.equals(absolute) || !(canonical.equals(base) || canonical.startsWith(base + File.separator))) throw new IOException("A game-data path is redirected outside its owned directory.");
    }
    private static void ensureDirectories(File directory, File root, FileGuard guard) throws IOException {
        if (directory.equals(root)) { checked(root, root, guard); return; }
        if (!present(directory, guard)) {
            ensureDirectories(directory.getParentFile(), root, guard);
            mkdir(directory, root, guard);
        } else {
            checked(directory, root, guard);
            if (!directory.isDirectory()) throw new IOException("Expected an import directory.");
        }
    }
    private static void mkdir(File directory, File root, FileGuard guard) throws IOException {
        if (!directory.mkdir()) throw new IOException("Could not create owned game-data directory: " + directory.getName());
        checked(directory, root, guard);
    }
    private static File[] list(File directory) throws IOException {
        File[] files = directory.listFiles();
        if (files == null) throw new IOException("Cannot list game-data directory: " + directory.getName());
        return files;
    }
    private static void writeExclusive(File file, byte[] bytes, FileGuard guard, File root) throws IOException {
        if (present(file, guard)) throw new IOException("Refusing to replace an existing game-data ownership record.");
        File temporary = child(file.getParentFile(), ".partial-" + UUID.randomUUID().toString().replace("-", ""));
        if (!temporary.createNewFile()) throw new IOException("Could not create a unique ownership record.");
        checked(temporary, root, guard);
        try (FileOutputStream stream = new FileOutputStream(temporary)) { stream.write(bytes); stream.getFD().sync(); }
        if (present(file, guard) || !temporary.renameTo(file)) throw new IOException("Could not publish game-data ownership. Existing files were preserved.");
    }
    private static void requireBytes(File file, File root, byte[] expected, FileGuard guard) throws IOException {
        checked(file, root, guard);
        if (!file.isFile() || file.length() != expected.length) throw new IOException("Unrecognized game-data ownership record. Existing files were preserved.");
        byte[] actual = new byte[expected.length];
        try (InputStream input = new FileInputStream(file)) {
            int offset = 0;
            while (offset < actual.length) {
                int read = input.read(actual, offset, actual.length - offset);
                if (read < 0) throw new IOException("Incomplete game-data ownership record.");
                offset += read;
            }
            if (input.read() != -1 || !Arrays.equals(actual, expected)) throw new IOException("Game-data ownership record differs. Existing files were preserved.");
        }
    }
    private static String hashFile(File file) throws IOException {
        MessageDigest hash = digest();
        try (InputStream input = new BufferedInputStream(new FileInputStream(file))) {
            byte[] buffer = new byte[BUFFER_SIZE];
            int count;
            while ((count = input.read(buffer)) != -1) { cancelled(); hash.update(buffer, 0, count); }
        }
        return hex(hash.digest());
    }
    private static MessageDigest digest() {
        try { return MessageDigest.getInstance("SHA-256"); }
        catch (NoSuchAlgorithmException error) { throw new IllegalStateException("Required SHA-256 is unavailable.", error); }
    }
    private static String hex(byte[] bytes) {
        char[] chars = new char[bytes.length * 2];
        char[] digits = "0123456789abcdef".toCharArray();
        for (int index = 0; index < bytes.length; index++) { chars[index * 2] = digits[(bytes[index] & 255) >>> 4]; chars[index * 2 + 1] = digits[bytes[index] & 15]; }
        return new String(chars);
    }
    private static void cancelled() throws InterruptedIOException {
        if (Thread.currentThread().isInterrupted()) throw new InterruptedIOException("Game-data installation interrupted; completed files were preserved.");
    }
}
