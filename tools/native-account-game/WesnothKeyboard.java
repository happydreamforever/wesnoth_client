package org.libsdl.app;

import android.text.InputType;
import android.view.InputDevice;
import android.view.KeyEvent;

/** Small adapter used only by the owned game APK's pinned SDL Java overlay. */
public final class WesnothKeyboard {
    private static final WesnothKeyboardState STATE = new WesnothKeyboardState();
    private static int nativeHeld;
    private WesnothKeyboard() {}

    public static int inputType(int requested) {
        if ((requested & InputType.TYPE_MASK_CLASS) != InputType.TYPE_CLASS_TEXT) return requested;
        return (requested & ~(InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS |
                InputType.TYPE_TEXT_FLAG_CAP_WORDS | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES |
                InputType.TYPE_TEXT_FLAG_AUTO_CORRECT | InputType.TYPE_TEXT_FLAG_AUTO_COMPLETE)) |
                InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS;
    }
    public static KeyEvent event(KeyEvent event) {
        int source = event.getSource();
        if (source == InputDevice.SOURCE_UNKNOWN) {
            InputDevice device = InputDevice.getDevice(event.getDeviceId());
            if (device != null) source = device.getSources();
        }
        if ((source & InputDevice.SOURCE_KEYBOARD) != InputDevice.SOURCE_KEYBOARD ||
                (event.getAction() != KeyEvent.ACTION_DOWN && event.getAction() != KeyEvent.ACTION_UP)) return event;
        int meta = STATE.event(event.getDeviceId(), event.getKeyCode(), event.getAction(),
                event.getRepeatCount(), event.getMetaState());
        reconcile(event.getKeyCode());
        if (meta == event.getMetaState()) return event;
        // Preserve intentional Shift/CapsLock, key layout, timing, repeats, symbols and device identity.
        // Only metadata made stale by a key-up or an explicit user reset changes.
        return new KeyEvent(event.getDownTime(), event.getEventTime(), event.getAction(), event.getKeyCode(),
                event.getRepeatCount(), meta, event.getDeviceId(), event.getScanCode(), event.getFlags(), event.getSource());
    }
    private static void reconcile(int currentKey) {
        int desired = STATE.held();
        for (int i = 0; i < WesnothKeyboardState.KEYS.length; ++i) {
            int bit = 1 << i, key = WesnothKeyboardState.KEYS[i];
            if (((nativeHeld ^ desired) & bit) != 0 && key != currentKey) {
                if ((desired & bit) != 0) SDLActivity.onNativeKeyDown(key);
                else SDLActivity.onNativeKeyUp(key);
            }
        }
        nativeHeld = desired;
    }
    public static void focusLost() {
        STATE.focusLost();
        // Release all supported native modifiers, including events missed before this adapter saw them.
        for (int key : WesnothKeyboardState.KEYS) SDLActivity.onNativeKeyUp(key);
        nativeHeld = 0;
    }
    public static void reset() { STATE.reset(); focusLost(); }
}
