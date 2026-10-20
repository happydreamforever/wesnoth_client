package org.wesnoth.offline;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.util.AtomicFile;
import android.util.Log;
import android.view.Gravity;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Explicit public-profile import. No credentials, database addresses or private keys are imported. */
public final class AccountSetupActivity extends Activity {
    private static final int PICK_PROFILE = 81;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private volatile boolean destroyed;
    private TextView status;
    private Button importButton;
    private String runId;

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        runId = OfflineInitActivity.runId(getIntent());
        ScrollView scroll = new ScrollView(this);
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL); layout.setGravity(Gravity.CENTER_HORIZONTAL);
        int padding = (int) (24 * getResources().getDisplayMetrics().density);
        layout.setPadding(padding, padding, padding, padding); scroll.addView(layout);
        TextView title = new TextView(this); title.setText("Wesnoth Alliance"); title.setTextSize(26); layout.addView(title);
        status = new TextView(this); status.setTextSize(17); status.setPadding(0, padding, 0, padding); layout.addView(status);
        importButton = new Button(this); importButton.setText("Import server profile");
        importButton.setOnClickListener(view -> {
            Intent picker = new Intent(Intent.ACTION_OPEN_DOCUMENT);
            picker.addCategory(Intent.CATEGORY_OPENABLE); picker.setType("*/*");
            try { startActivityForResult(picker, PICK_PROFILE); }
            catch (Exception error) { showError("No file picker is available. Keep the public profile in Downloads and try again."); }
        }); layout.addView(importButton);
        Button start = new Button(this); start.setText("Start game");
        start.setOnClickListener(view -> {
            Intent game = new Intent(this, OfflineInitActivity.class);
            game.putExtra("wesnoth_run_id", runId); startActivity(game);
        }); layout.addView(start);
        TextView help = new TextView(this);
        help.setText("Import the public-profile.json supplied by your Windows server. Check the address and fingerprint with its administrator before trusting it. In the game menu, choose Alliance to create or use your player account.");
        help.setPadding(0, padding, 0, 0); layout.addView(help);
        setContentView(scroll); showCurrent();
        Log.i(OfflineInitActivity.TAG, "WESNOTH_ACCOUNT_SETUP_READY run_id=" + runId);
    }
    private File profileFile() { return new File(getFilesDir(), "alliance-public-profile.json"); }
    private static byte[] readBounded(InputStream input) throws IOException {
        if (input == null) throw new IOException("Cannot open profile.");
        ByteArrayOutputStream bytes = new ByteArrayOutputStream(); byte[] buffer = new byte[4096]; int read;
        while ((read = input.read(buffer)) != -1) {
            if (bytes.size() + read > AlliancePublicProfile.MAX_BYTES) throw new IOException("Profile is larger than 32 KiB.");
            bytes.write(buffer, 0, read);
        }
        return bytes.toByteArray();
    }
    private void showCurrent() {
        File file = profileFile();
        if (!file.exists()) { status.setText("No server profile imported. Local game and guest multiplayer remain available."); return; }
        try (InputStream input = new AtomicFile(file).openRead()) {
            AlliancePublicProfile profile = AlliancePublicProfile.parse(readBounded(input));
            status.setText("Trusted server: " + profile.address + ":" + profile.port + "\nSHA-256: " + profile.fingerprint);
        } catch (Exception error) { status.setText("The saved server profile cannot be used. Import a valid public profile to use Alliance accounts."); }
    }
    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request != PICK_PROFILE || result != RESULT_OK || data == null || data.getData() == null) return;
        Uri uri = data.getData();
        if (!"content".equals(uri.getScheme())) { showError("Select the public profile using the file picker."); return; }
        importButton.setEnabled(false); status.setText("Checking public profile…");
        worker.execute(() -> {
            try (InputStream input = getContentResolver().openInputStream(uri)) {
                AlliancePublicProfile profile = AlliancePublicProfile.parse(readBounded(input));
                runOnUiThread(() -> {
                    if (destroyed || isFinishing()) return;
                    importButton.setEnabled(true); showCurrent();
                    new AlertDialog.Builder(this).setTitle("Trust this Alliance server?")
                        .setMessage("Address: " + profile.address + ":" + profile.port + "\n\nCertificate SHA-256:\n" + profile.fingerprint +
                            "\n\nConfirm these values match your Windows server. This replaces this app's saved Alliance server profile.")
                        .setNegativeButton("Cancel", (dialog, which) -> showCurrent())
                        .setPositiveButton("Trust server", (dialog, which) -> save(profile)).show();
                });
            } catch (Exception error) {
                runOnUiThread(() -> {
                    if (destroyed || isFinishing()) return;
                    importButton.setEnabled(true); showCurrent(); showError("The selected public profile is invalid, expired or unreadable. The previous profile was kept.");
                });
            }
        });
    }
    private void save(AlliancePublicProfile profile) {
        AtomicFile target = new AtomicFile(profileFile()); FileOutputStream stream = null;
        try {
            stream = target.startWrite(); stream.write(profile.bytes); target.finishWrite(stream); stream = null;
            showCurrent();
            Log.i(OfflineInitActivity.TAG, "WESNOTH_ACCOUNT_PROFILE_IMPORTED");
        } catch (IOException error) {
            if (stream != null) target.failWrite(stream);
            showError("The server profile could not be saved. Please try again.");
        }
    }
    private void showError(String message) {
        if (!destroyed && !isFinishing()) new AlertDialog.Builder(this).setTitle("Alliance server profile").setMessage(message).setPositiveButton("OK", null).show();
    }
    @Override protected void onDestroy() { destroyed = true; worker.shutdownNow(); super.onDestroy(); }
}
