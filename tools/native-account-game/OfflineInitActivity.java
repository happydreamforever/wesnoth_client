package org.wesnoth.offline;

import android.app.Activity;
import android.content.Intent;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
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
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
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
    private Button close;
    private String runId;
    private boolean working;
    private volatile boolean destroyed;
    private long lastProgress;
    private String lastPhase = "";

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
        FrameLayout screen = new FrameLayout(this);
        screen.setBackgroundColor(ProductTheme.TEAL_SHADOW);
        ImageView background = new ImageView(this);
        background.setScaleType(ImageView.ScaleType.CENTER_CROP);
        loadArt(background, ProductTheme.BACKGROUND_ASSET);
        screen.addView(background, new FrameLayout.LayoutParams(-1, -1));
        View shade = new View(this);
        shade.setBackground(new GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,
                new int[] {withAlpha(ProductTheme.TEAL_SHADOW, 40), withAlpha(ProductTheme.INK, 145)}));
        screen.addView(shade, new FrameLayout.LayoutParams(-1, -1));
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setGravity(Gravity.CENTER);
        float density = getResources().getDisplayMetrics().density;
        int padding = (int) (32 * density);
        layout.setPadding(padding, padding / 2, padding, padding / 2);
        Typeface script = Typeface.createFromAsset(getAssets(), ProductTheme.TITLE_FONT_ASSET);
        Typeface body = Typeface.createFromAsset(getAssets(), ProductTheme.BODY_FONT_ASSET);
        float titleSize = Math.min(68, (getResources().getDisplayMetrics().widthPixels / density - 80) / 7);
        TextView kicker = titleLine(ProductTheme.TITLE_KICKER, titleSize * .42f, script, ProductTheme.IVORY);
        layout.addView(kicker, new LinearLayout.LayoutParams(-1, -2));
        TextView title = titleLine(ProductTheme.TITLE_NAME, titleSize, script, ProductTheme.GOLD);
        layout.addView(title, new LinearLayout.LayoutParams(-1, -2));
        LinearLayout preparation = new LinearLayout(this);
        preparation.setOrientation(LinearLayout.VERTICAL);
        preparation.setGravity(Gravity.CENTER);
        preparation.setPadding(padding, padding / 2, padding, padding);
        preparation.setBackground(new StartupFrame(density, false, true, false));
        LinearLayout.LayoutParams preparationBounds = new LinearLayout.LayoutParams(-1, -2);
        preparationBounds.topMargin = padding / 2;
        layout.addView(preparation, preparationBounds);
        status = new TextView(this);
        status.setTypeface(body);
        status.setText("0%");
        status.setTextSize(18);
        status.setTextColor(ProductTheme.IVORY);
        status.setShadowLayer(3, 0, 1, Color.BLACK);
        status.setGravity(Gravity.CENTER);
        status.setPadding(0, padding / 2, 0, padding / 2);
        preparation.addView(status);
        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setMax(100);
        progress.setProgressDrawable(new StartupFrame(density, false, false, true));
        preparation.addView(progress, new LinearLayout.LayoutParams(-1, (int) (28 * density)));
        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(LinearLayout.HORIZONTAL);
        preparation.addView(actions, new LinearLayout.LayoutParams(-1, -2));
        retry = new Button(this);
        styleAction(retry, body, true);
        retry.setText("Retry");
        retry.setVisibility(View.GONE);
        retry.setOnClickListener(view -> begin());
        LinearLayout.LayoutParams retryBounds = new LinearLayout.LayoutParams(0, (int) (52 * density), 1);
        retryBounds.topMargin = padding / 2; retryBounds.rightMargin = padding / 4;
        actions.addView(retry, retryBounds);
        close = new Button(this);
        styleAction(close, body, false);
        close.setText("Close");
        close.setVisibility(View.GONE);
        close.setOnClickListener(view -> WesnothGameTask.finish(this));
        LinearLayout.LayoutParams closeBounds = new LinearLayout.LayoutParams(0, (int) (52 * density), 1);
        closeBounds.topMargin = padding / 2; closeBounds.leftMargin = padding / 4;
        actions.addView(close, closeBounds);
        // A small display or enlarged text may scroll the error actions into view.
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        LinearLayout centered = new LinearLayout(this);
        centered.setGravity(Gravity.CENTER);
        centered.setOrientation(LinearLayout.VERTICAL);
        centered.addView(layout, new LinearLayout.LayoutParams(
                Math.min(getResources().getDisplayMetrics().widthPixels, (int) (720 * density)), -2));
        scroll.addView(centered, new ScrollView.LayoutParams(-1, -2));
        screen.addView(scroll, new FrameLayout.LayoutParams(-1, -1));
        setContentView(screen);
        Log.i(TAG, "WESNOTH_STARTUP_READY run_id=" + runId);
        begin();
    }

    private static int withAlpha(int color, int alpha) {
        return (color & 0x00ffffff) | (alpha << 24);
    }

    private TextView titleLine(String text, float size, Typeface script, int color) {
        TextView title = new TextView(this);
        title.setText(text);
        title.setTypeface(script);
        title.setTextSize(size);
        title.setTextColor(color);
        title.setShadowLayer(4, 0, 2, ProductTheme.INK);
        title.setGravity(Gravity.CENTER);
        title.setIncludeFontPadding(true);
        return title;
    }

    private void styleAction(Button button, Typeface body, boolean primary) {
        float density = getResources().getDisplayMetrics().density;
        button.setBackground(new StartupFrame(density, primary, false, false));
        button.setTextColor(primary ? ProductTheme.STONE : ProductTheme.IVORY);
        button.setTypeface(body);
        button.setTextSize(18);
        button.setMinHeight((int) (48 * density));
    }

    private void loadArt(ImageView target, String asset) {
        try (InputStream source = getAssets().open(asset)) {
            BitmapFactory.Options options = new BitmapFactory.Options();
            options.inPreferredConfig = android.graphics.Bitmap.Config.ARGB_8888;
            target.setImageBitmap(BitmapFactory.decodeStream(source, null, options));
        } catch (IOException error) { Log.e(TAG, "WESNOTH_STARTUP_ART_UNAVAILABLE"); }
    }

    private void begin() {
        if (working || destroyed) return;
        working = true;
        retry.setVisibility(View.GONE);
        close.setVisibility(View.GONE);
        status.setText("0%");
        progress.setIndeterminate(false);
        progress.setProgress(0);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        lastProgress = 0;
        lastPhase = "";
        worker.execute(() -> {
            String stage = "server configuration";
            try {
                BundledStartup.prepareProfile(this);
                BundledStartup.prepareUi(this);
                stage = "game data";
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
                    progress.setIndeterminate(false);
                    progress.setProgress(StartupProgress.percent(manifest.bytes, manifest.bytes, true));
                    status.setText("100%");
                    getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                    Intent game = new Intent(this, OfflineGameActivity.class);
                    game.putExtra("wesnoth_run_id", runId);
                    Log.i(TAG, "WESNOTH_GAME_LAUNCH run_id=" + runId + " version=1.19.26");
                    startActivity(game);
                    finish();
                });
            } catch (Exception error) {
                final boolean configurationFailed = stage.equals("server configuration");
                Log.e(TAG, "WESNOTH_GAME FAIL run_id=" + runId +
                        (configurationFailed ? " bundled_configuration_failed" : " game_data_preparation_failed"));
                runOnUiThread(() -> {
                    if (destroyed || isFinishing()) return;
                    working = false;
                    progress.setIndeterminate(false);
                    progress.setProgress(0);
                    status.setText(configurationFailed
                            ? "Server configuration is unavailable. Contact the server administrator."
                            : "Game data is unavailable. Check the supplied data file and retry.");
                    retry.setVisibility(View.VISIBLE);
                    close.setVisibility(View.VISIBLE);
                    getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                });
            }
        });
    }
    private void showProgress(String phase, int files, int totalFiles, long bytes, long totalBytes) {
        long now = SystemClock.elapsedRealtime();
        if (phase.equals(lastPhase) && files != totalFiles && now - lastProgress < 150) return;
        lastProgress = now;
        lastPhase = phase;
        int value = StartupProgress.percent(bytes, totalBytes, false);
        Log.i(TAG, "WESNOTH_GAME_DATA_PROGRESS run_id=" + runId + " phase=" + phase + " files=" + files + "/" + totalFiles);
        runOnUiThread(() -> {
            if (destroyed || isFinishing()) return;
            progress.setIndeterminate(false);
            progress.setProgress(value);
            status.setText(value + "%");
        });
    }
    @Override protected void onDestroy() {
        destroyed = true;
        worker.shutdownNow();
        super.onDestroy();
    }
}
