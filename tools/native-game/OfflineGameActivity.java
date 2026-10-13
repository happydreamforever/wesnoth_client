package org.wesnoth.offline;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ActivityInfo;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.BatteryManager;
import android.os.Build;
import android.os.Bundle;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import android.util.DisplayMetrics;
import android.util.Log;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.webkit.MimeTypeMap;
import android.widget.Toast;
import java.io.File;
import java.io.FileNotFoundException;
import java.io.IOException;
import java.util.Locale;
import org.libsdl.app.SDLActivity;

/** SDLActivity host for the actual shared Wesnoth engine. */
public final class OfflineGameActivity extends SDLActivity {
    private String runId;

    @Override protected String[] getLibraries() {
        return new String[] { "c++_shared", "SDL3", "SDL3_image", "SDL3_mixer", "main" };
    }
    @Override protected void onCreate(Bundle savedInstanceState) {
        runId = OfflineInitActivity.runId(getIntent());
        super.onCreate(savedInstanceState);
        if (mBrokenLibraries) {
            Log.e(OfflineInitActivity.TAG, "WESNOTH_GAME FAIL run_id=" + runId + " message=Native game libraries could not be loaded.");
        } else {
            // SDL window/activity readiness does not establish a rendered title screen.
            Log.i(OfflineInitActivity.TAG, "WESNOTH_GAME_ACTIVITY_READY run_id=" + runId);
        }
    }
    @Override protected String[] getArguments() {
        File base = getExternalFilesDir(null);
        if (base == null) throw new IllegalStateException("Application game storage is unavailable.");
        DisplayMetrics metrics = new DisplayMetrics();
        getWindowManager().getDefaultDisplay().getRealMetrics(metrics);
        int width = Math.max(metrics.widthPixels, metrics.heightPixels);
        int height = Math.min(metrics.widthPixels, metrics.heightPixels);
        try {
            return new String[] {
                "--data-dir", new File(base, "gamedata").getCanonicalPath(),
                "--userdata-dir", new File(base, "userdata").getCanonicalPath(),
                "--usercache-dir", new File(getCacheDir(), "wesnoth").getCanonicalPath(),
                "--resolution", width + "x" + height,
                "--log-to-file"
            };
        } catch (IOException error) { throw new IllegalStateException("Cannot resolve game storage.", error); }
    }
    @Override public void setOrientationBis(int width, int height, boolean resizable, String hint) {
        setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE);
    }
    @Override public void onWindowFocusChanged(boolean focused) {
        super.onWindowFocusChanged(focused);
        if (!focused || mBrokenLibraries) return;
        if (Build.VERSION.SDK_INT >= 30) {
            getWindow().setDecorFitsSystemWindows(false);
            WindowInsetsController controller = getWindow().getInsetsController();
            if (controller != null) {
                controller.hide(WindowInsets.Type.statusBars() | WindowInsets.Type.navigationBars());
                controller.setSystemBarsBehavior(WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
            }
        } else {
            getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_FULLSCREEN |
                    View.SYSTEM_UI_FLAG_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY |
                    View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION |
                    View.SYSTEM_UI_FLAG_LAYOUT_STABLE);
        }
    }

    // These method names/signatures are called through JNI by the preserved engine.
    public String getLocaleCode() { return Locale.getDefault().toString(); }
    public double getBatteryPercentage() {
        BatteryManager manager = (BatteryManager) getSystemService(BATTERY_SERVICE);
        return manager == null ? -1.0 : manager.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY);
    }
    public void open(String address) {
        runOnUiThread(() -> {
            try {
                Uri uri = Uri.parse(address);
                Intent intent = new Intent(Intent.ACTION_VIEW);
                if ("http".equalsIgnoreCase(uri.getScheme()) || "https".equalsIgnoreCase(uri.getScheme())) {
                    intent.setData(uri);
                } else {
                    if (uri.getScheme() != null && !"file".equalsIgnoreCase(uri.getScheme())) throw new IOException("Unsupported address type.");
                    File file = LocalFileProvider.allowedFile(this, uri.getScheme() == null ? address : uri.getPath());
                    Uri shared = new Uri.Builder().scheme("content").authority(getPackageName() + ".files")
                            .appendPath("open").appendQueryParameter("path", file.getCanonicalPath()).build();
                    intent.setDataAndType(shared, LocalFileProvider.mime(file));
                    intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                }
                startActivity(intent);
            } catch (Exception error) {
                Log.w(OfflineInitActivity.TAG, "Could not open requested game link or file.", error);
                Toast.makeText(this, "No application could open this item.", Toast.LENGTH_LONG).show();
            }
        });
    }

    /** Read-only, individually granted access to files inside this application's storage. */
    public static final class LocalFileProvider extends ContentProvider {
        @Override public boolean onCreate() { return true; }
        static File allowedFile(Context context, String path) throws IOException {
            if (path == null) throw new FileNotFoundException("Missing game file path.");
            File file = new File(path).getCanonicalFile();
            File[] roots = { context.getExternalFilesDir(null), context.getFilesDir() };
            for (File root : roots) {
                if (root == null) continue;
                String base = root.getCanonicalPath() + File.separator;
                if (file.getPath().startsWith(base) && file.isFile()) return file;
            }
            throw new FileNotFoundException("File is outside this application's storage.");
        }
        private File resolve(Uri uri) throws FileNotFoundException {
            try {
                if (!"content".equals(uri.getScheme()) || !(getContext().getPackageName() + ".files").equals(uri.getAuthority()) || !"/open".equals(uri.getPath())) throw new IOException("Invalid game-file URI.");
                return allowedFile(getContext(), uri.getQueryParameter("path"));
            } catch (IOException error) { throw new FileNotFoundException(error.getMessage()); }
        }
        static String mime(File file) {
            String name = file.getName();
            int dot = name.lastIndexOf('.');
            String type = dot < 0 ? null : MimeTypeMap.getSingleton().getMimeTypeFromExtension(name.substring(dot + 1).toLowerCase(Locale.ROOT));
            return type == null ? "application/octet-stream" : type;
        }
        @Override public String getType(Uri uri) {
            try { return mime(resolve(uri)); }
            catch (FileNotFoundException error) { return "application/octet-stream"; }
        }
        @Override public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
            if (!"r".equals(mode)) throw new FileNotFoundException("Only read access is supported.");
            return ParcelFileDescriptor.open(resolve(uri), ParcelFileDescriptor.MODE_READ_ONLY);
        }
        @Override public Cursor query(Uri uri, String[] projection, String selection, String[] selectionArgs, String sortOrder) {
            try {
                File file = resolve(uri);
                String[] columns = projection == null ? new String[] {OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE} : projection;
                MatrixCursor cursor = new MatrixCursor(columns, 1);
                Object[] values = new Object[columns.length];
                for (int index = 0; index < columns.length; index++) {
                    if (OpenableColumns.DISPLAY_NAME.equals(columns[index])) values[index] = file.getName();
                    else if (OpenableColumns.SIZE.equals(columns[index])) values[index] = file.length();
                }
                cursor.addRow(values);
                return cursor;
            } catch (FileNotFoundException error) { return null; }
        }
        @Override public Uri insert(Uri uri, ContentValues values) { throw new UnsupportedOperationException("Read-only game files."); }
        @Override public int update(Uri uri, ContentValues values, String selection, String[] selectionArgs) { throw new UnsupportedOperationException("Read-only game files."); }
        @Override public int delete(Uri uri, String selection, String[] selectionArgs) { throw new UnsupportedOperationException("Read-only game files."); }
    }
}
