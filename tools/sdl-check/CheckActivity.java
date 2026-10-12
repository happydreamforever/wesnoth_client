package org.wesnoth.offlinecheck;

import org.libsdl.app.SDLActivity;

/** Isolated SDL graphics/input checkpoint using the selected upstream Java glue. */
public final class CheckActivity extends SDLActivity {
    @Override
    protected String[] getLibraries() {
        return new String[] { "c++_shared", "SDL3", "main" };
    }

    @Override
    protected String[] getArguments() {
        String runId = getIntent().getStringExtra("wesnoth_run_id");
        return new String[] { "--run-id", runId == null ? "manual" : runId };
    }
}
