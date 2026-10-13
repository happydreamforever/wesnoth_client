package org.wesnoth.offline;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.os.SystemClock;
import android.system.ErrnoException;
import android.system.Os;
import android.system.OsConstants;
import android.system.StructStat;
import android.util.Log;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.util.Locale;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Offline asset preparation only: no download URLs or network bootstrap. */
public final class OfflineInitActivity extends Activity {
    static final String TAG = "WesnothOffline";
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private TextView status;
    private ProgressBar progress;
    private Button retry;
    private String runId;
    private boolean working;
    private volatile boolean destroyed;
    private long lastProgress;

    static String runId(Intent intent) {
        String value = intent.getStringExtra("wesnoth_run_id");
        return value != null && value.matches("[A-Za-z0-9_-]{1,80}") ? value : UUID.randomUUID().toString().replace("-", "");
    }
    static final OfflineDataInstaller.FileGuard FILE_GUARD = new OfflineDataInstaller.FileGuard() {
        public boolean exists(File file) throws IOException {
            try { Os.lstat(file.getAbsolutePath()); return true; }
            catch (ErrnoException error) {
                if (error.errno == OsConstants.ENOENT) return false;
                throw new IOException("Cannot inspect application storage: " + file.getName(), error);
            }
        }
        public void check(File file) throws IOException {
            try {
                StructStat info = Os.lstat(file.getAbsolutePath());
                if (!OsConstants.S_ISREG(info.st_mode) && !OsConstants.S_ISDIR(info.st_mode)) {
                    throw new IOException("Application storage contains a link or special file: " + file.getName());
                }
            } catch (ErrnoException error) { throw new IOException("Cannot inspect application storage: " + file.getName(), error); }
        }
    };

    @Override protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        runId = runId(getIntent());
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setGravity(Gravity.CENTER);
        int padding = (int) (32 * getResources().getDisplayMetrics().density);
        layout.setPadding(padding, padding, padding, padding);
        TextView title = new TextView(this);
        title.setText("Wesnoth Offline");
        title.setTextSize(28);
        title.setGravity(Gravity.CENTER);
        layout.addView(title);
        status = new TextView(this);
        status.setText("Preparing game data…");
        status.setTextSize(18);
        status.setGravity(Gravity.CENTER);
        status.setPadding(0, padding / 2, 0, padding / 2);
        layout.addView(status);
        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setMax(1000);
        layout.addView(progress, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));
        retry = new Button(this);
        retry.setText("Try again");
        retry.setVisibility(View.GONE);
        retry.setOnClickListener(view -> begin());
        layout.addView(retry);
        setContentView(layout);
        begin();
    }

    private void begin() {
        if (working || destroyed) return;
        working = true;
        retry.setVisibility(View.GONE);
        status.setText("Checking game data…");
        progress.setIndeterminate(true);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        lastProgress = 0;
        worker.execute(() -> {
            try {
                OfflineDataInstaller.Manifest manifest;
                try (InputStream input = getAssets().open("gamedata-files.jsonl")) {
                    manifest = OfflineDataInstaller.readManifest(input, OfflineDataInstaller.MANIFEST_SHA256,
                            OfflineDataInstaller.FILE_COUNT, OfflineDataInstaller.DATA_BYTES);
                }
                File base = getExternalFilesDir(null);
                File obbDir = getObbDir();
                if (base == null || obbDir == null) throw new IOException("Application storage is unavailable.");
                OfflineDataInstaller.install(base, new File(obbDir, OfflineDataInstaller.OBB_NAME), manifest, FILE_GUARD,
                        (phase, files, totalFiles, bytes, totalBytes) -> showProgress(phase, files, totalFiles, bytes, totalBytes));
                Log.i(TAG, "WESNOTH_GAME_DATA_READY run_id=" + runId + " files=" + manifest.count() + " bytes=" + manifest.bytes);
                runOnUiThread(() -> {
                    if (destroyed || isFinishing()) return;
                    working = false;
                    status.setText("Starting Wesnoth…");
                    getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                    Intent game = new Intent(this, OfflineGameActivity.class);
                    game.putExtra("wesnoth_run_id", runId);
                    Log.i(TAG, "WESNOTH_GAME_LAUNCH run_id=" + runId + " version=1.19.26");
                    startActivity(game);
                    finish();
                });
            } catch (Exception error) {
                Log.e(TAG, "WESNOTH_GAME FAIL run_id=" + runId + " message=" + error.getMessage(), error);
                runOnUiThread(() -> {
                    if (destroyed || isFinishing()) return;
                    working = false;
                    progress.setIndeterminate(false);
                    progress.setProgress(0);
                    status.setText("Game data could not be prepared. Check that the supplied game-data file is installed, then try again. Your saved games have been kept.");
                    retry.setVisibility(View.VISIBLE);
                    getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                });
            }
        });
    }
    private void showProgress(String phase, int files, int totalFiles, long bytes, long totalBytes) {
        long now = SystemClock.elapsedRealtime();
        if (files != totalFiles && now - lastProgress < 500) return;
        lastProgress = now;
        int value = totalBytes == 0 ? 0 : (int) Math.min(1000, bytes * 1000 / totalBytes);
        Log.i(TAG, "WESNOTH_GAME_DATA_PROGRESS run_id=" + runId + " phase=" + phase + " files=" + files + "/" + totalFiles);
        runOnUiThread(() -> {
            if (destroyed || isFinishing()) return;
            progress.setIndeterminate(false);
            progress.setProgress(value);
            status.setText(phase + "\n" + String.format(Locale.getDefault(), "%,d / %,d files", files, totalFiles));
        });
    }
    @Override protected void onDestroy() {
        destroyed = true;
        worker.shutdownNow();
        super.onDestroy();
    }
}
