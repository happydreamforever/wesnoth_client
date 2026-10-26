package org.wesnoth.offline;

/** Phase byte counts are real work, not a timer. Completion requires publication. */
final class StartupProgress {
    private StartupProgress() {}
    static int percent(long bytes, long total, boolean complete) {
        if (complete) return 100;
        if (bytes < 0 || total <= 0) return 0;
        if (bytes >= total) return 99;
        return (int) Math.min(99, (bytes / (double) total) * 100);
    }
}
