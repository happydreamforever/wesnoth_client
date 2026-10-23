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
    private static boolean keyboard(KeyEvent event) {
        int source = event.getSource();
        if (source == InputDevice.SOURCE_UNKNOWN) {
            InputDevice device = InputDevice.getDevice(event.getDeviceId());
            if (device != null) source = device.getSources();
        }
        return (source & InputDevice.SOURCE_KEYBOARD) == InputDevice.SOURCE_KEYBOARD;
    }
    public static KeyEvent event(KeyEvent event) {
        if (!keyboard(event) || (event.getAction() != KeyEvent.ACTION_DOWN &&
                event.getAction() != KeyEvent.ACTION_UP)) return event;
        int meta = STATE.event(event.getDeviceId(), event.getKeyCode(), event.getAction(),
                event.getRepeatCount(), event.getMetaState());
        reconcile(event.getKeyCode());
        if (meta == event.getMetaState()) return event;
        // Preserve intentional Shift/CapsLock, key layout, timing, repeats, symbols and device identity.
        // Only metadata made stale by a key-up or an explicit user reset changes.
        return new KeyEvent(event.getDownTime(), event.getEventTime(), event.getAction(), event.getKeyCode(),
                event.getRepeatCount(), meta, event.getDeviceId(), event.getScanCode(), event.getFlags(), event.getSource());
    }
    /** Called after SDL forwards the current key; another keyboard may still hold that modifier. */
    public static void delivered(KeyEvent event) {
        if (!keyboard(event)) return;
        for (int i = 0; i < WesnothKeyboardState.KEYS.length; ++i) {
            if (event.getKeyCode() != WesnothKeyboardState.KEYS[i]) continue;
            if (event.getAction() == KeyEvent.ACTION_DOWN) nativeHeld |= 1 << i;
            else if (event.getAction() == KeyEvent.ACTION_UP) nativeHeld &= ~(1 << i);
        }
        reconcile(-1);
    }
    private static void reconcile(int currentKey) {
        int desired = STATE.held();
        for (int i = 0; i < WesnothKeyboardState.KEYS.length; ++i) {
            int bit = 1 << i, key = WesnothKeyboardState.KEYS[i];
            if (((nativeHeld ^ desired) & bit) != 0 && key != currentKey) {
                if ((desired & bit) != 0) SDLActivity.onNativeKeyDown(key);
                else SDLActivity.onNativeKeyUp(key);
                nativeHeld = (nativeHeld & ~bit) | (desired & bit);
            }
        }
    }
    public static void focusLost() {
        STATE.focusLost();
        // Release all supported native modifiers, including events missed before this adapter saw them.
        for (int key : WesnothKeyboardState.KEYS) SDLActivity.onNativeKeyUp(key);
        nativeHeld = 0;
    }
    public static void reset() { STATE.reset(); focusLost(); }
}
