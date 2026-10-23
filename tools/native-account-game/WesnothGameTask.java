package org.wesnoth.offline;

import android.app.Activity;
import android.app.ActivityManager;
import android.content.Context;
import android.os.Looper;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/** Close only this game's task before native exit; saved files are never touched. */
public final class WesnothGameTask {
    static final long WAIT_MILLIS = 1500;
    private WesnothGameTask() {}

    private static void close(Activity activity) {
        int taskId = activity.getTaskId();
        ActivityManager manager = (ActivityManager) activity.getSystemService(Context.ACTIVITY_SERVICE);
        if (manager != null) {
            for (ActivityManager.AppTask task : manager.getAppTasks()) {
                if (task.getTaskInfo().id == taskId) {
                    // Game activity is normally above AccountSetupActivity, not the task root.
                    // AppTask removal closes both; Activity.finish() alone returns to setup.
                    task.finishAndRemoveTask();
                    return;
                }
            }
        }
        // A root task may disappear concurrently with an Android lifecycle transition.
        activity.finishAffinity();
        activity.finishAndRemoveTask();
    }

    public static boolean finish(Activity activity) {
        if (Looper.myLooper() == Looper.getMainLooper()) {
            try { close(activity); return true; }
            catch (RuntimeException error) { return false; }
        }
        CountDownLatch finished = new CountDownLatch(1);
        AtomicBoolean success = new AtomicBoolean(false);
        try {
            activity.runOnUiThread(() -> {
                try { close(activity); success.set(true); }
                catch (RuntimeException error) { /* No arbitrary exception data enters logs. */ }
                finally { finished.countDown(); }
            });
            return finished.await(WAIT_MILLIS, TimeUnit.MILLISECONDS) && success.get();
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            return false;
        } catch (RuntimeException error) {
            return false;
        }
    }
}
