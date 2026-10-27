package org.wesnoth.offline;

import android.content.Context;
import android.util.AtomicFile;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.security.MessageDigest;
import java.util.Arrays;
import org.json.JSONArray;
import org.json.JSONObject;

/** Signed APK public configuration is authoritative. Legacy imported files remain untouched. */
final class BundledStartup {
    static final String PROFILE_NAME = "alliance-bundled-profile.json";
    private static final String PROFILE_SHA256 = "@PUBLIC_PROFILE_SHA256@";
    private static final String UI_SHA256 = "@UI_MANIFEST_SHA256@";
    private BundledStartup() {}

    static byte[] read(InputStream input, int limit) throws IOException {
        try (InputStream owned = input; ByteArrayOutputStream result = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[8192]; int count;
            while ((count = owned.read(buffer)) != -1) {
                if (result.size() > limit - count) throw new IOException("Bundled asset exceeds its bound.");
                result.write(buffer, 0, count);
            }
            return result.toByteArray();
        }
    }
    static String sha256(byte[] data) throws IOException {
        try {
            StringBuilder result = new StringBuilder();
            for (byte value : MessageDigest.getInstance("SHA-256").digest(data))
                result.append(String.format(java.util.Locale.ROOT, "%02x", value & 255));
            return result.toString();
        } catch (java.security.NoSuchAlgorithmException error) { throw new IOException("SHA-256 is unavailable."); }
    }
    static File child(File base, String name) throws IOException {
        File value = new File(base, name);
        if (!value.getCanonicalPath().equals(value.getAbsolutePath()) ||
                !value.getCanonicalPath().startsWith(base.getCanonicalPath() + File.separator))
            throw new IOException("Owned startup storage contains an unexpected path.");
        return value;
    }
    static File prepareProfile(Context context) throws IOException {
        byte[] data = read(context.getAssets().open("alliance-public-profile.json"), AlliancePublicProfile.MAX_BYTES);
        if (!sha256(data).equals(PROFILE_SHA256)) throw new IOException("The bundled server configuration is invalid.");
        AlliancePublicProfile.parse(data);
        File target = child(context.getFilesDir().getCanonicalFile(), PROFILE_NAME);
        AtomicFile atomic = new AtomicFile(target);
        if (target.exists()) {
            try (InputStream previous = atomic.openRead()) {
                if (Arrays.equals(read(previous, AlliancePublicProfile.MAX_BYTES), data)) return target;
            }
        }
        FileOutputStream stream = null;
        try {
            stream = atomic.startWrite(); stream.write(data); atomic.finishWrite(stream); stream = null;
        } finally { if (stream != null) atomic.failWrite(stream); }
        return target;
    }
    static File prepareUi(Context context) throws IOException {
        byte[] manifest = read(context.getAssets().open("alliance-ui-manifest.json"), 1024 * 1024);
        if (!sha256(manifest).equals(UI_SHA256)) throw new IOException("The bundled interface manifest is invalid.");
        File directory = child(context.getFilesDir().getCanonicalFile(), "alliance-ui-" + UI_SHA256);
        if (!directory.isDirectory() && !directory.mkdir()) throw new IOException("Interface storage is unavailable.");
        try {
            JSONObject index = new JSONObject(new String(manifest, java.nio.charset.StandardCharsets.UTF_8));
            JSONArray entries = index.getJSONArray("files");
            if (index.getInt("format") != 1 || entries.length() > 512) throw new IOException("Invalid interface manifest.");
            for (int i = 0; i < entries.length(); i++) {
                JSONObject entry = entries.getJSONObject(i);
                String path = entry.getString("path");
                if (!path.matches("[a-z0-9_-]+(?:/[a-z0-9_.-]+)+") || path.contains("/../") || path.contains("/./"))
                    throw new IOException("Invalid interface path.");
                int size = entry.getInt("bytes");
                if (size <= 0 || size > 8 * 1024 * 1024) throw new IOException("Invalid interface size.");
                byte[] data = read(context.getAssets().open("alliance-ui/" + path), size);
                if (data.length != size || !sha256(data).equals(entry.getString("sha256"))) throw new IOException("Invalid interface asset.");
                File target = child(directory, path);
                if (target.exists()) {
                    if (!Arrays.equals(read(new FileInputStream(target), size), data)) throw new IOException("Owned interface asset changed.");
                    continue;
                }
                File parent = target.getParentFile();
                if (!parent.isDirectory() && !parent.mkdirs()) throw new IOException("Cannot create interface storage.");
                child(directory, path);
                AtomicFile atomic = new AtomicFile(target); FileOutputStream output = null;
                try { output = atomic.startWrite(); output.write(data); atomic.finishWrite(output); output = null; }
                finally { if (output != null) atomic.failWrite(output); }
            }
            return directory;
        } catch (org.json.JSONException error) { throw new IOException("Invalid bundled interface metadata."); }
    }
}
